// Inventory + connectionSecret resolution.
// Mirrors _resolve_inventory_path, _candidate_host_vars_dirs, and
// process_host_connection_secrets in worker/execute.py.
package execute

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/opensible/worker-go/internal/config"
	"gopkg.in/yaml.v3"
)

var inventoryFileNames = []string{
	"inventory.yaml", "inventory.yml", "hosts.yaml", "hosts.yml", "hosts", "hosts.ini",
}

func repoDir(projectDir string) string { return filepath.Join(projectDir, "repo") }

func inventoryRoots(projectDir string) []string {
	rd := repoDir(projectDir)
	return []string{
		filepath.Join(rd, "IaC", "ansible", "inventories"),
		filepath.Join(rd, "inventories"),
	}
}

// resolveInventoryPath resolves a UI-provided inventory value.
func resolveInventoryPath(projectDir, invFile string) string {
	if invFile == "" {
		invFile = "inventory.yml"
	}
	if filepath.IsAbs(invFile) {
		return invFile
	}
	rd := repoDir(projectDir)
	direct := filepath.Join(rd, invFile)
	if _, err := os.Stat(direct); err == nil {
		return direct
	}
	stripped := []string{invFile}
	for _, prefix := range []string{"inventories/", "inventory/"} {
		if strings.HasPrefix(invFile, prefix) {
			stripped = append(stripped, invFile[len(prefix):])
		}
	}
	for _, rel := range stripped {
		for _, root := range inventoryRoots(projectDir) {
			candidate := filepath.Join(root, rel)
			if _, err := os.Stat(candidate); err == nil {
				return candidate
			}
		}
	}
	// Filename fallback.
	base := filepath.Base(invFile)
	for _, n := range inventoryFileNames {
		if n == base {
			for _, root := range inventoryRoots(projectDir) {
				candidate := filepath.Join(root, base)
				if _, err := os.Stat(candidate); err == nil {
					return candidate
				}
			}
		}
	}
	// Last chance: walk the inventory roots.
	for _, root := range inventoryRoots(projectDir) {
		if _, err := os.Stat(root); err != nil {
			continue
		}
		_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
			if err != nil || info.IsDir() {
				return nil
			}
			if filepath.Base(path) == base {
				direct = path
			}
			return nil
		})
	}
	return direct
}

func candidateHostVarsDirs(projectDir, invPath, projectID string) []string {
	dirs := []string{}
	if invPath != "" {
		dirs = append(dirs, filepath.Join(filepath.Dir(invPath), "host_vars"))
	}
	for _, root := range inventoryRoots(projectDir) {
		dirs = append(dirs, filepath.Join(root, "host_vars"))
	}
	if def := config.ProjectInventoryFile(projectID); def != "" {
		dirs = append(dirs, filepath.Join(filepath.Dir(def), "host_vars"))
	}
	return dedupe(dirs)
}

func candidateGroupVarsDirs(projectDir, invPath, projectID string) []string {
	dirs := []string{}
	if invPath != "" {
		dirs = append(dirs, filepath.Join(filepath.Dir(invPath), "group_vars"))
	}
	for _, root := range inventoryRoots(projectDir) {
		dirs = append(dirs, filepath.Join(root, "group_vars"))
	}
	if def := config.ProjectInventoryFile(projectID); def != "" {
		dirs = append(dirs, filepath.Join(filepath.Dir(def), "group_vars"))
	}
	return dedupe(dirs)
}

func dedupe(in []string) []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(in))
	for _, s := range in {
		if seen[s] {
			continue
		}
		seen[s] = true
		out = append(out, s)
	}
	return out
}

// hostVarsUpdate captures the resolved connection variables for a single host.
type hostVarsUpdate struct {
	Vars map[string]any
}

