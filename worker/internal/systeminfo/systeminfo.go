// Package systeminfo collects OS/Python/Ansible/etc metadata about the worker.
// Mirrors worker/system_info.py.
package systeminfo

import (
	"os"
	"os/exec"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// Collect returns a map matching the Python worker's schema.
func Collect() map[string]any {
	return map[string]any{
		"os":       osInfo(),
		"python":   pythonInfo(),
		"ansible":  ansibleInfo(),
		"sshpass":  sshpassInfo(),
		"openssh":  opensshInfo(),
		"mitogen":  mitogenInfo(),
		"cpu":      cpuInfo(),
		"memory":   memoryInfo(),
		"hostname": hostname(),
	}
}

func hostname() string {
	if h, err := os.Hostname(); err == nil {
		return h
	}
	return "Unknown"
}

func osInfo() map[string]string {
	name := runtime.GOOS
	arch := runtime.GOARCH
	kernel := ""
	if out, err := runCmd(2*time.Second, "uname", "-r"); err == nil {
		kernel = strings.TrimSpace(out)
	}
	full := ""
	if out, err := runCmd(2*time.Second, "uname", "-v"); err == nil {
		full = strings.TrimSpace(out)
	}
	// Improve name on Linux via /etc/os-release
	if name == "linux" {
		if b, err := os.ReadFile("/etc/os-release"); err == nil {
			for _, line := range strings.Split(string(b), "\n") {
				if strings.HasPrefix(line, "PRETTY_NAME=") {
					name = strings.Trim(strings.TrimPrefix(line, "PRETTY_NAME="), `"`)
					break
				}
			}
		}
	}
	return map[string]string{"name": name, "version": kernel, "kernel": full, "arch": arch}
}

func pythonInfo() map[string]string {
	execPath, _ := exec.LookPath("python3")
	if execPath == "" {
		execPath, _ = exec.LookPath("python")
	}
	version := "Unknown"
	if execPath != "" {
		if out, err := runCmd(3*time.Second, execPath, "-V"); err == nil {
			parts := strings.Fields(strings.TrimSpace(out))
			if len(parts) >= 2 {
				version = parts[1]
			}
		}
	}
	if execPath == "" {
		return map[string]string{"version": "Not installed", "implementation": "Unknown", "path": "Not found"}
	}
	return map[string]string{"version": version, "implementation": "CPython", "path": execPath}
}

func ansibleInfo() map[string]string {
	path, err := exec.LookPath("ansible-playbook")
	if err != nil {
		return map[string]string{"version": "Not installed", "executable": "Not found"}
	}
	version := "Unknown"
	if out, err := runCmd(5*time.Second, "ansible-playbook", "--version"); err == nil {
		first := strings.SplitN(out, "\n", 2)[0]
		if i := strings.Index(first, "["); i >= 0 {
			if j := strings.Index(first[i:], "]"); j > 0 {
				inside := first[i+1 : i+j]
				parts := strings.Fields(inside)
				if len(parts) > 1 {
					version = parts[len(parts)-1]
				}
			}
		}
	}
	return map[string]string{"version": version, "executable": path}
}

var verRe = regexp.MustCompile(`(\d+\.\d+(?:\.\d+)?)`)

func sshpassInfo() map[string]string {
	path, err := exec.LookPath("sshpass")
	if err != nil {
		return map[string]string{"version": "Not installed", "executable": "Not found"}
	}
	out, _ := runCmd(3*time.Second, "sshpass", "-V")
	version := "Unknown"
	if m := verRe.FindString(out); m != "" {
		version = m
	}
	return map[string]string{"version": version, "executable": path}
}

var opensshRe = regexp.MustCompile(`OpenSSH[_-](\d+\.\d+(?:p\d+)?)`)

func opensshInfo() map[string]string {
	path, err := exec.LookPath("ssh")
	if err != nil {
		return map[string]string{"version": "Not installed", "executable": "Not found"}
	}
	out, _ := runCmd(3*time.Second, "ssh", "-V") // ssh -V writes to stderr, runCmd merges
	version := "Unknown"
	if m := opensshRe.FindStringSubmatch(out); len(m) >= 2 {
		version = m[1]
	}
	return map[string]string{"version": version, "executable": path}
}

func mitogenInfo() map[string]string {
	out, err := runCmd(5*time.Second, "pip", "show", "mitogen")
	if err != nil {
		return map[string]string{"version": "Not installed", "executable": "Not found"}
	}
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, "Version:") {
			return map[string]string{"version": strings.TrimSpace(strings.TrimPrefix(line, "Version:")), "executable": "Python module"}
		}
	}
	return map[string]string{"version": "Not installed", "executable": "Not found"}
}

func cpuInfo() map[string]any {
	cores := runtime.NumCPU()
	model := ""
	if b, err := os.ReadFile("/proc/cpuinfo"); err == nil {
		for _, line := range strings.Split(string(b), "\n") {
			if strings.HasPrefix(line, "model name") {
				parts := strings.SplitN(line, ":", 2)
				if len(parts) == 2 {
					model = strings.TrimSpace(parts[1])
				}
				break
			}
		}
	}
	if model == "" {
		model = runtime.GOARCH
	}
	return map[string]any{"cores": cores, "model": model}
}

func memoryInfo() map[string]any {
	b, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return map[string]any{"total_mb": 0}
	}
	for _, line := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(line, "MemTotal:") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				if kb, err := strconv.ParseInt(fields[1], 10, 64); err == nil {
					return map[string]any{"total_mb": int(kb / 1024)}
				}
			}
		}
	}
	return map[string]any{"total_mb": 0}
}

// MitogenStrategyPluginsPath tries to find the ansible_mitogen strategy plugins.
func MitogenStrategyPluginsPath() string {
	// Try a python-side lookup so we get whatever site-packages actually has.
	code := `
import os, sys
try:
    import ansible_mitogen
    p = os.path.join(os.path.dirname(ansible_mitogen.__file__), 'plugins', 'strategy')
    print(p if os.path.isdir(p) else '', end='')
    sys.exit(0)
except Exception:
    pass
try:
    import mitogen
    p = os.path.join(os.path.dirname(mitogen.__file__), 'ansible_mitogen', 'plugins', 'strategy')
    print(p if os.path.isdir(p) else '', end='')
except Exception:
    print('', end='')
`
	if out, err := runCmd(3*time.Second, "python3", "-c", code); err == nil {
		p := strings.TrimSpace(out)
		if p != "" {
			if info, err := os.Stat(p); err == nil && info.IsDir() {
				return p
			}
		}
	}
	return ""
}

func runCmd(timeout time.Duration, name string, args ...string) (string, error) {
	// simple: capture combined output; use a timeout via context in caller if needed.
	cmd := exec.Command(name, args...)
	out, err := cmd.CombinedOutput()
	_ = timeout // reserved for future use; short subcommands here are fine.
	return string(out), err
}
