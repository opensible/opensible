// Package execute runs a claimed execution (ansible-playbook or tofu).
// Mirrors worker/execute.py.
package execute

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/opensible/worker-go/internal/config"
	"github.com/opensible/worker-go/internal/httpclient"
	"github.com/opensible/worker-go/internal/logging"
	"github.com/opensible/worker-go/internal/systeminfo"
	"github.com/opensible/worker-go/internal/vault"
)

// Run executes a single claimed execution and reports its final status.
func Run(executionID string, execData map[string]any, projectID string, client *httpclient.Client) {
	log := logging.L()
	runParams, _ := execData["runParams"].(map[string]any)
	if runParams == nil {
		log.Error("runParams missing", "execution", executionID)
		client.FinishExecution(executionID, "FAILED", float64(time.Now().Unix()), 0, nil, "runParams missing", nil)
		return
	}
	if t, _ := runParams["execution_type"].(string); t == "TOFU_RUN" {
		executeTofuRun(executionID, execData, projectID, client)
		return
	}
	executePlaybook(executionID, execData, projectID, client, runParams)
}

func executePlaybook(executionID string, execData map[string]any, projectID string, client *httpclient.Client, runParams map[string]any) {
	log := logging.L()
	startTime := time.Now()
	tempFiles := []string{}
	tempKeys := []string{}
	finalStatus := "FAILED"
	var returnCode *int
	var errStr string
	var resultData map[string]any
	sendLog := func(s string) { client.SendLog(executionID, s, 0) }

	defer func() {
		duration := int(time.Since(startTime).Seconds())
		client.FinishExecution(executionID, finalStatus, float64(time.Now().Unix()), duration, returnCode, errStr, resultData)
		for _, p := range tempKeys {
			_ = os.Remove(p)
		}
		for _, p := range tempFiles {
			info, err := os.Stat(p)
			if err != nil {
				continue
			}
			if info.IsDir() {
				_ = os.RemoveAll(p)
			} else {
				_ = os.Remove(p)
			}
		}
	}()

	tempPlaybookRaw, _ := runParams["temp_playbook"].(string)
	playbookContent, _ := runParams["playbook_content"].(string)
	tempPlaybook := tempPlaybookRaw
	if (tempPlaybook == "" || !fileExists(tempPlaybook)) && strings.TrimSpace(playbookContent) != "" {
		dir, err := os.MkdirTemp(config.TempDir, "worker_payload_"+executionID+"_*")
		if err == nil {
			tempFiles = append(tempFiles, dir)
			name := filepath.Base(tempPlaybookRaw)
			if name == "" || name == "." {
				name = executionID + ".yml"
			}
			target := filepath.Join(dir, name)
			if err := os.WriteFile(target, []byte(playbookContent), 0o600); err == nil {
				tempPlaybook = target
				runParams["temp_playbook"] = target
			}
		}
	}
	if !fileExists(tempPlaybook) {
		errStr = "Playbook file not found: " + tempPlaybook
		sendLog("ERROR: " + errStr + "\n")
		return
	}

	// Docker preflight injection.
	_ = injectDockerPreflight(tempPlaybook)

	executionType, _ := runParams["execution_type"].(string)
	if executionType == "" {
		executionType = "PLAYBOOK_RUN"
	}
	inventoryFiles := coerceStringList(runParams["inventory_files"])
	if len(inventoryFiles) == 0 {
		inventoryFiles = []string{"inventory.yml"}
	}
	tempInventory, _ := runParams["temp_inventory"].(string)
	ansibleConfig, _ := runParams["ansible_config"].(string)
	projectDir, _ := runParams["project_dir"].(string)
	limitHost, _ := runParams["limit_host"].(string)
	if limitHost == "gitlab-ce" {
		limitHost = "gitlab_ce"
	}
	if _, err := os.Stat(projectDir); err != nil {
		errStr = "Project directory not found: " + projectDir
		sendLog("ERROR: " + errStr + "\n")
		return
	}

	// Build -i args (respecting HOST_CHECK/HOST_FACTS temp inventory).
	var inventoryArgs []string
	if (executionType == "HOST_CHECK" || executionType == "HOST_FACTS") && tempInventory != "" {
		if !fileExists(tempInventory) {
			errStr = "Temporary inventory file not found: " + tempInventory
			sendLog("ERROR: " + errStr + "\n")
			return
		}
		inventoryArgs = []string{"-i", tempInventory}
		if v := runParams["temp_key_files"]; v != nil {
			for _, k := range coerceStringList(v) {
				tempKeys = append(tempKeys, k)
			}
		}
	} else if len(inventoryFiles) == 1 {
		invPath := resolveInventoryPath(projectDir, inventoryFiles[0])
		if !fileExists(invPath) {
			def := config.ProjectInventoryFile(projectID)
			if fileExists(def) {
				invPath = def
			} else {
				errStr = "Inventory file not found: " + inventoryFiles[0]
				sendLog("ERROR: " + errStr + "\n")
				return
			}
		}
		updates := processHostConnectionSecrets(projectDir, invPath, projectID, &tempKeys)
		if len(updates) > 0 {
			newInv, err := writeTempInventory(invPath, updates)
			if err == nil {
				tempFiles = append(tempFiles, filepath.Dir(newInv))
				inventoryArgs = []string{"-i", newInv}
			} else {
				inventoryArgs = []string{"-i", invPath}
			}
		} else {
			inventoryArgs = []string{"-i", invPath}
		}
	} else {
		// Multi-inventory: pass each -i separately.
		for _, invFile := range inventoryFiles {
			p := resolveInventoryPath(projectDir, invFile)
			if fileExists(p) {
				inventoryArgs = append(inventoryArgs, "-i", p)
			}
		}
	}

	// Command.
	stage, _ := runParams["stage"].(string)
	stage = strings.ToLower(stage)
	if stage == "" {
		stage = "apply"
	}
	galaxyInstall, _ := runParams["galaxy_install"].(bool)

	var cmdArgs []string
	if stage == "init" || galaxyInstall {
		repo := filepath.Join(projectDir, "repo")
		var req string
		for _, c := range []string{"requirements.yml", "requirements.yaml", "roles/requirements.yml", "collections/requirements.yml"} {
			p := filepath.Join(repo, c)
			if fileExists(p) {
				req = p
				break
			}
		}
		if req == "" {
			cmdArgs = []string{"ansible-galaxy", "install", "--help"}
		} else {
			cmdArgs = []string{"ansible-galaxy", "install", "-r", req, "-f"}
		}
	} else {
		cmdArgs = append([]string{"ansible-playbook", tempPlaybook}, inventoryArgs...)
		hostsList := coerceStringList(runParams["hosts"])
		if (executionType == "HOST_CHECK" || executionType == "HOST_FACTS") && limitHost != "" && len(hostsList) <= 1 {
			cmdArgs = append(cmdArgs, "--limit", limitHost)
		}
		if boolParam(runParams, "check_mode") {
			cmdArgs = append(cmdArgs, "--check")
		}
		if boolParam(runParams, "diff_mode") || stage == "plan" {
			cmdArgs = append(cmdArgs, "--diff")
		}
		if boolParam(runParams, "syntax_check") || stage == "validate" {
			cmdArgs = append(cmdArgs, "--syntax-check")
		}
		if v, ok := runParams["verbosity"].(string); ok {
			if v == "-v" || v == "-vv" || v == "-vvv" || v == "-vvvv" {
				cmdArgs = append(cmdArgs, v)
			}
		}
		if forks, ok := coerceInt(runParams["forks"]); ok && forks > 0 {
			cmdArgs = append(cmdArgs, "--forks", fmt.Sprintf("%d", forks))
		}
		if boolParam(runParams, "force_handlers") {
			cmdArgs = append(cmdArgs, "--force-handlers")
		}
		if ct, ok := coerceInt(runParams["connection_timeout"]); ok && ct > 0 {
			cmdArgs = append(cmdArgs, "--timeout", fmt.Sprintf("%d", ct))
		}
	}

	// Vault credentials.
	if stage != "init" {
		vaultID, _ := runParams["vault_id"].(string)
		transferred := coerceCredentialsList(runParams["vault_credentials"])
		if vaultID != "" {
			password := ""
			label := ""
			for _, c := range transferred {
				if c.ID == vaultID {
					password = c.Password
					label = c.Label
					break
				}
			}
			if password == "" {
				password = vault.GetKeyPasswordForVault(projectID, vaultID)
				label = vault.GetVaultNameForVaultID(projectID, vaultID)
			}
			if password != "" {
				fp, err := writePasswordFile(password)
				if err == nil {
					tempFiles = append(tempFiles, fp)
					if label != "" {
						cmdArgs = append(cmdArgs, "--vault-id", label+"@"+fp)
					} else {
						cmdArgs = append(cmdArgs, "--vault-password-file", fp)
					}
				}
			}
		} else if runNeedsVaultCredentials(tempPlaybook, inventoryArgs) {
			creds := transferred
			if len(creds) == 0 {
				creds = vault.GetCredentials(projectID)
			}
			for _, c := range creds {
				if c.Password == "" {
					continue
				}
				fp, err := writePasswordFile(c.Password)
				if err != nil {
					continue
				}
				tempFiles = append(tempFiles, fp)
				if c.Label != "" {
					cmdArgs = append(cmdArgs, "--vault-id", c.Label+"@"+fp)
				} else {
					cmdArgs = append(cmdArgs, "--vault-password-file", fp)
				}
			}
		}
	}

	// Environment.
	env := os.Environ()
	ansibleConfigPath := resolveAnsibleConfig(projectDir, ansibleConfig)
	if ansibleConfigPath != "" {
		env = append(env, "ANSIBLE_CONFIG="+ansibleConfigPath)
	}
	env = sanitizeAnsibleRuntimeEnv(env, ansibleConfigPath)

	// Roles path.
	rolesPath := filepath.Join(projectDir, "repo", "roles")
	if _, err := os.Stat(rolesPath); err == nil {
		env = append(env, "ANSIBLE_ROLES_PATH="+rolesPath)
	}

	// Mitogen strategy.
	if strategy, _ := runParams["strategy"].(string); strings.HasPrefix(strategy, "mitogen_") {
		if p := systeminfo.MitogenStrategyPluginsPath(); p != "" {
			env = append(env, "ANSIBLE_STRATEGY_PLUGINS="+p)
			log.Info("Using Mitogen strategy", "strategy", strategy, "plugins", p)
		}
	}

	log.Info("Starting ansible", "execution", executionID, "project", projectID,
		"type", executionType, "cmd", strings.Join(cmdArgs, " "))

	// Run process.
	cmd := exec.Command(cmdArgs[0], cmdArgs[1:]...)
	cmd.Dir = projectDir
	cmd.Env = env
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		errStr = err.Error()
		sendLog("ERROR: " + errStr + "\n")
		return
	}
	cmd.Stderr = cmd.Stdout
	if err := cmd.Start(); err != nil {
		errStr = err.Error()
		sendLog("ERROR: " + errStr + "\n")
		return
	}
	pgid, _ := syscall.Getpgid(cmd.Process.Pid)

	// Heartbeat goroutine.
	ctx, cancelHB := context.WithCancel(context.Background())
	var hbWG sync.WaitGroup
	hbWG.Add(1)
	go func() {
		defer hbWG.Done()
		t := time.NewTicker(30 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				client.Heartbeat(executionID)
			}
		}
	}()

	// Output streaming goroutine.
	var outputMu sync.Mutex
	var fullOutput []byte
	captureFull := executionType == "HOST_CHECK" || executionType == "HOST_FACTS"

	outDone := make(chan struct{})
	go func() {
		buf := make([]byte, 4096)
		for {
			n, rerr := stdout.Read(buf)
			if n > 0 {
				chunk := make([]byte, n)
				copy(chunk, buf[:n])
				client.SendLog(executionID, string(chunk), 0)
				if captureFull {
					outputMu.Lock()
					fullOutput = append(fullOutput, chunk...)
					outputMu.Unlock()
				}
			}
			if rerr != nil {
				close(outDone)
				return
			}
		}
	}()

	wasCanceling := false
	statusCheckTicker := time.NewTicker(2 * time.Second)
	defer statusCheckTicker.Stop()

	// Wait loop.