// processHostConnectionSecrets loads the inventory, resolves each host's
// `connectionSecret`, and writes any required temp SSH key files. The returned
// map is keyed by host name.
func processHostConnectionSecrets(projectDir, invPath, projectID string, tmpKeys *[]string) map[string]*hostVarsUpdate {
	invBytes, err := os.ReadFile(invPath)
	if err != nil {
		return nil
	}
	var inv map[string]any
	if err := yaml.Unmarshal(invBytes, &inv); err != nil {
		return nil
	}
	allHosts := map[string]bool{}
	hostGroups := map[string]map[string]bool{}

	var walkGroup func(name string, g map[string]any)
	addHosts := func(hosts any, group string) {
		switch v := hosts.(type) {
		case map[string]any:
			for h := range v {
				h = strings.TrimSpace(h)
				if h == "" {
					continue
				}
				allHosts[h] = true
				if hostGroups[h] == nil {
					hostGroups[h] = map[string]bool{}
				}
				hostGroups[h][group] = true
			}
		case []any:
			for _, e := range v {
				if s, ok := e.(string); ok {
					s = strings.TrimSpace(s)
					if s == "" {
						continue
					}
					allHosts[s] = true
					if hostGroups[s] == nil {
						hostGroups[s] = map[string]bool{}
					}
					hostGroups[s][group] = true
				}
			}
		}
	}
	walkGroup = func(name string, g map[string]any) {
		if g == nil {
			return
		}
		if hosts, ok := g["hosts"]; ok {
			addHosts(hosts, name)
		}
		if children, ok := g["children"].(map[string]any); ok {
			for cn, cd := range children {
				if m, ok := cd.(map[string]any); ok {
					walkGroup(cn, m)
				}
			}
		}
	}
	if all, ok := inv["all"].(map[string]any); ok {
		walkGroup("all", all)
	}

	hvDirs := candidateHostVarsDirs(projectDir, invPath, projectID)
	gvDirs := candidateGroupVarsDirs(projectDir, invPath, projectID)

	loadGroupVarsForHost := func(host string) map[string]any {
		groups := []string{}
		for g := range hostGroups[host] {
			groups = append(groups, g)
		}
		groups = append(groups, "all")
		for _, g := range groups {
			for _, gvDir := range gvDirs {
				if _, err := os.Stat(gvDir); err != nil {
					continue
				}
				for _, ext := range []string{".yml", ".yaml"} {
					p := filepath.Join(gvDir, g+ext)
					if _, err := os.Stat(p); err != nil {
						continue
					}
					b, err := os.ReadFile(p)
					if err != nil {
						continue
					}
					var m map[string]any
					if err := yaml.Unmarshal(b, &m); err != nil {
						continue
					}
					if _, ok := m["connectionSecret"]; ok {
						return m
					}
				}
			}
		}
		return nil
	}

	updates := map[string]*hostVarsUpdate{}
	for host := range allHosts {
		var hostVars map[string]any
		for _, hvDir := range hvDirs {
			p := filepath.Join(hvDir, host+".yml")
			if _, err := os.Stat(p); err != nil {
				continue
			}
			b, err := os.ReadFile(p)
			if err != nil {
				continue
			}
			var m map[string]any
			if err := yaml.Unmarshal(b, &m); err == nil {
				hostVars = m
				break
			}
		}
		if hostVars == nil {
			hostVars = map[string]any{}
		}
		secretName, _ := hostVars["connectionSecret"].(string)
		if secretName == "" {
			if gv := loadGroupVarsForHost(host); gv != nil {
				secretName, _ = gv["connectionSecret"].(string)
				if secretName != "" {
					for _, k := range []string{"ansible_user", "ansible_port"} {
						if _, has := hostVars[k]; !has {
							if v, ok := gv[k]; ok {
								hostVars[k] = v
							}
						}
					}
				}
			}
		}
		if secretName == "" {
			continue
		}
		secretsDir := config.ProjectSecretsDir(projectID)
		secretPath := filepath.Join(secretsDir, "ssh_keys", secretName+".json")
		if _, err := os.Stat(secretPath); err != nil {
			secretPath = filepath.Join(secretsDir, secretName+".json")
		}
		b, err := os.ReadFile(secretPath)
		if err != nil {
			continue
		}
		var secret map[string]any
		if err := json.Unmarshal(b, &secret); err != nil {
			continue
		}
		typ, _ := secret["type"].(string)
		if user, _ := secret["username"].(string); user != "" {
			hostVars["ansible_user"] = user
		}
		if _, has := hostVars["ansible_ssh_common_args"]; !has {
			hostVars["ansible_ssh_common_args"] = "-o StrictHostKeyChecking=accept-new"
		}
		switch typ {
		case "ssh_key":
			pk, _ := secret["privateKey"].(string)
			if pk == "" {
				continue
			}
			if !strings.HasSuffix(pk, "\n") {
				pk += "\n"
			}
			f, err := os.CreateTemp(config.TempDir, "ssh_key_*.pem")
			if err != nil {
				continue
			}
			_, _ = f.WriteString(pk)
			path := f.Name()
			_ = f.Close()
			_ = os.Chmod(path, 0o600)
			*tmpKeys = append(*tmpKeys, path)
			abs, _ := filepath.Abs(path)
			hostVars["ansible_ssh_private_key_file"] = abs
		case "login_password":
			pw, _ := secret["password"].(string)
			if pw == "" {
				continue
			}
			hostVars["ansible_password"] = pw
			hostVars["ansible_ssh_pass"] = pw
		default:
			continue
		}
		updates[host] = &hostVarsUpdate{Vars: hostVars}
	}
	return updates
}

