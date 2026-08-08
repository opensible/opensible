// Policy-as-code gate for Cloud Provisioning stacks.
package execute

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"regexp"
	"sort"
	"strings"
	"time"
)

// ---------------------------------------------------------------------------
// Config (mirrors backend _default_policy)
// ---------------------------------------------------------------------------

type policyRule struct {
	Enabled  bool   `json:"enabled"`
	Severity string `json:"severity"`
	Enforcement string   `json:"enforcement"`
	MaxDestroy  int      `json:"max_destroy"`
	Types       []string `json:"types"`
	Keys        []string `json:"keys"`
	Ports       []int    `json:"ports"`
	Limit       int      `json:"limit"`
}
type customRule struct {
	ID            string   `json:"id"`
	Name          string   `json:"name"`
	Description   string   `json:"description"`
	Enabled       bool     `json:"enabled"`
	Severity      string   `json:"severity"`    // info | warn | deny
	Enforcement   string   `json:"enforcement"` // inherit | block | report
	ResourceTypes []string `json:"resource_types"`
	Addresses     []string `json:"addresses"`
	Actions       []string `json:"actions"`
	Attribute     string   `json:"attribute"`
	Operator      string   `json:"operator"`
	Value         string   `json:"value"`
	Message       string   `json:"message"`
}

type policyConfig struct {
	Mode        string                `json:"mode"` // warn | enforce
	Rules       map[string]policyRule `json:"rules"`
	CustomRules []customRule          `json:"custom_rules"`
}

type Violation struct {
	Rule     string `json:"rule"`
	Name     string `json:"name,omitempty"`
	Severity string `json:"severity"`
	Blocking bool   `json:"blocking"`
	Address  string `json:"address,omitempty"`
	Type     string `json:"type,omitempty"`
	Message  string `json:"message"`
}

