// Custom policy rules: user-defined validation checks evaluated against the
// plan JSON (resource_changes / output_changes) or the rendered tfvars file.
package execute

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

const (
	targetResource = "resource"
	targetOutput   = "output"
	targetConfig   = "config"
)

var customTargets = map[string]bool{
	targetResource: true,
	targetOutput:   true,
	targetConfig:   true,
}

var customSeverities = map[string]bool{
	"info":    true,
	"warning": true,
	"error":   true,
}

var customOperators = map[string]bool{
	"exists":       true,
	"not_exists":   true,
	"equals":       true,
	"not_equals":   true,
	"contains":     true,
	"not_contains": true,
	"matches":      true,
	"not_matches":  true,
	"gt":           true,
	"gte":          true,
	"lt":           true,
	"lte":          true,
}

// customMatch selects which plan entries a custom rule applies to. Empty
// slices mean "any" (no filter). Types and addresses are RE2 regex patterns;
// actions are matched exactly.
type customMatch struct {
	Types     []string `json:"types"`
	Addresses []string `json:"addresses"`
	Actions   []string `json:"actions"`
}

type customCheck struct {
	Path     string `json:"path"`
	Operator string `json:"operator"`
	Value    any    `json:"value,omitempty"`
}

type customRule struct {
	ID          string        `json:"id"`
	Name        string        `json:"name"`
	Description string        `json:"description"`
	Category    string        `json:"category"`
	Enabled     bool          `json:"enabled"`
	Severity    string        `json:"severity"`    // info | warning | error
	Enforcement string        `json:"enforcement"` // inherit | block | report (empty == inherit)
	Target      string        `json:"target"`      // resource | output | config
	Match       customMatch   `json:"match"`
	Checks      []customCheck `json:"checks"`
	Message     string        `json:"message"`
}

// customBlocksWhen mirrors ruleBlocks for the richer severity set:
//   - "info": never blocks.
//   - "block": always blocks.
//   - "report": never blocks.
//   - "inherit" (default): blocks only in enforce mode.
func customBlocksWhen(sev, enf, gateMode string) bool {
	sev = strings.ToLower(strings.TrimSpace(sev))
	if sev == "info" {
		return false
	}
	switch strings.ToLower(strings.TrimSpace(enf)) {
	case "block":
		return true
	case "report":
		return false
	default: // "inherit" or empty
		return gateMode == "enforce"
	}
}

// evaluateCustomRules appends violations (and configuration errors) to res.
func evaluateCustomRules(res *PolicyResult, plan *planJSON, cfg *policyConfig, stackDir string) {
	for _, rule := range cfg.CustomRules {
		if !rule.Enabled {
			continue
		}
		if err := validateCustomRule(&rule); err != nil {
			res.Errors = append(res.Errors, fmt.Sprintf("custom rule %q: %v", rule.ID, err))
			continue
		}
		blocking := customBlocksWhen(rule.Severity, rule.Enforcement, cfg.Mode)
		var hits []customHit
		switch rule.Target {
		case targetResource:
			hits = evalCustomResources(plan, &rule)
		case targetOutput:
			hits = evalCustomOutputs(plan, &rule)
		case targetConfig:
			hits = evalCustomConfig(rule, stackDir)
		}
		if len(hits) == 0 {
			continue
		}
		for _, c := range hits {
			if !customRuleMatches(&rule, c.Root) {
				continue
			}
			res.Violations = append(res.Violations, Violation{
				Rule:     rule.ID,
				Severity: rule.Severity,
				Blocking: blocking,
				Address:  c.Address,
				Type:     c.Type,
				Message:  customMessage(&rule, c.Address),
			})
		}
	}
}

type customHit struct {
	Address string
	Type    string
	Root    any
}

// customRuleMatches reports whether every check of the rule passes for root.
// All conditions must hold for the rule to fire (AND semantics).
func customRuleMatches(rule *customRule, root any) bool {
	for _, c := range rule.Checks {
		if !customCheckPasses(c, root) {
			return false
		}
	}
	return true
}