waitLoop:
	for {
		select {
		case <-outDone:
			break waitLoop
		case <-statusCheckTicker.C:
			st := client.GetExecutionStatus(executionID, projectID)
			if st == "SUCCESS" || st == "FAILED" || st == "CANCELED" {
				log.Warn("Execution already finalized server-side, killing", "status", st)
				if pgid > 0 {
					_ = syscall.Kill(-pgid, syscall.SIGKILL)
				} else {
					_ = cmd.Process.Kill()
				}
				<-outDone
				break waitLoop
			}
			if st == "CANCELING" && !wasCanceling {
				wasCanceling = true
				sendLog("[worker] Canceling execution, sending SIGTERM\n")
				if pgid > 0 {
					_ = syscall.Kill(-pgid, syscall.SIGTERM)
				} else {
					_ = cmd.Process.Signal(syscall.SIGTERM)
				}
				graceTimer := time.NewTimer(10 * time.Second)
				select {
				case <-outDone:
					graceTimer.Stop()
				case <-graceTimer.C:
					sendLog("[worker] Force killing execution after 10s grace period\n")
					if pgid > 0 {
						_ = syscall.Kill(-pgid, syscall.SIGKILL)
					} else {
						_ = cmd.Process.Kill()
					}
					<-outDone
				}
				break waitLoop
			}
		}
	}

	waitErr := cmd.Wait()
	cancelHB()
	hbWG.Wait()

	rc := 0
	if waitErr != nil {
		if ee, ok := waitErr.(*exec.ExitError); ok {
			rc = ee.ExitCode()
		} else {
			rc = 1
		}
	}
	returnCode = &rc

	// If backend already finalized, do nothing further.
	if st := client.GetExecutionStatus(executionID, projectID); st == "SUCCESS" || st == "FAILED" || st == "CANCELED" {
		finalStatus = st
		return
	}

	if captureFull {
		resultData = ExtractResult(string(fullOutput), executionType, rc, runParams)
	}

	switch {
	case wasCanceling:
		finalStatus = "CANCELED"
	case rc == 0:
		finalStatus = "SUCCESS"
	default:
		finalStatus = "FAILED"
	}
}

