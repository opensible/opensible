// Package claim implements optional file-based claiming used by legacy
// deployments where the worker reads execution JSON files directly.
// Mirrors worker/claim.py. Production deployments should use the HTTP API's
// /api/worker/claim endpoint instead.
package claim

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"syscall"
	"time"

	"github.com/opensible/worker-go/internal/config"
	"github.com/opensible/worker-go/internal/logging"
)

type queued struct {
	QueuedAt float64
	Path     string
	Data     map[string]any
	Project  string
}

// NextRun returns the next QUEUED execution, transitioning it to RUNNING.
// Returns "", nil, "" when no work is available.
func NextRun(projectID string) (string, map[string]any, string) {
	var projectDirs []string
	if projectID != "" {
		projectDirs = []string{config.ProjectDir(projectID)}
	} else {
		entries, err := os.ReadDir(config.ProjectsDir)
		if err != nil {
			return "", nil, ""
		}
		for _, e := range entries {
			if e.IsDir() {
				projectDirs = append(projectDirs, filepath.Join(config.ProjectsDir, e.Name()))
			}
		}
	}

	var candidates []queued
	for _, pd := range projectDirs {
		pid := filepath.Base(pd)
		execDir := filepath.Join(pd, "history", "executions")
		if _, err := os.Stat(execDir); err != nil {
			execDir = filepath.Join(pd, "executions")
			if _, err := os.Stat(execDir); err != nil {
				continue
			}
		}
		matches, _ := filepath.Glob(filepath.Join(execDir, "*.json"))
		for _, m := range matches {
			data, ok := readExecution(m, true)
			if !ok {
				continue
			}
			if status, _ := data["status"].(string); status != "QUEUED" {
				continue
			}
			qa, _ := data["queuedAt"].(float64)
			if qa == 0 {
				qa, _ = data["createdAt"].(float64)
			}
			candidates = append(candidates, queued{QueuedAt: qa, Path: m, Data: data, Project: pid})
		}
	}

	if len(candidates) == 0 {
		return "", nil, ""
	}
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].QueuedAt < candidates[j].QueuedAt })

	for _, c := range candidates {
		id, _ := c.Data["id"].(string)
		if id == "" {
			continue
		}
		if !claimFile(c.Path, c.Data) {
			continue
		}
		logging.L().Info("Claimed run", "id", id, "project", c.Project)
		return id, c.Data, c.Project
	}
	return "", nil, ""
}

func readExecution(path string, tryLock bool) (map[string]any, bool) {
	f, err := os.Open(path)
	if err != nil {
		return nil, false
	}
	defer f.Close()
	if tryLock {
		if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
			return nil, false
		}
		defer syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
	}
	var data map[string]any
	if err := json.NewDecoder(f).Decode(&data); err != nil {
		return nil, false
	}
	return data, true
}

func claimFile(path string, data map[string]any) bool {
	f, err := os.OpenFile(path, os.O_RDWR, 0o644)
	if err != nil {
		return false
	}
	defer f.Close()
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX); err != nil {
		return false
	}
	defer syscall.Flock(int(f.Fd()), syscall.LOCK_UN)

	// Re-read after locking.
	if _, err := f.Seek(0, 0); err != nil {
		return false
	}
	var current map[string]any
	if err := json.NewDecoder(f).Decode(&current); err != nil {
		return false
	}
	if s, _ := current["status"].(string); s != "QUEUED" {
		return false
	}
	current["status"] = "RUNNING"
	current["startedAt"] = float64(time.Now().Unix())
	// A short random id
	current["workerId"] = randHex(8)

	b, err := json.MarshalIndent(current, "", "  ")
	if err != nil {
		return false
	}
	if _, err := f.Seek(0, 0); err != nil {
		return false
	}
	if err := f.Truncate(0); err != nil {
		return false
	}
	if _, err := f.Write(b); err != nil {
		return false
	}
	_ = f.Sync()
	// mirror map back to caller
	for k, v := range current {
		data[k] = v
	}
	return true
}

func randHex(n int) string {
	const chars = "0123456789abcdef"
	b := make([]byte, n)
	now := time.Now().UnixNano()
	for i := range b {
		b[i] = chars[int(now>>uint(i*4))&0x0f]
	}
	return string(b)
}
