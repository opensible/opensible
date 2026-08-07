// Policy-as-code gate for Cloud Provisioning stacks.
package execute

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"sort"
	"strings"
	"time"
)

type policyRule struct {
	Enabled    bool     `json:"enabled"`
	Severity   string   `json:"severity"`
	MaxDestroy int      `json:"max_destroy"`
	Types      []string `json:"types"`
	Keys       []string `json:"keys"`
	Ports      []int    `json:"ports"`
	Limit      int      `json:"limit"`
}

type policyConfig struct {
	Mode  string                `json:"mode"` // warn | enforce
	Rules map[string]policyRule `json:"rules"`
}

type Violation struct {
	Rule     string `json:"rule"`
	Severity string `json:"severity"`
	Address  string `json:"address,omitempty"`
	Type     string `json:"type,omitempty"`
	Message  string `json:"message"`
}

type PolicyResult struct {
	Verdict    string      `json:"verdict"` // pass | warn | fail
	Mode       string      `json:"mode"`
	Denies     int         `json:"denies"`
	Warns      int         `json:"warns"`
	Violations []Violation `json:"violations"`
}

func parsePolicyConfig(raw any) *policyConfig {
	if raw == nil {
		return nil
	}
	b, err := json.Marshal(raw)
	if err != nil {
		return nil
	}
	var cfg policyConfig
	if err := json.Unmarshal(b, &cfg); err != nil {
		return nil
	}
	if cfg.Mode != "enforce" {
		cfg.Mode = "warn"
	}
	if len(cfg.Rules) == 0 {
		return nil
	}
	for _, r := range cfg.Rules {
		if r.Enabled {
			return &cfg
		}
	}
	return nil
}

type planChange struct {
	Actions []string       `json:"actions"`
	After   map[string]any `json:"after"`
}

type planResourceChange struct {
	Address string     `json:"address"`
	Type    string     `json:"type"`
	Change  planChange `json:"change"`
}

type planJSON struct {
	ResourceChanges []planResourceChange `json:"resource_changes"`
}

func showPlanJSON(stackDir, planFile string, env []string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "tofu", "show", "-json", planFile)
	cmd.Dir = stackDir
	cmd.Env = env
	return cmd.Output()
}

func hasAction(actions []string, want string) bool {
	for _, a := range actions {
		if a == want {
			return true
		}
	}
	return false
}

func sevOf(r policyRule) string {
	if r.Severity == "deny" {
		return "deny"
	}
	return "warn"
}