type PolicyResult struct {
	Verdict string `json:"verdict"` // pass | warn | fail
	Mode    string `json:"mode"`
	Denies  int    `json:"denies"`
	Warns   int    `json:"warns"`
	Infos   int    `json:"infos"`
	// Blocked is true when at least one violation is blocking.
	Blocked    bool        `json:"blocked"`
	BlockedBy  []string    `json:"blocked_by,omitempty"`
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
	// Nothing enabled → treat as absent so we skip the whole gate.
	for _, r := range cfg.Rules {
		if r.Enabled {
			return &cfg
		}
	}
	for _, c := range cfg.CustomRules {
		if c.Enabled {
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

// showPlanJSON runs `tofu show -json <planFile>` in stackDir.
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

// enforcementOf normalises the per-rule enforcement override.
func enforcementOf(r policyRule) string {
	switch strings.ToLower(strings.TrimSpace(r.Enforcement)) {
	case "block":
		return "block"
	case "report":
		return "report"
	default:
		return "inherit"
	}
}


func ruleBlocks(r policyRule, mode string) bool {
	switch enforcementOf(r) {
	case "block":
		return true
	case "report":
		return false
	default:
		return sevOf(r) == "deny" && mode == "enforce"
	}
}


func customSevOf(c customRule) string {
	switch strings.ToLower(strings.TrimSpace(c.Severity)) {
	case "deny":
		return "deny"
	case "info":
		return "info"
	default:
		return "warn"
	}
}

func customRuleBlocks(c customRule, mode string) bool {
	switch strings.ToLower(strings.TrimSpace(c.Enforcement)) {
	case "block":
		return true
	case "report":
		return false
	default:
		return customSevOf(c) == "deny" && mode == "enforce"
	}
}

// anyRegexMatch reports whether s matches any of the patterns. An empty
// pattern list means "no constraint" and matches everything.
func anyRegexMatch(patterns []string, s string) bool {
	if len(patterns) == 0 {
		return true
	}
	for _, p := range patterns {
		re, err := regexp.Compile(p)
		if err != nil {
			continue
		}
		if re.MatchString(s) {
			return true
		}
	}
	return false
}

func lookupAttribute(root any, path string) []any {
	if strings.TrimSpace(path) == "" {
		if root == nil {
			return nil
		}
		return []any{root}
	}
	current := []any{root}
	for _, seg := range strings.Split(path, ".") {
		seg = strings.TrimSpace(seg)
		if seg == "" {
			continue
		}
		var next []any
		for _, node := range current {
			switch t := node.(type) {
			case map[string]any:
				if seg == "*" {
					for _, v := range t {
						next = append(next, v)
					}
				} else if v, ok := t[seg]; ok {
					next = append(next, v)
				}
			case []any:
				if seg == "*" {
					next = append(next, t...)
				} else if idx, ok := asInt(seg); ok && idx >= 0 && idx < len(t) {
					next = append(next, t[idx])
				} else {
					// Allow "ingress.cidr_blocks" against a list of objects.
					for _, e := range t {
						if m, ok := e.(map[string]any); ok {
							if v, ok := m[seg]; ok {
								next = append(next, v)
							}
						}
					}
				}
			}
		}
		if len(next) == 0 {
			return nil
		}
		current = next
	}
	return current
}

func flattenValues(vals []any) []any {
	var out []any
	for _, v := range vals {
		if list, ok := v.([]any); ok {
			out = append(out, flattenValues(list)...)
			continue
		}
		out = append(out, v)
	}
	return out
}

func valueToString(v any) string {
	switch t := v.(type) {
	case nil:
		return ""
	case string:
		return t
	case float64:
		if t == float64(int64(t)) {
			return fmt.Sprintf("%d", int64(t))
		}
		return fmt.Sprintf("%v", t)
	case bool:
		return fmt.Sprintf("%t", t)
	case map[string]any, []any:
		if b, err := json.Marshal(t); err == nil {
			return string(b)
		}
	}
	return fmt.Sprint(v)
}

func asFloat(v any) (float64, bool) {
	switch t := v.(type) {
	case float64:
		return t, true
	case int:
		return float64(t), true
	case bool:
		if t {
			return 1, true
		}
		return 0, true
	case string:
		var f float64
		if _, err := fmt.Sscanf(strings.TrimSpace(t), "%g", &f); err == nil {
			return f, true
		}
	}
	return 0, false
}


func evalCustomRule(c customRule, rc planResourceChange) []string {
	if !anyRegexMatch(c.ResourceTypes, rc.Type) {
		return nil
	}
	if !anyRegexMatch(c.Addresses, rc.Address) {
		return nil
	}
	actions := c.Actions
	if len(actions) == 0 {
		actions = []string{"create", "update"}
	}
	matchedAction := false
	for _, a := range actions {
		if hasAction(rc.Change.Actions, strings.ToLower(strings.TrimSpace(a))) {
			matchedAction = true
			break
		}
	}
	if !matchedAction {
		return nil
	}

	op := strings.ToLower(strings.TrimSpace(c.Operator))
	if op == "" {
		op = "matches"
	}
	found := flattenValues(lookupAttribute(map[string]any(rc.Change.After), c.Attribute))

	msg := c.Message
	fallback := func(detail string) string {
		if msg != "" {
			return msg
		}
		return detail
	}
	attrLabel := c.Attribute
	if attrLabel == "" {
		attrLabel = "resource"
	}

	switch op {
	case "exists":
		if len(found) == 0 {
			return []string{fallback(fmt.Sprintf("attribute %q is required but not set", attrLabel))}
		}
		return nil
	case "not_exists":
		if len(found) > 0 {
			return []string{fallback(fmt.Sprintf("attribute %q must not be set", attrLabel))}
		}
		return nil
	}

	if len(found) == 0 {
		// Nothing to compare — "not_matches"/"not_equals" are satisfied by absence.
		return nil
	}

	var re *regexp.Regexp
	if op == "matches" || op == "not_matches" {
		compiled, err := regexp.Compile(c.Value)
		if err != nil {
			return []string{fmt.Sprintf("rule has an invalid regex (%s): %v", c.Value, err)}
		}
		re = compiled
	}

	var hits []string
	for _, v := range found {
		s := valueToString(v)
		switch op {
		case "matches":
			if re.MatchString(s) {
				hits = append(hits, fallback(fmt.Sprintf("%s = %q matches /%s/", attrLabel, s, c.Value)))
			}
		case "not_matches":
			if !re.MatchString(s) {
				hits = append(hits, fallback(fmt.Sprintf("%s = %q does not match /%s/", attrLabel, s, c.Value)))
			}
		case "equals":
			if s == c.Value {
				hits = append(hits, fallback(fmt.Sprintf("%s equals %q", attrLabel, c.Value)))
			}
		case "not_equals":
			if s != c.Value {
				hits = append(hits, fallback(fmt.Sprintf("%s = %q, expected %q", attrLabel, s, c.Value)))
			}
		case "gt", "lt":
			want, ok1 := asFloat(c.Value)
			got, ok2 := asFloat(v)
			if !ok1 || !ok2 {
				continue
			}
			if (op == "gt" && got > want) || (op == "lt" && got < want) {
				sym := ">"
				if op == "lt" {
					sym = "<"
				}
				hits = append(hits, fallback(fmt.Sprintf("%s = %s is %s %s", attrLabel, s, sym, c.Value)))
			}
		}
		if len(hits) > 0 {
			break // one violation per resource is enough
		}
	}
	return hits
}


// evaluatePolicy applies the enabled rules to the plan JSON.
func evaluatePolicy(raw []byte, cfg *policyConfig) (*PolicyResult, error) {
	var plan planJSON
	if err := json.Unmarshal(raw, &plan); err != nil {
		return nil, err
	}

	res := &PolicyResult{Verdict: "pass", Mode: cfg.Mode, Violations: []Violation{}}
	blocking := map[string]bool{}
	for id, r := range cfg.Rules {
		blocking[id] = r.Enabled && ruleBlocks(r, cfg.Mode)
	}
	add := func(rule, sev, addr, rtype, msg string) {
		res.Violations = append(res.Violations, Violation{
			Rule: rule, Severity: sev, Blocking: blocking[rule], Address: addr, Type: rtype, Message: msg,
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

	// --- custom (user-defined) rules ----------------------------------------
	for _, c := range cfg.CustomRules {
		if !c.Enabled {
			continue
		}
		ruleKey := "custom:" + c.ID
		sev := customSevOf(c)
		blocks := customRuleBlocks(c, cfg.Mode)
		name := c.Name
		if name == "" {
			name = c.ID
		}
		for _, rc := range plan.ResourceChanges {
			for _, msg := range evalCustomRule(c, rc) {
				res.Violations = append(res.Violations, Violation{
					Rule: ruleKey, Name: name, Severity: sev, Blocking: blocks,
					Address: rc.Address, Type: rc.Type, Message: msg,
				})
			}
		}
	}

	seenBlocked := map[string]bool{}
	for _, v := range res.Violations {
		switch v.Severity {
		case "deny":
			res.Denies++
		case "info":
			res.Infos++
		default:
			res.Warns++
		}
		if v.Blocking && !seenBlocked[v.Rule] {
			seenBlocked[v.Rule] = true
			res.BlockedBy = append(res.BlockedBy, v.Rule)
		}
	}

	sort.Strings(res.BlockedBy)
	res.Blocked = len(res.BlockedBy) > 0
	switch {
	case res.Blocked || res.Denies > 0:
		res.Verdict = "fail"
	case res.Warns > 0:
		res.Verdict = "warn"
	}
	return res, nil
}

// resourceTags normalises the many tag shapes providers use. Returns nil when
// the resource has no recognisable tag attribute.
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

// publicIngressHits scans a resource's planned attributes for firewall/security
// group ingress rules that expose one of `ports` to the open internet.
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

// ruleCoversPort reports whether a rule's port range includes p. A rule with no
// recognisable port info is treated as "all ports" (i.e. it covers p).
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
	// from_port / to_port pairs (AWS style).
	from, ok1 := asInt(rule["from_port"])
	to, ok2 := asInt(rule["to_port"])
	if ok1 && ok2 {
		if from == 0 && to == 0 {
			return true // "all ports" convention
		}
		return p >= from && p <= to
	}
	// No port information at all → assume the rule is wide open.
	_, hasAnyPortKey := rule["from_port"]
	return !hasAnyPortKey
}

// ---------------------------------------------------------------------------
// Reporting
// ---------------------------------------------------------------------------

func formatPolicyReport(res *PolicyResult) string {
	var b strings.Builder
	b.WriteString("\n[policy] ── Policy-as-code gate ──────────────────────────────\n")
	b.WriteString(fmt.Sprintf("[policy] mode=%s  deny=%d  warn=%d  info=%d\n", res.Mode, res.Denies, res.Warns, res.Infos))
	if len(res.Violations) == 0 {
		b.WriteString("[policy] PASS — no violations found.\n\n")
		return b.String()
	}
	for _, v := range res.Violations {
		label := "WARN"
		switch v.Severity {
		case "deny":
			label = "DENY"
		case "info":
			label = "INFO"
		}
		if v.Blocking {
			label = "BLOCK"
		}
		addr := v.Address
		if addr == "" {
			addr = "(plan)"
		}
		ruleLabel := v.Rule
		if v.Name != "" {
			ruleLabel = v.Name
		}
		b.WriteString(fmt.Sprintf("[policy] %s  %-22s %s — %s\n", label, ruleLabel, addr, v.Message))
	}
	switch {
	case res.Blocked:
		b.WriteString("[policy] FAILED — run blocked by rule(s): " + strings.Join(res.BlockedBy, ", ") + "\n\n")
	case res.Denies > 0:
		b.WriteString("[policy] Violations found, but no rule is set to block this run — continuing.\n\n")
	default:
		b.WriteString("[policy] Warnings only — continuing.\n\n")
	}
	return b.String()
}