// ---------- helpers ----------

func fileExists(p string) bool {
	if p == "" {
		return false
	}
	info, err := os.Stat(p)
	return err == nil && !info.IsDir()
}

func boolParam(m map[string]any, key string) bool {
	if v, ok := m[key]; ok {
		switch t := v.(type) {
		case bool:
			return t
		case string:
			return t == "true" || t == "1"
		}
	}
	return false
}

func coerceInt(v any) (int, bool) {
	switch t := v.(type) {
	case int:
		return t, true
	case int64:
		return int(t), true
	case float64:
		return int(t), true
	case string:
		var n int
		if _, err := fmt.Sscanf(t, "%d", &n); err == nil {
			return n, true
		}
	}
	return 0, false
}

func coerceCredentialsList(v any) []vault.Credential {
	arr, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]vault.Credential, 0, len(arr))
	for _, e := range arr {
		m, ok := e.(map[string]any)
		if !ok {
			continue
		}
		id, _ := m["id"].(string)
		label, _ := m["label"].(string)
		pass, _ := m["password"].(string)
		out = append(out, vault.Credential{ID: id, Label: label, Password: pass})
	}
	return out
}

func writePasswordFile(password string) (string, error) {
	f, err := os.CreateTemp(config.TempDir, "vault_pass_*.txt")
	if err != nil {
		return "", err
	}
	if !strings.HasSuffix(password, "\n") {
		password += "\n"
	}
	if _, err := f.WriteString(password); err != nil {
		f.Close()
		return "", err
	}
	f.Close()
	_ = os.Chmod(f.Name(), 0o600)
	return f.Name(), nil
}