// evaluatePolicy applies the enabled rules to the plan JSON.
func evaluatePolicy(raw []byte, cfg *policyConfig) (*PolicyResult, error) {
	var plan planJSON
	if err := json.Unmarshal(raw, &plan); err != nil {
		return nil, err
	}

	res := &PolicyResult{Verdict: "pass", Mode: cfg.Mode, Violations: []Violation{}}
	add := func(rule, sev, addr, rtype, msg string) {
		res.Violations = append(res.Violations, Violation{
			Rule: rule, Severity: sev, Address: addr, Type: rtype, Message: msg,
		})
	}

	destroyed, created := 0, 0
	for _, rc := range plan.ResourceChanges {
		if hasAction(rc.Change.Actions, "delete") {
			destroyed++
		}
		if hasAction(rc.Change.Actions, "create") {
			created++
		}
	}

	// --- deny_destroy -------------------------------------------------------
	if r, ok := cfg.Rules["deny_destroy"]; ok && r.Enabled && destroyed > r.MaxDestroy {
		for _, rc := range plan.ResourceChanges {
			if hasAction(rc.Change.Actions, "delete") {
				add("deny_destroy", sevOf(r), rc.Address, rc.Type,
					fmt.Sprintf("resource would be destroyed (%d destroy(s), limit %d)", destroyed, r.MaxDestroy))
			}
		}
	}

	// --- denied_resource_types ---------------------------------------------
	if r, ok := cfg.Rules["denied_resource_types"]; ok && r.Enabled && len(r.Types) > 0 {
		denied := map[string]bool{}
		for _, t := range r.Types {
			denied[strings.ToLower(strings.TrimSpace(t))] = true
		}
		for _, rc := range plan.ResourceChanges {
			if hasAction(rc.Change.Actions, "no-op") || hasAction(rc.Change.Actions, "delete") {
				continue
			}
			if denied[strings.ToLower(rc.Type)] {
				add("denied_resource_types", sevOf(r), rc.Address, rc.Type,
					"resource type is on the denied list")
			}
		}
	}

	// --- require_tags -------------------------------------------------------
	if r, ok := cfg.Rules["require_tags"]; ok && r.Enabled && len(r.Keys) > 0 {
		for _, rc := range plan.ResourceChanges {
			if !hasAction(rc.Change.Actions, "create") && !hasAction(rc.Change.Actions, "update") {
				continue
			}
			tags := resourceTags(rc.Change.After)
			if tags == nil {
				continue // resource isn't taggable — don't punish it
			}
			var missing []string
			for _, k := range r.Keys {
				if _, ok := tags[strings.ToLower(k)]; !ok {
					missing = append(missing, k)
				}
			}
			if len(missing) > 0 {
				sort.Strings(missing)
				add("require_tags", sevOf(r), rc.Address, rc.Type,
					"missing required tag(s): "+strings.Join(missing, ", "))
			}
		}
	}

	// --- deny_public_ingress ------------------------------------------------
	if r, ok := cfg.Rules["deny_public_ingress"]; ok && r.Enabled {
		ports := r.Ports
		if len(ports) == 0 {
			ports = []int{22, 3389}
		}
		for _, rc := range plan.ResourceChanges {
			if hasAction(rc.Change.Actions, "delete") || hasAction(rc.Change.Actions, "no-op") {
				continue
			}
			for _, hit := range publicIngressHits(rc.Change.After, ports) {
				add("deny_public_ingress", sevOf(r), rc.Address, rc.Type,
					"ingress rule opens port "+hit+" to the whole internet (0.0.0.0/0 or ::/0)")
			}
		}
	}

	// --- max_created --------------------------------------------------------
	if r, ok := cfg.Rules["max_created"]; ok && r.Enabled && r.Limit > 0 && created > r.Limit {
		add("max_created", sevOf(r), "", "",
			fmt.Sprintf("plan creates %d resources, above the limit of %d", created, r.Limit))
	}

	for _, v := range res.Violations {
		if v.Severity == "deny" {
			res.Denies++
		} else {
			res.Warns++
		}
	}
	switch {
	case res.Denies > 0:
		res.Verdict = "fail"
	case res.Warns > 0:
		res.Verdict = "warn"
	}
	return res, nil
}

func resourceTags(after map[string]any) map[string]string {
	if after == nil {
		return nil
	}
	for _, key := range []string{"tags", "tags_all", "labels"} {
		v, ok := after[key]
		if !ok || v == nil {
			continue
		}
		out := map[string]string{}
		switch t := v.(type) {
		case map[string]any:
			for k, val := range t {
				out[strings.ToLower(k)] = fmt.Sprint(val)
			}
			return out
		case []any: // e.g. [{key=..., value=...}] or ["a","b"]
			for _, e := range t {
				switch item := e.(type) {
				case map[string]any:
					if k, ok := item["key"]; ok {
						out[strings.ToLower(fmt.Sprint(k))] = fmt.Sprint(item["value"])
					}
				case string:
					out[strings.ToLower(item)] = ""
				}
			}
			return out
		}
	}
	return nil
}

var openCIDRs = map[string]bool{"0.0.0.0/0": true, "::/0": true, "0.0.0.0/0,::/0": true}

func publicIngressHits(after map[string]any, ports []int) []string {
	if after == nil {
		return nil
	}
	var hits []string
	seen := map[string]bool{}

	consider := func(rule map[string]any) {
		dir, _ := rule["direction"].(string)
		if dir != "" && !strings.EqualFold(dir, "in") && !strings.EqualFold(dir, "ingress") &&
			!strings.EqualFold(dir, "inbound") {
			return
		}
		if !ruleIsOpenToWorld(rule) {
			return
		}
		for _, p := range ports {
			if ruleCoversPort(rule, p) {
				key := fmt.Sprint(p)
				if !seen[key] {
					seen[key] = true
					hits = append(hits, key)
				}
			}
		}
	}

	for _, key := range []string{"ingress", "rule", "firewall_rule", "security_rules", "rules"} {
		v, ok := after[key]
		if !ok {
			continue
		}
		switch t := v.(type) {
		case []any:
			for _, e := range t {
				if m, ok := e.(map[string]any); ok {
					consider(m)
				}
			}
		case map[string]any:
			consider(t)
		}
	}
	// Flat single-rule resources (aws_vpc_security_group_ingress_rule, etc.)
	if _, hasCidr := after["cidr_ipv4"]; hasCidr {
		consider(after)
	} else if _, hasCidr := after["cidr_blocks"]; hasCidr {
		consider(after)
	}
	sort.Strings(hits)
	return hits
}

