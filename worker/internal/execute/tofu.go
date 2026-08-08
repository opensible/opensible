// OpenTofu execution for Cloud Provisioning stacks.
// Mirrors _execute_tofu_run and related helpers in worker/execute.py.
package execute

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/opensible/worker-go/internal/httpclient"
	"github.com/opensible/worker-go/internal/logging"
)

var tofuInstallMu sync.Mutex

func tofuArgs(action string) ([]string, error) {
	switch strings.ToLower(action) {
	case "init":
		return []string{"tofu", "init", "-input=false", "-no-color", "-reconfigure"}, nil
	case "plan":
		return []string{"tofu", "plan", "-input=false", "-no-color", "-out=tfplan"}, nil
	case "apply":
		return []string{"tofu", "apply", "-input=false", "-no-color", "-auto-approve"}, nil
	case "destroy":
		return []string{"tofu", "destroy", "-input=false", "-no-color", "-auto-approve"}, nil
	case "validate":
		return []string{"tofu", "validate", "-no-color"}, nil
	case "fmt":
		return []string{"tofu", "fmt", "-recursive"}, nil
	case "refresh":
		return []string{"tofu", "apply", "-refresh-only", "-input=false", "-no-color", "-auto-approve"}, nil
	case "drift":
		// Read-only drift detection. -detailed-exitcode: 0 = in sync,
		// 2 = drift detected, 1 = error. State is never written.
		return []string{"tofu", "plan", "-refresh-only", "-input=false", "-no-color", "-detailed-exitcode"}, nil
	}
	return nil, errors.New("unsupported tofu action: " + action)
}

func ensureTofuInstalled(log func(string)) bool {
	if _, err := exec.LookPath("tofu"); err == nil {
		return true
	}
	tofuInstallMu.Lock()
	defer tofuInstallMu.Unlock()
	if _, err := exec.LookPath("tofu"); err == nil {
		return true
	}
	arch := ""
	switch os.Getenv("HOSTTYPE") {
	}
	// Detect via uname -m equivalent
	uname, _ := exec.Command("uname", "-m").Output()
	m := strings.TrimSpace(string(uname))
	switch m {
	case "x86_64", "amd64":
		arch = "amd64"
	case "aarch64", "arm64":
		arch = "arm64"
	default:
		log("ERROR: unsupported CPU arch for auto-install: " + m + "\n")
		return false
	}
	version := os.Getenv("OPENTOFU_VERSION")
	if version == "" {
		version = "1.8.4"
	}
	url := fmt.Sprintf("https://github.com/opentofu/opentofu/releases/download/v%s/tofu_%s_linux_%s.zip", version, version, arch)
	home, _ := os.UserHomeDir()
	targets := []string{"/usr/local/bin", filepath.Join(home, ".local/bin"), "/tmp/tofu-bin"}
	for _, dir := range targets {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			log(fmt.Sprintf("[auto-install] failed in %s: %v\n", dir, err))
			continue
		}
		zipPath := filepath.Join(dir, "_tofu.zip")
		log(fmt.Sprintf("[auto-install] downloading OpenTofu %s (%s) to %s...\n", version, arch, dir))
		if err := download(url, zipPath); err != nil {
			log(fmt.Sprintf("[auto-install] failed in %s: %v\n", dir, err))
			continue
		}
		if err := exec.Command("unzip", "-o", zipPath, "-d", dir, "tofu").Run(); err != nil {
			log(fmt.Sprintf("[auto-install] failed in %s (unzip): %v\n", dir, err))
			_ = os.Remove(zipPath)
			continue
		}
		_ = os.Remove(zipPath)
		_ = os.Chmod(filepath.Join(dir, "tofu"), 0o755)
		os.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
		if _, err := exec.LookPath("tofu"); err == nil {
			log(fmt.Sprintf("[auto-install] OpenTofu %s installed at %s/tofu\n\n", version, dir))
			return true
		}
	}
	return false
}

func download(url, dst string) error {
	resp, err := http.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("http %d", resp.StatusCode)
	}
	f, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = io.Copy(f, resp.Body)
	return err
}

