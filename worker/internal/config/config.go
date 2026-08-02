// Package config holds worker paths, environment variables, and directory
// resolution helpers. Mirrors worker/config.py.
package config

import (
	"os"
	"path/filepath"
	"sync"
)

var (
	DataDir     string
	ProjectsDir string
	TempDir     string
	LogDir      string
	LogFile     string
	initOnce    sync.Once
)

// Init resolves paths from environment variables and creates the base dirs.
func Init() {
	initOnce.Do(func() {
		DataDir = os.Getenv("DATA_DIR")
		if DataDir == "" {
			// Match worker/config.py: BASE_DIR = worker.parent.parent → data/
			cwd, _ := os.Getwd()
			DataDir = filepath.Join(cwd, "data")
		}
		ProjectsDir = filepath.Join(DataDir, "projects")
		TempDir = filepath.Join(DataDir, "temp")
		LogDir = filepath.Join(DataDir, "logs")
		LogFile = filepath.Join(LogDir, "worker.log")

		for _, d := range []string{DataDir, ProjectsDir, TempDir, LogDir} {
			_ = os.MkdirAll(d, 0o755)
		}
	})
}

// ProjectDir returns the on-disk project root.
func ProjectDir(projectID string) string {
	return filepath.Join(ProjectsDir, projectID)
}

// ProjectSecretsDir returns the encrypted secrets root for a project.
func ProjectSecretsDir(projectID string) string {
	return filepath.Join(ProjectDir(projectID), "secrets")
}

// ProjectVaultDir returns secrets/vault/ for the project.
func ProjectVaultDir(projectID string) string {
	return filepath.Join(ProjectSecretsDir(projectID), "vault")
}

// ProjectVaultKeysDir returns secrets/vault_keys/ for the project.
func ProjectVaultKeysDir(projectID string) string {
	return filepath.Join(ProjectSecretsDir(projectID), "vault_keys")
}

// ProjectVaultsFile returns secrets/vault/vaults.json for the project.
func ProjectVaultsFile(projectID string) string {
	return filepath.Join(ProjectVaultDir(projectID), "vaults.json")
}

// ProjectExecutionsDir returns history/executions or (legacy) executions.
func ProjectExecutionsDir(projectID string) string {
	base := ProjectDir(projectID)
	newPath := filepath.Join(base, "history", "executions")
	if _, err := os.Stat(newPath); err == nil {
		return newPath
	}
	if _, err := os.Stat(filepath.Join(base, "history")); err == nil {
		return newPath
	}
	return filepath.Join(base, "executions")
}

// ProjectInventoryFile picks a sane default inventory path for a project.
func ProjectInventoryFile(projectID string) string {
	base := ProjectDir(projectID)
	names := []string{"inventory.yaml", "inventory.yml", "hosts.yaml", "hosts.yml", "hosts", "hosts.ini"}
	roots := []string{
		filepath.Join(base, "repo", "IaC", "ansible", "inventories"),
		filepath.Join(base, "repo", "inventories"),
	}
	for _, root := range roots {
		for _, n := range names {
			p := filepath.Join(root, n)
			if _, err := os.Stat(p); err == nil {
				return p
			}
		}
	}
	return filepath.Join(roots[0], "inventory.yml")
}