func ruleIsOpenToWorld(rule map[string]any) bool {
	for _, key := range []string{"cidr_blocks", "ipv6_cidr_blocks", "source_ips", "cidr_ipv4", "cidr_ipv6", "source_addresses", "remote_ip_prefix"} {
		v, ok := rule[key]
		if !ok || v == nil {
			continue
		}
		switch t := v.(type) {
		case string:
			if openCIDRs[strings.TrimSpace(t)] {
				return true
			}
		case []any:
			for _, e := range t {
				if s, ok := e.(string); ok && openCIDRs[strings.TrimSpace(s)] {
					return true
				}
			}
		}
	}
	return false
}

func asInt(v any) (int, bool) {
	switch t := v.(type) {
	case float64:
		return int(t), true
	case int:
		return t, true
	case string:
		s := strings.TrimSpace(t)
		if s == "" {
			return 0, false
		}
		var n int
		if _, err := fmt.Sscanf(s, "%d", &n); err == nil {
			return n, true
		}
	}
	return 0, false
}

func ruleCoversPort(rule map[string]any, p int) bool {
	if proto, ok := rule["protocol"].(string); ok {
		lp := strings.ToLower(strings.TrimSpace(proto))
		if lp == "icmp" || lp == "icmpv6" {
			return false
		}
	}
	// Explicit "port" / "port_range" strings, e.g. "22", "20-80", "any".
	for _, key := range []string{"port", "port_range", "ports", "destination_port_range"} {
		v, ok := rule[key]
		if !ok || v == nil {
			continue
		}
		s := strings.TrimSpace(fmt.Sprint(v))
		if s == "" || strings.EqualFold(s, "any") || s == "*" || s == "1-65535" || s == "0-65535" {
			return true
		}
		if strings.Contains(s, "-") {
			parts := strings.SplitN(s, "-", 2)
			from, ok1 := asInt(parts[0])
			to, ok2 := asInt(parts[1])
			if ok1 && ok2 && p >= from && p <= to {
				return true
			}
			continue
		}
		for _, chunk := range strings.Split(s, ",") {
			if n, ok := asInt(strings.TrimSpace(chunk)); ok && n == p {
				return true
			}
		}
	}
	from, ok1 := asInt(rule["from_port"])
	to, ok2 := asInt(rule["to_port"])
	if ok1 && ok2 {
		if from == 0 && to == 0 {
			return true // "all ports" convention
		}
		return p >= from && p <= to
	}

	_, hasAnyPortKey := rule["from_port"]
	return !hasAnyPortKey
}

// ---------------------------------------------------------------------------
// Reporting
// ---------------------------------------------------------------------------

func formatPolicyReport(res *PolicyResult) string {
	var b strings.Builder
	b.WriteString("\n[policy] ── Policy-as-code gate ──────────────────────────────\n")
	b.WriteString(fmt.Sprintf("[policy] mode=%s  deny=%d  warn=%d\n", res.Mode, res.Denies, res.Warns))
	if len(res.Violations) == 0 {
		b.WriteString("[policy] PASS — no violations found.\n\n")
		return b.String()
	}
	for _, v := range res.Violations {
		label := "WARN"
		if v.Severity == "deny" {
			label = "DENY"
		}
		addr := v.Address
		if addr == "" {
			addr = "(plan)"
		}
		b.WriteString(fmt.Sprintf("[policy] %s  %-22s %s — %s\n", label, v.Rule, addr, v.Message))
	}
	switch {
	case res.Denies > 0 && res.Mode == "enforce":
		b.WriteString("[policy] FAILED — the run is blocked because the gate is in enforce mode.\n\n")
	case res.Denies > 0:
		b.WriteString("[policy] Violations found, but the gate is in warn mode — continuing.\n\n")
	default:
		b.WriteString("[policy] Warnings only — continuing.\n\n")
	}
	return b.String()
}