func hclQuote(s string) string {
	esc := strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(s)
	return `"` + esc + `"`
}

// templateOwnedFiles are always overwritten from
// /app/IaC/opentofu-<provider>/envs/_template on every sync so bundled IaC
// updates land in the stack dir even if the user hasn't touched them.
var templateOwnedFiles = map[string]bool{
	"main.tf": true, "variables.tf": true, "providers.tf": true, "versions.tf": true,
	"backend.tf": true, "README.md": true, "credentials.auto.tfvars.example": true,
}

func syncIaCIntoStack(stackDir, provider string, log func(string)) {
	if provider == "" {
		provider = "bytedc"
	}
	provider = strings.ToLower(provider)
	srcRoot := "/app/IaC/opentofu-" + provider
	srcModules := filepath.Join(srcRoot, "modules")
	srcTemplate := filepath.Join(srcRoot, "envs", "_template")
	if _, err := os.Stat(srcRoot); err != nil {
		log("[sync] skipped — bundled IaC not found at " + srcRoot + ". Rebuild the worker image to pick up module updates.\n")
		return
	}

	// Provider-change detection: if this stack dir was previously initialised
	// against a different provider (e.g. bytedc → hetzner), the cached
	// .terraform/ plugins and .terraform.lock.hcl still pin the old provider,
	// so `tofu init -reconfigure` happily "reuses" huaweicloud/hcs. Wipe the
	// per-stack init cache when the provider marker changes.
	markerPath := filepath.Join(stackDir, ".opensible-provider")
	prev := ""
	if b, err := os.ReadFile(markerPath); err == nil {
		prev = strings.TrimSpace(string(b))
	}
	if prev != "" && prev != provider {
		log(fmt.Sprintf("[sync] provider changed (%s → %s); clearing .terraform cache and lock file.\n", prev, provider))
		_ = os.RemoveAll(filepath.Join(stackDir, ".terraform"))
		_ = os.Remove(filepath.Join(stackDir, ".terraform.lock.hcl"))
		_ = os.Remove(filepath.Join(stackDir, "tfplan"))
	}
	_ = os.WriteFile(markerPath, []byte(provider+"\n"), 0o644)

	// stack_dir → envs/<stack>; go two levels up to get stacks_root.
	// Modules are namespaced per provider so multiple provider stacks in the
	// same project don't fight over stacksRoot/modules.
	stacksRoot := filepath.Dir(filepath.Dir(stackDir))
	modulesDirName := "modules-" + provider
	if _, err := os.Stat(srcModules); err == nil {
		dstModules := filepath.Join(stacksRoot, modulesDirName)
		_ = os.RemoveAll(dstModules)
		if err := copyTree(srcModules, dstModules); err != nil {
			log(fmt.Sprintf("[sync] WARNING: failed to refresh modules: %v\n", err))
			return
		}
		// Back-compat symlink/copy so existing templates that reference
		// ../../modules keep working when only one provider is in use.
		// Skip when another provider's marker exists anywhere else under
		// stacksRoot — otherwise a Hetzner run would clobber ByteDC's
		// legacy modules/ dir (or vice-versa) in mixed-provider projects.
		if otherProviderPresent(stacksRoot, stackDir, provider) {
			log(fmt.Sprintf("[sync] skipped legacy modules/ copy: another provider stack exists in %s (using provider-namespaced %s/ only)\n", stacksRoot, modulesDirName))
		} else {
			legacyModules := filepath.Join(stacksRoot, "modules")
			_ = os.RemoveAll(legacyModules)
			if err := copyTree(dstModules, legacyModules); err != nil {
				log(fmt.Sprintf("[sync] WARNING: failed to refresh legacy modules/: %v\n", err))
			}
		}
	}
	copied := 0
	if _, err := os.Stat(srcTemplate); err == nil {
		for fname := range templateOwnedFiles {
			sp := filepath.Join(srcTemplate, fname)
			if _, err := os.Stat(sp); err != nil {
				continue
			}
			b, err := os.ReadFile(sp)
			if err != nil {
				continue
			}
			_ = os.WriteFile(filepath.Join(stackDir, fname), b, 0o644)
			copied++
		}
	}
	log(fmt.Sprintf("[sync] refreshed %s modules/ and %d template .tf files in %s\n", provider, copied, stackDir))
}

