// Package logging provides a structured logger with rotating file output.
// Mirrors worker/logging_setup.py.
package logging

import (
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/opensible/worker-go/internal/config"
)

var (
	logger      *slog.Logger
	currentLvl  slog.Level
	levelVar    = new(slog.LevelVar)
	setupOnce   sync.Once
	fileHandler *rotatingFile
)

// L returns the shared logger.
func L() *slog.Logger {
	if logger == nil {
		Setup()
	}
	return logger
}

func parseLevel(s string) (slog.Level, bool) {
	switch strings.ToUpper(strings.TrimSpace(s)) {
	case "DEBUG":
		return slog.LevelDebug, true
	case "INFO":
		return slog.LevelInfo, true
	case "WARNING", "WARN":
		return slog.LevelWarn, true
	case "ERROR":
		return slog.LevelError, true
	case "CRITICAL":
		return slog.LevelError + 4, true
	}
	return slog.LevelDebug, false
}

// getSettings reads execution_settings.json, falling back to env.
func getSettings() (slog.Level, int64) {
	settingsFile := filepath.Join(config.DataDir, "execution_settings.json")
	if b, err := os.ReadFile(settingsFile); err == nil {
		var raw struct {
			LogLevel     string `json:"log_level"`
			MaxLogSizeMB int64  `json:"max_log_size_mb"`
		}
		if err := json.Unmarshal(b, &raw); err == nil {
			lvl, ok := parseLevel(raw.LogLevel)
			if !ok {
				lvl = slog.LevelDebug
			}
			size := raw.MaxLogSizeMB
			if size <= 0 {
				size = 10
			}
			return lvl, size
		}
	}
	lvlEnv := os.Getenv("LOG_LEVEL")
	if lvlEnv == "" {
		lvlEnv = "DEBUG"
	}
	lvl, ok := parseLevel(lvlEnv)
	if !ok {
		lvl = slog.LevelDebug
	}
	size := int64(10)
	if v := os.Getenv("MAX_LOG_SIZE_MB"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			size = int64(n)
		}
	}
	return lvl, size
}

// Setup initializes the shared logger. Safe to call multiple times.
func Setup() {
	setupOnce.Do(func() {
		lvl, sizeMB := getSettings()
		currentLvl = lvl
		levelVar.Set(lvl)

		config.Init() // ensure LOG_FILE dir exists
		fh, err := newRotatingFile(config.LogFile, sizeMB*1024*1024, 5)
		if err != nil {
			// Fall back to stderr only.
			fh = nil
		}
		fileHandler = fh

		var w io.Writer = os.Stdout
		if fh != nil {
			w = io.MultiWriter(os.Stdout, fh)
		}

		handler := slog.NewTextHandler(w, &slog.HandlerOptions{
			Level: levelVar,
			ReplaceAttr: func(groups []string, a slog.Attr) slog.Attr {
				if a.Key == slog.TimeKey {
					t := a.Value.Time()
					return slog.String("ts", t.Format("2006-01-02 15:04:05"))
				}
				return a
			},
		})
		logger = slog.New(handler)
	})
}

// ReloadLogLevel re-reads the settings file and updates the active level.
func ReloadLogLevel() bool {
	lvl, _ := getSettings()
	if lvl == currentLvl {
		return true
	}
	currentLvl = lvl
	levelVar.Set(lvl)
	L().Info("Log level reloaded", "level", lvl.String())
	return true
}

// rotatingFile is a very small size-based rotator.
type rotatingFile struct {
	mu       sync.Mutex
	path     string
	maxBytes int64
	backups  int
	f        *os.File
	size     int64
}

func newRotatingFile(path string, maxBytes int64, backups int) (*rotatingFile, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	// Docker keeps historical stdout/stderr. The UI reads this live file, so make
	// each worker container start with a clean active log instead of showing stale
	// lines from a previous worker process.
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
	if err != nil {
		return nil, err
	}
	info, _ := f.Stat()
	size := int64(0)
	if info != nil {
		size = info.Size()
	}
	return &rotatingFile{path: path, maxBytes: maxBytes, backups: backups, f: f, size: size}, nil
}

func (r *rotatingFile) Write(p []byte) (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.maxBytes > 0 && r.size+int64(len(p)) > r.maxBytes {
		r.rotate()
	}
	n, err := r.f.Write(p)
	r.size += int64(n)
	return n, err
}

func (r *rotatingFile) rotate() {
	_ = r.f.Close()
	// Shift .N → .N+1
	for i := r.backups - 1; i >= 1; i-- {
		src := fmt.Sprintf("%s.%d", r.path, i)
		dst := fmt.Sprintf("%s.%d", r.path, i+1)
		_ = os.Rename(src, dst)
	}
	_ = os.Rename(r.path, r.path+".1")
	f, err := os.OpenFile(r.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		// Best effort: reopen original.
		f, _ = os.OpenFile(r.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	}
	r.f = f
	r.size = 0
	_ = time.Now()
}
