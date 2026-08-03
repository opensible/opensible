// Result extraction from ansible output for HOST_CHECK / HOST_FACTS.
// Mirrors extract_result_from_output / _parse_play_recap_per_host in
// worker/execute.py.
package execute

import (
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
)

var recapLineRe = regexp.MustCompile(`^(\S+)\s*:\s*(?:ok[=:](\d+))?\s*(?:changed[=:](\d+))?\s*(?:unreachable[=:](\d+))?\s*(?:failed[=:](\d+))?`)

// parsePlayRecapPerHost returns a map hostname → "online"/"offline".
func parsePlayRecapPerHost(output string) map[string]string {
	lines := strings.Split(output, "\n")
	start := -1
	for i, l := range lines {
		if strings.Contains(l, "PLAY RECAP") {
			start = i
			break
		}
	}
	if start < 0 {
		return nil
	}
	hosts := map[string]string{}
	end := start + 50
	if end > len(lines) {
		end = len(lines)
	}
	for i := start + 1; i < end; i++ {
		line := strings.TrimSpace(lines[i])
		if line == "" || strings.HasPrefix(line, "PLAY") || strings.HasPrefix(line, "TASK") {
			break
		}
		m := recapLineRe.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		ok, _ := strconv.Atoi(m[2])
		unreach, _ := strconv.Atoi(m[4])
		failed, _ := strconv.Atoi(m[5])
		if unreach == 0 && failed == 0 && ok >= 1 {
			hosts[m[1]] = "online"
		} else {
			hosts[m[1]] = "offline"
		}
	}
	if len(hosts) == 0 {
		return nil
	}
	return hosts
}

var (
	failedCountRe    = regexp.MustCompile(`failed\s*=\s*(\d+)`)
	unreachCountRe   = regexp.MustCompile(`unreachable\s*=\s*(\d+)`)
	okCountRe        = regexp.MustCompile(`ok\s*=\s*(\d+)`)
	unreachableJSON  = regexp.MustCompile(`(?s)UNREACHABLE!\s*=>\s*(\{.*?\})`)
	failedJSON       = regexp.MustCompile(`(?s)FAILED!\s*=>\s*(\{.*?\})`)
	successBraceJSON = regexp.MustCompile(`(?s)SUCCESS\s*=>\s*(\{.*?\})`)
)

// ExtractResult mirrors the Python extract_result_from_output.
func ExtractResult(output, executionType string, returnCode int, runParams map[string]any) map[string]any {
	switch executionType {
	case "HOST_CHECK":
		hostsList := coerceStringList(runParams["hosts"])
		if len(hostsList) > 1 {
			per := parsePlayRecapPerHost(output)
			if per != nil {
				return map[string]any{
					"hosts":   per,
					"message": "Checked " + strconv.Itoa(len(per)) + " host(s)",
				}
			}
			result := map[string]string{}
			state := "offline"
			if returnCode == 0 {
				state = "online"
			}
			for _, h := range hostsList {
				result[h] = state
			}
			msg := "Could not parse per-host result from output"
			if returnCode == 0 {
				msg = "Checked " + strconv.Itoa(len(hostsList)) + " host(s)"
			}
			return map[string]any{"hosts": result, "message": msg}
		}
		var failedCount, unreachCount *int
		if m := failedCountRe.FindStringSubmatch(output); m != nil {
			n, _ := strconv.Atoi(m[1])
			failedCount = &n
		}
		if m := unreachCountRe.FindStringSubmatch(output); m != nil {
			n, _ := strconv.Atoi(m[1])
			unreachCount = &n
		}
		if failedCount != nil {
			if *failedCount == 0 && (unreachCount == nil || *unreachCount == 0) {
				return map[string]any{"available": true, "message": "Host is available and ready to execute commands"}
			}
			ur := 0
			if unreachCount != nil {
				ur = *unreachCount
			}
			return map[string]any{"available": false, "message": "Host is unavailable (failed=" + strconv.Itoa(*failedCount) + ", unreachable=" + strconv.Itoa(ur) + ")"}
		}
		if returnCode == 0 {
			return map[string]any{"available": true, "message": "Host is available and ready to execute commands"}
		}
		return map[string]any{"available": false, "message": "Host is unavailable (ansible return code: " + strconv.Itoa(returnCode) + ")"}
	case "HOST_FACTS":
		if returnCode != 0 {
			for _, re := range []*regexp.Regexp{unreachableJSON, failedJSON} {
				if m := re.FindStringSubmatch(output); m != nil {
					var obj map[string]any
					if err := json.Unmarshal([]byte(m[1]), &obj); err == nil {
						msg, _ := obj["msg"].(string)
						if msg == "" {
							msg, _ = obj["stderr"].(string)
						}
						if msg == "" {
							msg, _ = obj["stdout"].(string)
						}
						if strings.TrimSpace(msg) != "" {
							ur, _ := obj["unreachable"].(bool)
							return map[string]any{"error": msg, "return_code": returnCode, "unreachable": ur}
						}
					}
				}
			}
			return map[string]any{"error": "Ansible failed (return code: " + strconv.Itoa(returnCode) + ")", "return_code": returnCode}
		}
		// Try the '"msg": "<escaped JSON>"' pattern first.
		if facts := extractFactsFromMsg(output); facts != nil {
			return map[string]any{"facts": facts}
		}
		if m := successBraceJSON.FindStringSubmatch(output); m != nil {
			var obj map[string]any
			if err := json.Unmarshal([]byte(m[1]), &obj); err == nil {
				if af, ok := obj["ansible_facts"].(map[string]any); ok {
					return map[string]any{"facts": af}
				}
				return map[string]any{"facts": obj}
			}
		}
		return map[string]any{"error": "Could not extract facts from output"}
	}
	return nil
}

// extractFactsFromMsg replicates the "msg": "..." JSON walker in the Python
// version. It looks for the first `"msg": "` occurrence and consumes until an
// unescaped closing quote, then json.Unmarshal twice (once to strip the
// escaping, once to parse the payload).
func extractFactsFromMsg(output string) map[string]any {
	idx := strings.Index(output, `"msg": "`)
	if idx == -1 {
		return nil
	}
	start := idx + len(`"msg": "`)
	i := start
	escaped := false
	for i < len(output) {
		c := output[i]
		if c == '\\' && !escaped {
			escaped = true
			i++
			continue
		}
		if c == '"' && !escaped {
			break
		}
		escaped = false
		i++
	}
	if i <= start {
		return nil
	}
	quoted := `"` + output[start:i] + `"`
	var inner string
	if err := json.Unmarshal([]byte(quoted), &inner); err != nil {
		return nil
	}
	var facts map[string]any
	if err := json.Unmarshal([]byte(inner), &facts); err != nil {
		return nil
	}
	// If the payload has ansible_facts, return that; else return as-is.
	if af, ok := facts["ansible_facts"].(map[string]any); ok {
		return af
	}
	return facts
}

func coerceStringList(v any) []string {
	if v == nil {
		return nil
	}
	if arr, ok := v.([]any); ok {
		out := make([]string, 0, len(arr))
		for _, e := range arr {
			if s, ok := e.(string); ok {
				out = append(out, s)
			}
		}
		return out
	}
	return nil
}

// silence unused import when someone strips regex
var _ = regexp.MustCompile