func resolveAnsibleConfig(projectDir, ansibleConfig string) string {
	repoCfg := filepath.Join(projectDir, "repo", "ansible.cfg")
	if ansibleConfig == "" {
		if fileExists(repoCfg) {
			return repoCfg
		}
		return ""
	}
	if strings.HasPrefix(ansibleConfig, "ansible-config/") {
		p := filepath.Join(projectDir, ansibleConfig)
		if fileExists(p) {
			return p
		}
		return ""
	}
	if ansibleConfig == "ansible.cfg" || strings.HasSuffix(ansibleConfig, ".cfg") {
		if fileExists(repoCfg) {
			return repoCfg
		}
		p := filepath.Join(projectDir, "ansible-config", ansibleConfig)
		if fileExists(p) {
			return p
		}
		return ""
	}
	p := filepath.Join(projectDir, "ansible-config", ansibleConfig)
	if fileExists(p) {
		return p
	}
	return ""
}

// runNeedsVaultCredentials replicates _run_needs_vault_credentials from the
// Python worker: playbook or inventory group_vars/host_vars contain
// $ANSIBLE_VAULT.
func runNeedsVaultCredentials(playbookPath string, inventoryArgs []string) bool {
	if b, err := os.ReadFile(playbookPath); err == nil {
		s := string(b)
		if strings.Contains(s, "$ANSIBLE_VAULT") || strings.Contains(s, "vars_files:") {
			return true
		}
	}
	// Walk inventory host_vars/group_vars for encrypted files.
	var invPaths []string
	for i := 0; i < len(inventoryArgs)-1; i++ {
		if inventoryArgs[i] == "-i" {
			invPaths = append(invPaths, inventoryArgs[i+1])
		}
	}
	for _, ip := range invPaths {
		info, err := os.Stat(ip)
		if err != nil {
			continue
		}
		var roots []string
		if info.IsDir() {
			roots = []string{filepath.Join(ip, "group_vars"), filepath.Join(ip, "host_vars")}
		} else {
			roots = []string{filepath.Join(filepath.Dir(ip), "group_vars"), filepath.Join(filepath.Dir(ip), "host_vars")}
		}
		for _, root := range roots {
			found := false
			_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
				if err != nil || info.IsDir() {
					return nil
				}
				name := strings.ToLower(info.Name())
				if !strings.HasSuffix(name, ".yml") && !strings.HasSuffix(name, ".yaml") {
					return nil
				}
				b, err := os.ReadFile(path)
				if err != nil {
					return nil
				}
				if strings.HasPrefix(strings.TrimSpace(string(b)), "$ANSIBLE_VAULT") {
					found = true
				}
				return nil
			})
			if found {
				return true
			}
		}
	}
	return false
}

