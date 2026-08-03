// Package vault wraps `ansible-vault` for encrypt/decrypt operations.
// Mirrors worker/vault_utils.py.
package vault

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/opensible/worker-go/internal/config"
)

// Vault is the on-disk vaults.json record.
type Vault struct {
	ID      string `json:"id"`
	VaultID string `json:"vaultId,omitempty"`
	Name    string `json:"name,omitempty"`
	KeyID   string `json:"keyId,omitempty"`
}

// LoadVaults reads secrets/vault/vaults.json (with legacy fallback).
func LoadVaults(projectID string) []Vault {
	primary := config.ProjectVaultsFile(projectID)
	b, err := os.ReadFile(primary)
	if err != nil {
		legacy := filepath.Join(config.ProjectSecretsDir(projectID), "vaults.json")
		b, err = os.ReadFile(legacy)
		if err != nil {
			return nil
		}
	}
	var v []Vault
	if err := json.Unmarshal(b, &v); err != nil {
		return nil
	}
	return v
}

// GetVaultNameForVaultID returns the ansible-vault label for a vault uuid.
func GetVaultNameForVaultID(projectID, vaultUUID string) string {
	for _, v := range LoadVaults(projectID) {
		if v.ID == vaultUUID {
			if v.VaultID != "" {
				return v.VaultID
			}
			return v.Name
		}
	}
	return ""
}

// Credential is a vault label + password pair.
type Credential struct {
	ID       string
	Label    string
	Password string
}

// GetCredentials returns all configured vault credentials for the project.
func GetCredentials(projectID string) []Credential {
	var out []Credential
	keysDir := config.ProjectVaultKeysDir(projectID)
	for _, v := range LoadVaults(projectID) {
		if v.KeyID == "" {
			continue
		}
		b, err := os.ReadFile(filepath.Join(keysDir, v.KeyID+".pass"))
		if err != nil {
			continue
		}
		pw := strings.TrimRight(string(b), "\n")
		if pw == "" {
			continue
		}
		label := v.VaultID
		if label == "" {
			label = v.Name
		}
		out = append(out, Credential{ID: v.ID, Label: strings.TrimSpace(label), Password: pw})
	}
	return out
}

// GetKeyPasswordForVault returns the password bound to a vault uuid.
func GetKeyPasswordForVault(projectID, vaultUUID string) string {
	for _, v := range LoadVaults(projectID) {
		if v.ID != vaultUUID {
			continue
		}
		if v.KeyID == "" {
			return ""
		}
		b, err := os.ReadFile(filepath.Join(config.ProjectVaultKeysDir(projectID), v.KeyID+".pass"))
		if err != nil {
			return ""
		}
		return strings.TrimRight(string(b), "\n")
	}
	return ""
}

// IsEncrypted returns true if content starts with $ANSIBLE_VAULT.
func IsEncrypted(content string) bool {
	return strings.HasPrefix(strings.TrimSpace(content), "$ANSIBLE_VAULT")
}

// ParseVaultIDFromHeader extracts the label from a $ANSIBLE_VAULT header.
func ParseVaultIDFromHeader(content string) string {
	line := strings.SplitN(strings.TrimSpace(content), "\n", 2)[0]
	if !strings.HasPrefix(line, "$ANSIBLE_VAULT") {
		return ""
	}
	parts := strings.Split(line, ";")
	if len(parts) < 4 {
		return ""
	}
	return strings.TrimSpace(parts[3])
}

// Encrypt calls ansible-vault to encrypt plain text.
func Encrypt(plain, password, vaultID string) (string, error) {
	plainF, err := writeTemp(".yml", plain)
	if err != nil {
		return "", err
	}
	defer os.Remove(plainF)
	passF, err := writeTemp(".pass", ensureTrailingNewline(password))
	if err != nil {
		return "", err
	}
	_ = os.Chmod(passF, 0o600)
	defer os.Remove(passF)
	outF, err := os.CreateTemp("", "vaultout-*.yml")
	if err != nil {
		return "", err
	}
	outPath := outF.Name()
	_ = outF.Close()
	defer os.Remove(outPath)
	args := []string{"encrypt", plainF, "--output", outPath}
	if vaultID != "" {
		args = append(args, "--vault-id", vaultID+"@"+passF, "--encrypt-vault-id", vaultID)
	} else {
		args = append(args, "--vault-password-file", passF)
	}
	if err := runWithTimeout(30*time.Second, "ansible-vault", args...); err != nil {
		return "", err
	}
	b, err := os.ReadFile(outPath)
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// Decrypt calls ansible-vault to decrypt encrypted content.
func Decrypt(encrypted, password, vaultID string) (string, error) {
	encF, err := writeTemp(".yml", encrypted)
	if err != nil {
		return "", err
	}
	defer os.Remove(encF)
	passF, err := writeTemp(".pass", ensureTrailingNewline(password))
	if err != nil {
		return "", err
	}
	_ = os.Chmod(passF, 0o600)
	defer os.Remove(passF)
	args := []string{"decrypt", encF, "--output", "-"}
	if vaultID != "" {
		args = append(args, "--vault-id", vaultID+"@"+passF)
	} else {
		args = append(args, "--vault-password-file", passF)
	}
	cmd := exec.Command("ansible-vault", args...)
	out, err := cmd.Output()
	if err != nil {
		return "", err
	}
	return string(out), nil
}

func writeTemp(suffix, content string) (string, error) {
	f, err := os.CreateTemp("", "vault-*"+suffix)
	if err != nil {
		return "", err
	}
	defer f.Close()
	if _, err := f.WriteString(content); err != nil {
		return "", err
	}
	return f.Name(), nil
}

func ensureTrailingNewline(s string) string {
	if strings.HasSuffix(s, "\n") {
		return s
	}
	return s + "\n"
}

func runWithTimeout(_ time.Duration, name string, args ...string) error {
	cmd := exec.Command(name, args...)
	return cmd.Run()
}