func validateCustomRule(r *customRule) error {
	if strings.TrimSpace(r.ID) == "" {
		return fmt.Errorf("rule is missing an id")
	}
	if !customSeverities[strings.ToLower(strings.TrimSpace(r.Severity))] {
		return fmt.Errorf("unknown severity %q (want info, warning or error)", r.Severity)
	}
	if !customTargets[strings.ToLower(strings.TrimSpace(r.Target))] {
		return fmt.Errorf("unknown target %q (want resource, output or config)", r.Target)
	}
	enf := strings.ToLower(strings.TrimSpace(r.Enforcement))
	if enf != "" && enf != "inherit" && enf != "block" && enf != "report" {
		return fmt.Errorf("unknown enforcement %q (want inherit, block or report)", r.Enforcement)
	}
	if len(r.Checks) == 0 {
		return fmt.Errorf("rule has no checks")
	}
	for _, expr := range r.Match.Types {
		if _, err := regexp.Compile(expr); err != nil {
			return fmt.Errorf("invalid type pattern %q: %v", expr, err)
		}
	}
	for _, expr := range r.Match.Addresses {
		if _, err := regexp.Compile(expr); err != nil {
			return fmt.Errorf("invalid address pattern %q: %v", expr, err)
		}
	}
	for _, c := range r.Checks {
		op := strings.ToLower(strings.TrimSpace(c.Operator))
		if !customOperators[op] {
			return fmt.Errorf("unknown operator %q in check %q", c.Operator, c.Path)
		}
		if strings.HasPrefix(op, "matches") {
			expr, ok := c.Value.(string)
			if !ok {
				return fmt.Errorf("check %q needs a valid regex value", c.Path)
			}
			if _, err := regexp.Compile(expr); err != nil {
				return fmt.Errorf("check %q has an invalid regex: %v", c.Path, err)
			}
		}
	}
	return nil
}

func customMessage(r *customRule, addr string) string {
	msg := strings.TrimSpace(r.Message)
	if msg == "" {
		msg = strings.TrimSpace(r.Name)
	}
	if msg == "" {
		msg = "custom rule violation"
	}
	if addr != "" {
		msg += " (" + addr + ")"
	}
	return msg
}

func matchesAny(pats []string, s string) bool {
	for _, p := range pats {
		re, err := regexp.Compile(p)
		if err == nil && re.MatchString(s) {
			return true
		}
	}
	return false
}

func actionsContain(actions []string, want []string) bool {
	for _, w := range want {
		for _, a := range actions {
			if a == w {
				return true
			}
		}
	}
	return false
}

func evalCustomResources(plan *planJSON, rule *customRule) []customHit {
	var hits []customHit
	for _, rc := range plan.ResourceChanges {
		if len(rule.Match.Types) > 0 && !matchesAny(rule.Match.Types, rc.Type) {
			continue
		}
		if len(rule.Match.Addresses) > 0 && !matchesAny(rule.Match.Addresses, rc.Address) {
			continue
		}
		if len(rule.Match.Actions) > 0 && !actionsContain(rc.Change.Actions, rule.Match.Actions) {
			continue
		}
		hits = append(hits, customHit{Address: rc.Address, Type: rc.Type, Root: rc.Change.After})
	}
	return hits
}

func evalCustomOutputs(plan *planJSON, rule *customRule) []customHit {
	var hits []customHit
	for name, oc := range plan.OutputChanges {
		if len(rule.Match.Addresses) > 0 && !matchesAny(rule.Match.Addresses, name) {
			continue
		}
		if len(rule.Match.Actions) > 0 && !actionsContain(oc.Actions, rule.Match.Actions) {
			continue
		}
		hits = append(hits, customHit{Address: "output." + name, Root: oc.After})
	}
	return hits
}

func evalCustomConfig(rule customRule, stackDir string) []customHit {
	if stackDir == "" {
		return nil
	}
	src, err := os.ReadFile(filepath.Join(stackDir, "terraform.tfvars"))
	if err != nil {
		return nil // no rendered tfvars on the worker — nothing to check
	}
	vals, err := parseTfvars(string(src))
	if err != nil {
		return nil
	}
	return []customHit{{Address: "config", Root: vals}}
}

func customCheckPasses(c customCheck, root any) bool {
	op := strings.ToLower(strings.TrimSpace(c.Operator))
	got, ok := resolvePath(root, c.Path)
	switch op {
	case "exists":
		return ok && len(got) > 0
	case "not_exists":
		return !ok || len(got) == 0
	case "not_equals", "not_contains", "not_matches":
		if !ok || len(got) == 0 {
			return false
		}
		for _, g := range got {
			if customOpMatch(op[4:], g, c.Value) {
				return false
			}
		}
		return true
	default:
		if !ok || len(got) == 0 {
			return false
		}
		for _, g := range got {
			if customOpMatch(op, g, c.Value) {
				return true
			}
		}
		return false
	}
}

// resolvePath walks a dot-separated path (supporting `[0]` indices and `*`
// wildcards) and returns every value matched. The empty result means the path
// did not resolve.
func resolvePath(root any, path string) ([]any, bool) {
	path = strings.TrimSpace(path)
	if path == "" || path == "." {
		return []any{root}, true
	}
	parts := strings.Split(path, ".")
	vals := []any{root}
	for _, part := range parts {
		var next []any
		if part == "*" {
			for _, v := range vals {
				switch t := v.(type) {
				case []any:
					next = append(next, t...)
				case map[string]any:
					for _, mv := range t {
						next = append(next, mv)
					}
				}
			}
		} else if idx, isIdx := bracketIndex(part); isIdx {
			for _, v := range vals {
				if list, ok := v.([]any); ok && idx >= 0 && idx < len(list) {
					next = append(next, list[idx])
				}
			}
		} else {
			for _, v := range vals {
				if m, ok := v.(map[string]any); ok {
					if mv, exists := m[part]; exists {
						next = append(next, mv)
					}
				}
			}
		}
		if len(next) == 0 {
			return nil, false
		}
		vals = next
	}
	return vals, true
}