var deprecatedAnsibleCallbacks = map[string]bool{
	"profile_tasks":               true,
	"ansible.posix.profile_tasks": true,
}

func sanitizeAnsibleRuntimeEnv(env []string, cfgPath string) []string {
	// Ensure ANSIBLE_DEPRECATION_WARNINGS=False and Python interpreter default.
	haveDepr := false
	havePy := false
	for _, kv := range env {
		if strings.HasPrefix(kv, "ANSIBLE_DEPRECATION_WARNINGS=") {
			haveDepr = true
		}
		if strings.HasPrefix(kv, "ANSIBLE_PYTHON_INTERPRETER=") {
			havePy = true
		}
	}
	if !haveDepr {
		env = append(env, "ANSIBLE_DEPRECATION_WARNINGS=False")
	}
	if !havePy {
		env = append(env, "ANSIBLE_PYTHON_INTERPRETER=/usr/bin/python3")
	}

	// Strip profile_tasks from ANSIBLE_CALLBACKS_ENABLED.
	for i, kv := range env {
		if !strings.HasPrefix(kv, "ANSIBLE_CALLBACKS_ENABLED=") {
			continue
		}
		val := strings.TrimPrefix(kv, "ANSIBLE_CALLBACKS_ENABLED=")
		parts := strings.Split(val, ",")
		var kept []string
		for _, p := range parts {
			p = strings.TrimSpace(strings.SplitN(p, "#", 2)[0])
			if p == "" || deprecatedAnsibleCallbacks[p] {
				continue
			}
			kept = append(kept, p)
		}
		env[i] = "ANSIBLE_CALLBACKS_ENABLED=" + strings.Join(kept, ",")
		return env
	}

	// If ansible.cfg has callbacks_enabled with profile_tasks, override via env.
	if cfgPath != "" {
		if val, ok := readAnsibleCfgCallbacks(cfgPath); ok {
			parts := strings.Split(val, ",")
			var kept []string
			changed := false
			for _, p := range parts {
				p = strings.TrimSpace(strings.SplitN(p, "#", 2)[0])
				if p == "" {
					continue
				}
				if deprecatedAnsibleCallbacks[p] {
					changed = true
					continue
				}
				kept = append(kept, p)
			}
			if changed {
				env = append(env, "ANSIBLE_CALLBACKS_ENABLED="+strings.Join(kept, ","))
			}
		}
	}
	return env
}

