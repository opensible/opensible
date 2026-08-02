// Package main is the OpenSible worker entrypoint (Go port).
package main

import (
	"flag"
	"os"
	"strconv"
	"strings"

	"github.com/opensible/worker-go/internal/config"
	"github.com/opensible/worker-go/internal/logging"
	"github.com/opensible/worker-go/internal/loop"
	"github.com/opensible/worker-go/internal/vaultserver"
)

func main() {
	defaultServer := os.Getenv("WORKER_SERVER_URL")
	if defaultServer == "" {
		defaultServer = "http://localhost:5000"
	}

	serverURL := flag.String("server-url", defaultServer, "Backend server URL")
	pollInterval := flag.Int("poll-interval", 3, "Poll interval seconds")
	projectID := flag.String("project-id", "", "Only process this project")
	workerName := flag.String("worker-name", os.Getenv("WORKER_NAME"), "Worker display name (default: hostname)")
	defaultMax := 1
	if v := os.Getenv("WORKER_MAX_CONCURRENCY"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			defaultMax = n
		}
	}
	maxConcurrency := flag.Int("max-concurrency", defaultMax, "Max concurrent runs")
	tagsRaw := flag.String("tags", os.Getenv("WORKER_TAGS"), "Comma-separated worker tags")
	runVaultServer := flag.Bool("vault-server", false, "Also start the local vault HTTP server")

	flag.Parse()


	// Initialize config + logging.
	config.Init()
	logging.Setup()
	log := logging.L()

	var tags []string
	if *tagsRaw != "" {
		for _, t := range strings.Split(*tagsRaw, ",") {
			t = strings.TrimSpace(t)
			if t != "" {
				tags = append(tags, t)
			}
		}
	}

	if *runVaultServer || os.Getenv("VAULT_SERVER_ENABLE") == "1" {
		go func() {
			if err := vaultserver.Run(); err != nil {
				log.Warn("vault server exited", "err", err)
			}
		}()
	}

	log.Info("Worker starting", "server", *serverURL, "poll", *pollInterval, "maxConcurrency", *maxConcurrency)

	loop.Run(loop.Options{
		ServerURL:      *serverURL,
		PollInterval:   *pollInterval,
		ProjectID:      *projectID,
		WorkerName:     *workerName,
		MaxConcurrency: *maxConcurrency,
		Tags:           tags,
	})
}
