package claim

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/opensible/worker-go/internal/config"
)

func TestNextRunClaimsOldestQueued(t *testing.T) {
	root := t.TempDir()
	t.Setenv("DATA_DIR", root)

	// Reset config.Init sync.Once by exercising Init after env is set.
	// config.Init is once; call it after DATA_DIR is set for this process.
	config.DataDir = root
	config.ProjectsDir = filepath.Join(root, "projects")
	config.TempDir = filepath.Join(root, "temp")
	config.LogDir = filepath.Join(root, "logs")
	for _, d := range []string{config.DataDir, config.ProjectsDir, config.TempDir, config.LogDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}

	projectID := "proj-a"
	execDir := filepath.Join(config.ProjectsDir, projectID, "history", "executions")
	if err := os.MkdirAll(execDir, 0o755); err != nil {
		t.Fatal(err)
	}

	writeExec := func(name string, queuedAt float64, status string) {
		t.Helper()
		payload := map[string]any{
			"id":       name,
			"status":   status,
			"queuedAt": queuedAt,
		}
		b, err := json.MarshalIndent(payload, "", "  ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(execDir, name+".json"), b, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	writeExec("exec-new", 200, "QUEUED")
	writeExec("exec-old", 100, "QUEUED")
	writeExec("exec-done", 50, "SUCCESS")

	id, data, gotProject := NextRun("")
	if id != "exec-old" {
		t.Fatalf("expected oldest queued exec-old, got %q", id)
	}
	if gotProject != projectID {
		t.Fatalf("project=%q want %q", gotProject, projectID)
	}
	if status, _ := data["status"].(string); status != "RUNNING" {
		t.Fatalf("status=%v want RUNNING", data["status"])
	}

	// File on disk should also be RUNNING so a second claim skips it.
	raw, err := os.ReadFile(filepath.Join(execDir, "exec-old.json"))
	if err != nil {
		t.Fatal(err)
	}
	var onDisk map[string]any
	if err := json.Unmarshal(raw, &onDisk); err != nil {
		t.Fatal(err)
	}
	if onDisk["status"] != "RUNNING" {
		t.Fatalf("on-disk status=%v want RUNNING", onDisk["status"])
	}

	id2, _, _ := NextRun("")
	if id2 != "exec-new" {
		t.Fatalf("second claim expected exec-new, got %q", id2)
	}

	id3, _, _ := NextRun("")
	if id3 != "" {
		t.Fatalf("expected empty queue, got %q", id3)
	}
}

func TestNextRunEmptyWhenNoProjects(t *testing.T) {
	root := t.TempDir()
	config.DataDir = root
	config.ProjectsDir = filepath.Join(root, "projects")
	if err := os.MkdirAll(config.ProjectsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	id, data, project := NextRun("")
	if id != "" || data != nil || project != "" {
		t.Fatalf("expected empty claim, got id=%q project=%q data=%v", id, project, data)
	}
}