func bracketIndex(part string) (int, bool) {
	if len(part) >= 3 && part[0] == '[' && part[len(part)-1] == ']' {
		part = part[1 : len(part)-1]
	}
	n, err := strconv.Atoi(part)
	return n, err == nil
}

func customOpMatch(op string, got, want any) bool {
	switch op {
	case "equals":
		return valuesEqual(got, want)
	case "contains":
		return valueContains(got, want)
	case "matches":
		return valueMatches(got, want)
	case "gt", "gte", "lt", "lte":
		return valueCompare(op, got, want)
	}
	return false
}

func normalizeScalar(v any) (string, bool) {
	switch t := v.(type) {
	case string:
		return t, true
	case bool:
		return strconv.FormatBool(t), true
	case float64:
		return strconv.FormatFloat(t, 'f', -1, 64), true
	case int:
		return strconv.Itoa(t), true
	case int64:
		return strconv.FormatInt(t, 10), true
	}
	return "", false
}

func valuesEqual(a, b any) bool {
	sa, oka := normalizeScalar(a)
	sb, okb := normalizeScalar(b)
	if oka && okb {
		return sa == sb
	}
	return a == nil && b == nil
}

func valueContains(got, want any) bool {
	switch t := got.(type) {
	case string:
		if s, ok := want.(string); ok {
			return strings.Contains(t, s)
		}
	case []any:
		for _, e := range t {
			if valuesEqual(e, want) {
				return true
			}
		}
	case map[string]any:
		if s, ok := want.(string); ok {
			_, exists := t[s]
			return exists
		}
	}
	return false
}

func valueMatches(got, want any) bool {
	s, ok := normalizeScalar(got)
	if !ok {
		return false
	}
	expr, ok := want.(string)
	if !ok {
		return false
	}
	re, err := regexp.Compile(expr)
	if err != nil {
		return false
	}
	return re.MatchString(s)
}

func toFloat(v any) (float64, bool) {
	switch t := v.(type) {
	case float64:
		return t, true
	case float32:
		return float64(t), true
	case int:
		return float64(t), true
	case int64:
		return float64(t), true
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(t), 64)
		return f, err == nil
	}
	return 0, false
}

func valueCompare(op string, got, want any) bool {
	gn, ok1 := toFloat(got)
	wn, ok2 := toFloat(want)
	if ok1 && ok2 {
		switch op {
		case "gt":
			return gn > wn
		case "gte":
			return gn >= wn
		case "lt":
			return gn < wn
		case "lte":
			return gn <= wn
		}
	}
	gs, ok1s := normalizeScalar(got)
	ws, ok2s := normalizeScalar(want)
	if ok1s && ok2s {
		switch op {
		case "gt":
			return gs > ws
		case "gte":
			return gs >= ws
		case "lt":
			return gs < ws
		case "lte":
			return gs <= ws
		}
	}
	return false
}

// ---------------------------------------------------------------------------
// Minimal terraform.tfvars parser for the subset rendered by the server's
// _render_value() (flat assignments, quoted strings, numbers, booleans, lists
// and maps).
// ---------------------------------------------------------------------------

type tfvarToken struct {
	kind byte
	text string
	num  float64
}

const (
	tkEOF byte = iota
	tkIdent
	tkString
	tkNumber
	tkTrue
	tkFalse
	tkLBrak
	tkRBrak
	tkLBrace
	tkRBrace
	tkComma
	tkEq
)

func parseTfvars(src string) (map[string]any, error) {
	toks, err := tokenizeTfvars(src)
	if err != nil {
		return nil, err
	}
	p := &tfvarParser{toks: toks}
	out := map[string]any{}
	for p.pos < len(p.toks) && p.peek().kind != tkEOF {
		first := p.peek()
		if first.kind != tkIdent {
			return nil, fmt.Errorf("expected a variable name")
		}
		key := first.text
		p.pos++
		if p.peek().kind != tkEq {
			return nil, fmt.Errorf("expected '=' after %q", key)
		}
		p.pos++
		v, err := p.value()
		if err != nil {
			return nil, err
		}
		out[key] = v
	}
	return out, nil
}

type tfvarParser struct {
	toks []tfvarToken
	pos  int
}

