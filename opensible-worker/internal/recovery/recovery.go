// Package recovery marks stuck RUNNING or CANCELING executions terminal.
// Mirrors worker/recovery.py. Note: unlike the Python version, this Go port
// does NOT call back into the backend's `executions_store` helpers — the
// Go worker treats those files as opaque JSON. A separate backend process
// is still authoritative for validated state transitions.
package recovery

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"

	"github.com/opensible/worker-go/internal/config"
	"github.com/opensible/worker-go/internal/logging"
)

var finalStatuses = map[string]bool{
	"SUCCESS":  true,
	"FAILED":   true,
	"CANCELED": true,
	"SKIPPED":  true,
}

// RecoverStuck walks projects' execution JSON files and force-terminates ones
// that have been stuck too long.
func RecoverStuck(maxAgeMinutes, maxCancelMinutes int) {
	if maxAgeMinutes <= 0 {
		maxAgeMinutes = 30
	}
	if maxCancelMinutes <= 0 {
		maxCancelMinutes = 5
	}
	entries, err := os.ReadDir(config.ProjectsDir)
	if err != nil {
		return
	}
	now := float64(time.Now().Unix())
	recovered := 0
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		projectID := e.Name()
		execDir := filepath.Join(config.ProjectsDir, projectID, "history", "executions")
		if _, err := os.Stat(execDir); err != nil {
			execDir = filepath.Join(config.ProjectsDir, projectID, "executions")
			if _, err := os.Stat(execDir); err != nil {
				continue
			}
		}
		files, _ := filepath.Glob(filepath.Join(execDir, "*.json"))
		for _, path := range files {
			if recoverOne(path, now, maxAgeMinutes, maxCancelMinutes) {
				recovered++
			}
		}
	}
	if recovered > 0 {
		logging.L().Info("Recovered stuck runs", "count", recovered)
	}
}

func recoverOne(path string, now float64, maxAgeMinutes, maxCancelMinutes int) bool {
	b, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	var data map[string]any
	if err := json.Unmarshal(b, &data); err != nil {
		return false
	}
	status, _ := data["status"].(string)
	if finalStatuses[status] {
		return false
	}
	switch status {
	case "RUNNING":
		started, _ := data["startedAt"].(float64)
		if started == 0 {
			return false
		}
		age := (now - started) / 60
		if age <= float64(maxAgeMinutes) {
			return false
		}
		data["status"] = "FAILED"
		data["finishedAt"] = now
		data["duration"] = int(now - started)
		data["statusUpdatedAt"] = now
		data["error"] = "Execution timeout"
	case "CANCELING":
		req, _ := data["cancelRequestedAt"].(float64)
		if req == 0 {
			return false
		}
		age := (now - req) / 60
		if age <= float64(maxCancelMinutes) {
			return false
		}
		data["status"] = "CANCELED"
		data["canceledAt"] = now
		data["cancelReason"] = "timeout"
		data["statusUpdatedAt"] = now
		if s, ok := data["startedAt"].(float64); ok {
			data["duration"] = int(now - s)
			data["finishedAt"] = now
		}
	default:
		return false
	}
	out, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		return false
	}
	if err := os.WriteFile(path, out, 0o644); err != nil {
		return false
	}
	logging.L().Info("Recovered execution", "path", path, "newStatus", data["status"])
	return true
}
