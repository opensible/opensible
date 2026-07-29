// Package vaultserver exposes a minimal HTTP API that wraps ansible-vault
// operations. Mirrors worker/vault_server.py.
package vaultserver

import (
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"net/http"
	"os"

	"github.com/opensible/worker-go/internal/config"
	"github.com/opensible/worker-go/internal/logging"
	"github.com/opensible/worker-go/internal/vault"
)

type reqPayload struct {
	ProjectID string `json:"project_id"`
	VaultID   string `json:"vault_id"`
	Content   string `json:"content"`
}

type respPayload struct {
	Success bool   `json:"success"`
	Content string `json:"content,omitempty"`
	Error   string `json:"error,omitempty"`
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func checkAuth(secret string, r *http.Request) bool {
	if secret == "" {
		return true
	}
	provided := r.Header.Get("X-Vault-Secret")
	return subtle.ConstantTimeCompare([]byte(provided), []byte(secret)) == 1
}

// Run starts the HTTP server. Returns when the server exits.
func Run() error {
	secret := os.Getenv("VAULT_SERVER_SECRET")
	host := os.Getenv("VAULT_SERVER_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := os.Getenv("VAULT_SERVER_PORT")
	if port == "" {
		port = "9999"
	}
	if secret == "" {
		logging.L().Warn("VAULT_SERVER_SECRET is not set — accepting any loopback client")
	}
	if host != "127.0.0.1" && host != "localhost" && host != "::1" && secret == "" {
		logging.L().Error("REFUSING to bind on non-loopback without VAULT_SERVER_SECRET", "host", host)
		return fmt.Errorf("refuse insecure bind")
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]bool{"ok": true})
	})
	mux.HandleFunc("/encrypt", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			writeJSON(w, 405, respPayload{Success: false, Error: "method not allowed"})
			return
		}
		if !checkAuth(secret, r) {
			writeJSON(w, 401, respPayload{Success: false, Error: "Unauthorized"})
			return
		}
		var p reqPayload
		if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
			writeJSON(w, 400, respPayload{Success: false, Error: err.Error()})
			return
		}
		if p.ProjectID == "" || p.VaultID == "" {
			writeJSON(w, 400, respPayload{Success: false, Error: "project_id and vault_id required"})
			return
		}
		if _, err := os.Stat(config.ProjectDir(p.ProjectID)); err != nil {
			writeJSON(w, 404, respPayload{Success: false, Error: "Project not found"})
			return
		}
		password := vault.GetKeyPasswordForVault(p.ProjectID, p.VaultID)
		if password == "" {
			writeJSON(w, 404, respPayload{Success: false, Error: "Vault or key not found"})
			return
		}
		if vault.IsEncrypted(p.Content) {
			writeJSON(w, 400, respPayload{Success: false, Error: "Content is already encrypted"})
			return
		}
		label := vault.GetVaultNameForVaultID(p.ProjectID, p.VaultID)
		out, err := vault.Encrypt(p.Content, password, label)
		if err != nil {
			writeJSON(w, 500, respPayload{Success: false, Error: err.Error()})
			return
		}
		writeJSON(w, 200, respPayload{Success: true, Content: out})
	})
	mux.HandleFunc("/decrypt", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			writeJSON(w, 405, respPayload{Success: false, Error: "method not allowed"})
			return
		}
		if !checkAuth(secret, r) {
			writeJSON(w, 401, respPayload{Success: false, Error: "Unauthorized"})
			return
		}
		var p reqPayload
		if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
			writeJSON(w, 400, respPayload{Success: false, Error: err.Error()})
			return
		}
		if p.ProjectID == "" || p.VaultID == "" {
			writeJSON(w, 400, respPayload{Success: false, Error: "project_id and vault_id required"})
			return
		}
		if _, err := os.Stat(config.ProjectDir(p.ProjectID)); err != nil {
			writeJSON(w, 404, respPayload{Success: false, Error: "Project not found"})
			return
		}
		if !vault.IsEncrypted(p.Content) {
			writeJSON(w, 200, respPayload{Success: true, Content: p.Content})
			return
		}
		password := vault.GetKeyPasswordForVault(p.ProjectID, p.VaultID)
		if password == "" {
			writeJSON(w, 404, respPayload{Success: false, Error: "Vault or key not found"})
			return
		}
		header := vault.ParseVaultIDFromHeader(p.Content)
		out, err := vault.Decrypt(p.Content, password, header)
		if err != nil {
			writeJSON(w, 500, respPayload{Success: false, Error: err.Error()})
			return
		}
		writeJSON(w, 200, respPayload{Success: true, Content: out})
	})

	addr := host + ":" + port
	logging.L().Info("Vault server listening", "addr", addr)
	return http.ListenAndServe(addr, mux)
}