// otherProviderPresent walks stacksRoot/envs/* and returns true if any stack
// dir other than the current one has an .opensible-provider marker pointing at
// a different provider. Used to decide whether it's safe to touch the shared
// legacy stacksRoot/modules path.
func otherProviderPresent(stacksRoot, currentStackDir, currentProvider string) bool {
	envsRoot := filepath.Join(stacksRoot, "envs")
	entries, err := os.ReadDir(envsRoot)
	if err != nil {
		return false
	}
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		sd := filepath.Join(envsRoot, e.Name())
		if sd == currentStackDir {
			continue
		}
		b, err := os.ReadFile(filepath.Join(sd, ".opensible-provider"))
		if err != nil {
			continue
		}
		if p := strings.TrimSpace(string(b)); p != "" && p != currentProvider {
			return true
		}
	}
	return false
}

// executeTofuRun runs an OpenTofu action for a Cloud Provisioning stack.
func executeTofuRun(executionID string, execData map[string]any, projectID string, client *httpclient.Client) {
	_ = logging.L()
	runParams, _ := execData["runParams"].(map[string]any)
	action, _ := runParams["tofu_action"].(string)
	stackName, _ := runParams["stack_name"].(string)
	stackDir, _ := runParams["stack_dir"].(string)
	secrets, _ := runParams["secrets"].(map[string]any)
	secretKeysRaw := runParams["secret_keys"]
	extraEnv, _ := runParams["env"].(map[string]any)
	provider, _ := runParams["provider"].(string)

	sendLog := func(text string) { client.SendLog(executionID, text, 0) }
	startTime := time.Now()
	finalStatus := "FAILED"
	var returnCode *int
	var credsPath string
	// Policy gate: nil unless the backend shipped an opted-in policy payload.
	policyCfg := parsePolicyConfig(runParams["policy"])
	var policyRes *PolicyResult

	defer func() {
		if credsPath != "" {
			_ = os.Remove(credsPath)
		}
		duration := int(time.Since(startTime).Seconds())
		result := map[string]any{"tofu_action": action, "stack": stackName}
		if policyRes != nil {
			result["policy"] = policyRes
		}
		client.FinishExecution(executionID, finalStatus, float64(time.Now().Unix()), duration, returnCode, "", result)
	}()

	client.Heartbeat(executionID)

	if stackDir == "" {
		sendLog("ERROR: stack directory missing from runParams.\n")
		rc := 2
		returnCode = &rc
		return
	}
	if _, err := os.Stat(stackDir); err != nil {
		sendLog(fmt.Sprintf("ERROR: stack directory not found on worker: %s\n", stackDir))
		rc := 2
		returnCode = &rc
		return
	}

	syncIaCIntoStack(stackDir, provider, sendLog)

	cmdArgs, err := tofuArgs(action)
	if err != nil {
		sendLog("ERROR: " + err.Error() + "\n")
		rc := 2
		returnCode = &rc
		return
	}
	sendLog(fmt.Sprintf("$ cd %s\n$ %s\n\n", stackDir, strings.Join(cmdArgs, " ")))

	if _, err := exec.LookPath("tofu"); err != nil {
		if !ensureTofuInstalled(sendLog) {
			sendLog("ERROR: `tofu` binary not found and auto-install failed.\n")
			rc := 127
			returnCode = &rc
			return
		}
	}

	// Materialize credentials.auto.tfvars.
	if len(secrets) > 0 {
		var keys []string
		switch v := secretKeysRaw.(type) {
		case []any:
			for _, e := range v {
				if s, ok := e.(string); ok {
					keys = append(keys, s)
				}
			}
		}
		if len(keys) == 0 {
			for k := range secrets {
				keys = append(keys, k)
			}
		}
		var body []string
		for _, k := range keys {
			if v, ok := secrets[k]; ok {
				if s, ok := v.(string); ok && s != "" {
					// Trim surrounding whitespace/newlines from pasted secrets
					// (e.g. AWS access/secret keys) so they don't corrupt
					// downstream signatures or auth headers. Preserve inner
					// newlines for multi-line secrets like kubeconfig.
					trimmed := strings.Trim(s, " \t\r\n")
					if trimmed == "" {
						continue
					}
					body = append(body, k+" = "+hclQuote(trimmed))
				}
			}
		}
		if len(body) > 0 {
			credsPath = filepath.Join(stackDir, "credentials.auto.tfvars")
			if err := os.WriteFile(credsPath, []byte(strings.Join(body, "\n")+"\n"), 0o600); err == nil {
				_ = os.Chmod(credsPath, 0o600)
			}
		}
	}

	env := os.Environ()
	for k, v := range extraEnv {
		if v == nil {
			continue
		}
		env = append(env, k+"="+fmt.Sprint(v))
	}

	// ---- Policy-as-code pre-flight gate -----------------------------------
	if policyCfg != nil && (strings.EqualFold(action, "apply") || strings.EqualFold(action, "destroy")) {
		sendLog("[policy] gate enabled — running a speculative plan before " + strings.ToLower(action) + "...\n")
		planFile := ".opensible-policy.tfplan"
		pargs := []string{"plan", "-input=false", "-no-color", "-out=" + planFile}
		if strings.EqualFold(action, "destroy") {
			pargs = append(pargs, "-destroy")
		}
		pctx, pcancel := context.WithTimeout(context.Background(), 20*time.Minute)
		pcmd := exec.CommandContext(pctx, "tofu", pargs...)
		pcmd.Dir = stackDir
		pcmd.Env = env
		pout, perr := pcmd.CombinedOutput()
		pcancel()
		if perr != nil {
			sendLog(string(pout))
			sendLog("[policy] ERROR: speculative plan failed — cannot evaluate policy, aborting.\n")
			rc := 1
			returnCode = &rc
			_ = os.Remove(filepath.Join(stackDir, planFile))
			return
		}
		raw, serr := showPlanJSON(stackDir, planFile, env)
		_ = os.Remove(filepath.Join(stackDir, planFile))
		if serr != nil {
			sendLog("[policy] ERROR: `tofu show -json` failed: " + serr.Error() + " — aborting.\n")
			rc := 1
			returnCode = &rc
			return
		}
		res, eerr := evaluatePolicy(raw, policyCfg)
		if eerr != nil {
			sendLog("[policy] ERROR: could not parse plan JSON: " + eerr.Error() + " — aborting.\n")
			rc := 1
			returnCode = &rc
			return
		}
		policyRes = res
		sendLog(formatPolicyReport(res))
		if res.Blocked {
			finalStatus = "FAILED"
			rc := 3
			returnCode = &rc
			return
		}
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	// Heartbeat.
	go func() {
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

	cmd := exec.Command(cmdArgs[0], cmdArgs[1:]...)
	cmd.Dir = stackDir
	cmd.Env = env
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		sendLog("ERROR: " + err.Error() + "\n")
		rc := 1
		returnCode = &rc
		return
	}
	cmd.Stderr = cmd.Stdout
	if err := cmd.Start(); err != nil {
		if errors.Is(err, exec.ErrNotFound) {
			sendLog("ERROR: `tofu` binary not found on PATH of this worker.\n")
			rc := 127
			returnCode = &rc
			return
		}
		sendLog("ERROR: " + err.Error() + "\n")
		rc := 1
		returnCode = &rc
		return
	}

	// Stream + poll cancel.
	done := make(chan error, 1)
	go func() {
		buf := make([]byte, 4096)
		for {
			n, rerr := stdout.Read(buf)
			if n > 0 {
				sendLog(string(buf[:n]))
			}
			if rerr != nil {
				done <- cmd.Wait()
				return
			}
		}
	}()

	cancelTicker := time.NewTicker(2 * time.Second)
	defer cancelTicker.Stop()
	killed := false
loop:
	for {
		select {
		case werr := <-done:
			if werr != nil {
				if ee, ok := werr.(*exec.ExitError); ok {
					rc := ee.ExitCode()
					returnCode = &rc
				} else {
					rc := 1
					returnCode = &rc
				}
			} else {
				rc := 0
				returnCode = &rc
			}
			break loop
		case <-cancelTicker.C:
			st := client.GetExecutionStatus(executionID, projectID)
			up := strings.ToUpper(st)
			if up == "CANCELING" || up == "CANCELED" {
				sendLog("\n[cancel requested — terminating tofu process]\n")
				killGroup(cmd.Process.Pid, syscall.SIGTERM)
				killed = true
				grace := time.NewTimer(10 * time.Second)
				select {
				case werr := <-done:
					if werr != nil {
						if ee, ok := werr.(*exec.ExitError); ok {
							rc := ee.ExitCode()
							returnCode = &rc
						} else {
							rc := 1
							returnCode = &rc
						}
					}
					grace.Stop()
				case <-grace.C:
					killGroup(cmd.Process.Pid, syscall.SIGKILL)
					_ = cmd.Wait()
					rc := 137
					returnCode = &rc
				}
				break loop
			}
		}
	}
	if returnCode != nil && *returnCode == 0 {
		finalStatus = "SUCCESS"
	}
	// `drift` runs `tofu plan -detailed-exitcode`, where exit 2 means
	// "changes detected" — a successful check, not a failure.
	if strings.EqualFold(action, "drift") && returnCode != nil {
		switch *returnCode {
		case 0:
			finalStatus = "SUCCESS"
			sendLog("\n[drift] no drift detected — infrastructure matches state.\n")
		case 2:
			finalStatus = "SUCCESS"
			sendLog("\n[drift] DRIFT DETECTED — the live infrastructure differs from the recorded state (see plan above).\n")
		}
	}
	// Policy evaluation for a plain `plan` run: reuse the tfplan that was just
	// written, so the only extra work is one local `tofu show -json`.
	if policyCfg != nil && strings.EqualFold(action, "plan") && finalStatus == "SUCCESS" && !killed {
		if raw, serr := showPlanJSON(stackDir, "tfplan", env); serr == nil {
			if res, eerr := evaluatePolicy(raw, policyCfg); eerr == nil {
				policyRes = res
				sendLog(formatPolicyReport(res))
				if res.Blocked {
					finalStatus = "FAILED"
					rc := 3
					returnCode = &rc
				}
			} else {
				sendLog("[policy] WARNING: could not parse plan JSON: " + eerr.Error() + "\n")
			}
		} else {
			sendLog("[policy] WARNING: `tofu show -json tfplan` failed: " + serr.Error() + "\n")
		}
	}
	if killed {
		finalStatus = "CANCELED"
	}

	// Snapshot tfstate on success.
	if finalStatus == "SUCCESS" && (action == "apply" || action == "destroy" || action == "refresh" || action == "plan") {
		snapCtx, snapCancel := context.WithTimeout(context.Background(), 60*time.Second)
		defer snapCancel()
		snap := exec.CommandContext(snapCtx, "tofu", "state", "pull")
		snap.Dir = stackDir
		snap.Env = env
		out, err := snap.Output()
		if err == nil && strings.TrimSpace(string(out)) != "" {
			path := filepath.Join(stackDir, "terraform.tfstate.json")
			_ = os.WriteFile(path, out, 0o644)
			sendLog(fmt.Sprintf("\n[snapshot] wrote %s (%d bytes)\n", filepath.Base(path), len(out)))
		} else {
			msg := "unknown"
			if err != nil {
				msg = err.Error()
			}
			sendLog("\n[snapshot] tofu state pull failed: " + msg + "\n")
		}
	}
	_ = strconv.Itoa(0)
}

func killGroup(pid int, sig syscall.Signal) {
	pgid, err := syscall.Getpgid(pid)
	if err == nil {
		_ = syscall.Kill(-pgid, sig)
		return
	}
	_ = syscall.Kill(pid, sig)
}
