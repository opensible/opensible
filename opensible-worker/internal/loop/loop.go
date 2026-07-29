// Package loop is the worker main-loop: heartbeat, system info, claim, dispatch.
// Mirrors worker/loop.py.
package loop

import (
	"errors"
	"math"
	"os"
	"os/signal"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/opensible/worker-go/internal/execute"
	"github.com/opensible/worker-go/internal/httpclient"
	"github.com/opensible/worker-go/internal/logging"
	"github.com/opensible/worker-go/internal/systeminfo"
)

// Options configures the worker loop.
type Options struct {
	ServerURL      string
	PollInterval   int
	ProjectID      string
	WorkerName     string
	MaxConcurrency int
	Tags           []string
	Capabilities   map[string]any
}

var shutdown atomic.Bool

// Run enters the main worker loop.
func Run(opts Options) {
	log := logging.L()
	if opts.WorkerName == "" {
		if h, err := os.Hostname(); err == nil {
			opts.WorkerName = h
		} else {
			opts.WorkerName = "worker"
		}
	}
	if opts.PollInterval <= 0 {
		opts.PollInterval = 3
	}
	if opts.MaxConcurrency <= 0 {
		opts.MaxConcurrency = 1
	}

	client := httpclient.New(opts.ServerURL)
	if !client.WaitUntilReady(90 * time.Second) {
		log.Warn("Backend health check did not become ready before timeout; continuing with normal retry loop")
	}

	if client.WorkerToken == "" {
		log.Info("No token found, registering worker...")
		if _, err := client.Register(opts.WorkerName, opts.Capabilities); err != nil {
			log.Error("Failed to register worker", "err", err)
			return
		}
	}

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		s := <-sigCh
		log.Info("Received signal, shutting down", "signal", s.String())
		shutdown.Store(true)
	}()

	log.Info("Worker started, waiting for tasks...", "maxConcurrency", opts.MaxConcurrency)

	pollInterval := time.Duration(opts.PollInterval) * time.Second
	heartbeatInterval := 30 * time.Second
	sysInfoInterval := 300 * time.Second
	sysInfoRetry := 30 * time.Second
	logReloadInterval := 60 * time.Second

	var lastHeartbeat, lastSysSent, lastSysAttempt, lastLogReload time.Time
	needReregister := false
	rateLimitRetries := 0
	const maxRateLimitRetries = 5

	// Concurrency semaphore: cap in-flight executions at MaxConcurrency.
	sem := make(chan struct{}, opts.MaxConcurrency)

	// Initial system-info push (best-effort).
	if info := systeminfo.Collect(); info != nil {
		lastSysAttempt = time.Now()
		if client.SendSystemInfo(info) {
			lastSysSent = time.Now()
			log.Debug("System info sent at startup")
		}
	}

	for !shutdown.Load() {
		now := time.Now()

		// Heartbeat.
		if now.Sub(lastHeartbeat) >= heartbeatInterval {
			ok, wantSys := client.Heartbeat("")
			if ok {
				lastHeartbeat = now
				needReregister = false
			} else if !needReregister {
				log.Warn("Heartbeat failed - will re-register on next claim")
				needReregister = true
			}
			if wantSys {
				if info := systeminfo.Collect(); info != nil {
					if client.SendSystemInfo(info) {
						lastSysSent = time.Now()
					}
				}
			}
		}

		// System info periodic push.
		needSend := now.Sub(lastSysSent) >= sysInfoInterval ||
			(lastSysSent.IsZero() && now.Sub(lastSysAttempt) >= sysInfoRetry)
		if needSend {
			info := systeminfo.Collect()
			lastSysAttempt = now
			if info != nil && client.SendSystemInfo(info) {
				lastSysSent = now
			}
		}

		// Log level reload.
		if now.Sub(lastLogReload) >= logReloadInterval {
			logging.ReloadLogLevel()
			lastLogReload = now
		}

		// Only claim if we have a free slot; otherwise sleep briefly.
		select {
		case sem <- struct{}{}:
		default:
			time.Sleep(pollInterval)
			continue
		}

		// Claim.
		execData, err := client.Claim(opts.ProjectID, opts.MaxConcurrency, opts.Tags)
		if err != nil {
			<-sem // release reserved slot on claim failure
			var httpErr *httpclient.HTTPError
			isHTTP := errors.As(err, &httpErr)
			es := err.Error()
			is401 := (isHTTP && httpErr.Status == 401) || strings.Contains(strings.ToUpper(es), "UNAUTHORIZED")
			is429 := (isHTTP && httpErr.Status == 429) || strings.Contains(strings.ToUpper(es), "TOO MANY REQUESTS")

			if is401 {
				log.Warn("Claim returned 401, re-registering...")
				if _, err := client.Register(opts.WorkerName, opts.Capabilities); err != nil {
					log.Error("Failed to re-register worker", "err", err)
					needReregister = true
					time.Sleep(pollInterval)
					continue
				}
				needReregister = false
				continue
			}
			if is429 {
				if rateLimitRetries < maxRateLimitRetries {
					rateLimitRetries++
				}
				backoff := time.Duration(math.Min(60, float64(opts.PollInterval)*math.Pow(2, math.Min(5, float64(rateLimitRetries))))) * time.Second
				log.Warn("Rate limited", "attempt", rateLimitRetries, "sleep", backoff)
				time.Sleep(backoff)
				continue
			}
			rateLimitRetries = 0
			log.Error("Claim failed", "err", err)
			time.Sleep(pollInterval)
			continue
		}
		rateLimitRetries = 0
		needReregister = false

		if execData == nil {
			<-sem // nothing to run — release slot
			time.Sleep(pollInterval)
			continue
		}

		execID, _ := execData["executionId"].(string)
		projID, _ := execData["projectId"].(string)
		log.Debug("Claimed execution", "id", execID, "project", projID)
		go func(id string, data map[string]any, pid string) {
			defer func() { <-sem }()
			execute.Run(id, data, pid, client)
		}(execID, execData, projID)
	}

	log.Info("Worker stopped")
}