func (p *tfvarParser) peek() tfvarToken {
	if p.pos < len(p.toks) {
		return p.toks[p.pos]
	}
	return tfvarToken{kind: tkEOF}
}

func (p *tfvarParser) value() (any, error) {
	t := p.peek()
	switch t.kind {
	case tkString, tkNumber, tkTrue, tkFalse:
		p.pos++
		if t.kind == tkTrue {
			return true, nil
		}
		if t.kind == tkFalse {
			return false, nil
		}
		if t.kind == tkNumber {
			return t.num, nil
		}
		return t.text, nil
	case tkLBrak:
		return p.list()
	case tkLBrace:
		return p.object()
	}
	return nil, fmt.Errorf("unexpected token in value")
}

func (p *tfvarParser) list() (any, error) {
	p.pos++ // consume '['
	out := []any{}
	for {
		t := p.peek()
		if t.kind == tkRBrak {
			p.pos++
			return out, nil
		}
		if t.kind == tkEOF {
			return nil, fmt.Errorf("unterminated list")
		}
		v, err := p.value()
		if err != nil {
			return nil, err
		}
		out = append(out, v)
		if p.peek().kind == tkComma {
			p.pos++
			continue
		}
		if p.peek().kind != tkRBrak {
			return nil, fmt.Errorf("expected ',' or ']' in list")
		}
	}
}

func (p *tfvarParser) object() (any, error) {
	p.pos++ // consume '{'
	out := map[string]any{}
	for {
		t := p.peek()
		if t.kind == tkRBrace {
			p.pos++
			return out, nil
		}
		if t.kind == tkEOF {
			return nil, fmt.Errorf("unterminated object")
		}
		if t.kind != tkIdent {
			return nil, fmt.Errorf("expected a key inside object")
		}
		key := t.text
		p.pos++
		if p.peek().kind != tkEq {
			return nil, fmt.Errorf("expected '=' after %q", key)
		}
		p.pos++
		v, err := p.value()
		if err != nil {
			return nil, err
		}
		out[key] = v
		if p.peek().kind == tkComma {
			p.pos++
		}
	}
}

func tokenizeTfvars(src string) ([]tfvarToken, error) {
	var toks []tfvarToken
	i := 0
	n := len(src)
	for i < n {
		c := src[i]
		switch {
		case c == ' ' || c == '\t' || c == '\n' || c == '\r':
			i++
		case c == '#':
			for i < n && src[i] != '\n' {
				i++
			}
		case c == '[':
			toks = append(toks, tfvarToken{kind: tkLBrak})
			i++
		case c == ']':
			toks = append(toks, tfvarToken{kind: tkRBrak})
			i++
		case c == '{':
			toks = append(toks, tfvarToken{kind: tkLBrace})
			i++
		case c == '}':
			toks = append(toks, tfvarToken{kind: tkRBrace})
			i++
		case c == ',':
			toks = append(toks, tfvarToken{kind: tkComma})
			i++
		case c == '=':
			toks = append(toks, tfvarToken{kind: tkEq})
			i++
		case c == '"':
			start := i
			i++
			var b strings.Builder
			for i < n && src[i] != '"' {
				if src[i] == '\\' && i+1 < n {
					next := src[i+1]
					if next == '"' || next == '\\' {
						b.WriteByte(next)
						i += 2
						continue
					}
				}
				b.WriteByte(src[i])
				i++
			}
			if i >= n {
				return nil, fmt.Errorf("unterminated string starting at offset %d", start)
			}
			i++ // closing quote
			toks = append(toks, tfvarToken{kind: tkString, text: b.String()})
		case isIdentStart(c):
			start := i
			for i < n && isIdentPart(src[i]) {
				i++
			}
			word := src[start:i]
			switch word {
			case "true":
				toks = append(toks, tfvarToken{kind: tkTrue})
			case "false":
				toks = append(toks, tfvarToken{kind: tkFalse})
			default:
				toks = append(toks, tfvarToken{kind: tkIdent, text: word})
			}
		case c == '-' || (c >= '0' && c <= '9'):
			start := i
			if c == '-' {
				i++
			}
			for i < n && ((src[i] >= '0' && src[i] <= '9') || src[i] == '.') {
				i++
			}
			f, err := strconv.ParseFloat(src[start:i], 64)
			if err != nil {
				return nil, fmt.Errorf("invalid number %q", src[start:i])
			}
			toks = append(toks, tfvarToken{kind: tkNumber, num: f})
		default:
			return nil, fmt.Errorf("unexpected character %q at offset %d", string(c), i)
		}
	}
	toks = append(toks, tfvarToken{kind: tkEOF})
	return toks, nil
}

func isIdentStart(c byte) bool {
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_'
}

func isIdentPart(c byte) bool {
	return isIdentStart(c) || (c >= '0' && c <= '9') || c == '-' || c == '.'
}