// writeTempInventory writes a copy of invPath (plus a fresh host_vars/ with
// resolved credentials) into a new temp directory. Returns the new inventory
// path.
func writeTempInventory(invPath string, updates map[string]*hostVarsUpdate) (string, error) {
	invName := filepath.Base(invPath)
	dir, err := os.MkdirTemp(config.TempDir, "inventory_dir_*")
	if err != nil {
		return "", err
	}
	src, err := os.ReadFile(invPath)
	if err != nil {
		return "", err
	}
	dst := filepath.Join(dir, invName)
	if err := os.WriteFile(dst, src, 0o644); err != nil {
		return "", err
	}
	// Copy sibling group_vars / host_vars if present.
	srcDir := filepath.Dir(invPath)
	for _, sub := range []string{"group_vars", "host_vars"} {
		_ = copyTree(filepath.Join(srcDir, sub), filepath.Join(dir, sub))
	}
	// Overlay resolved host_vars files.
	hvDir := filepath.Join(dir, "host_vars")
	if err := os.MkdirAll(hvDir, 0o755); err != nil {
		return "", err
	}
	for host, upd := range updates {
		out, err := yaml.Marshal(upd.Vars)
		if err != nil {
			continue
		}
		if err := os.WriteFile(filepath.Join(hvDir, host+".yml"), out, 0o600); err != nil {
			return "", err
		}
	}
	return dst, nil
}

func copyTree(src, dst string) error {
	info, err := os.Stat(src)
	if err != nil || !info.IsDir() {
		return err
	}
	if err := os.MkdirAll(dst, 0o755); err != nil {
		return err
	}
	entries, err := os.ReadDir(src)
	if err != nil {
		return err
	}
	for _, e := range entries {
		sp := filepath.Join(src, e.Name())
		dp := filepath.Join(dst, e.Name())
		if e.IsDir() {
			if err := copyTree(sp, dp); err != nil {
				return err
			}
			continue
		}
		b, err := os.ReadFile(sp)
		if err != nil {
			return err
		}
		if err := os.WriteFile(dp, b, 0o644); err != nil {
			return err
		}
	}
	return nil
}

// silence unused import when copyTree is later stripped
var _ = fmt.Sprintf