func readAnsibleCfgCallbacks(path string) (string, bool) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", false
	}
	inDefaults := false
	for _, line := range strings.Split(string(b), "\n") {
		s := strings.TrimSpace(line)
		if s == "" || strings.HasPrefix(s, "#") || strings.HasPrefix(s, ";") {
			continue
		}
		if strings.HasPrefix(s, "[") {
			end := strings.Index(s, "]")
			if end > 0 {
				inDefaults = strings.EqualFold(strings.TrimSpace(s[1:end]), "defaults")
			}
			continue
		}
		if inDefaults {
			if eq := strings.Index(s, "="); eq > 0 {
				k := strings.TrimSpace(s[:eq])
				if strings.EqualFold(k, "callbacks_enabled") {
					return strings.TrimSpace(s[eq+1:]), true
				}
			}
		}
	}
	return "", false
}

// injectDockerPreflight patches a generated playbook so community.docker
// modules get python3-docker/python3-requests installed on the target first.
// Only patches if the playbook mentions docker modules and doesn't already
// have the preflight task.
func injectDockerPreflight(playbookPath string) bool {
	b, err := os.ReadFile(playbookPath)
	if err != nil {
		return false
	}
	body := string(b)
	needsPatch := false
	markers := []string{
		"community.docker.", "docker_login:", "docker_image:",
		"docker_container:", "docker_network:", "docker_volume:",
	}
	for _, m := range markers {
		if strings.Contains(body, m) {
			needsPatch = true
			break
		}
	}
	if !needsPatch {
		return false
	}
	if strings.Contains(body, "OpenSible preflight | Ensure Python deps for community.docker") {
		return false
	}
	// We only append a task-shape header note; playbook may not be trivially
	// mutable via string append without breaking YAML. Rather than a full YAML
	// rewrite we emit a warning log and skip mutation — the backend template
	// generator now ships the same preflight itself, so this only affects
	// legacy saved jobs.
	logging.L().Warn("Playbook uses community.docker modules but preflight not present; consider re-saving job", "playbook", playbookPath)
	return false
}
