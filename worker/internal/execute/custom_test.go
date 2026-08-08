package execute

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func customPlanJSON(t *testing.T, outputAfter any) []byte {
	t.Helper()
	plan := map[string]any{
		"resource_changes": []map[string]any{
			{
				"address": "aws_instance.db.0",
				"type":    "aws_instance",
				"change": map[string]any{
					"actions": []string{"create"},
					"after": map[string]any{
						"instance_type": "t3.large",
						"root_block_device": []map[string]any{
							{"volume_size": 500, "encrypted": false},
							{"volume_size": 50, "encrypted": true},
						},
						"tags": map[string]any{"owner": "platform"},
					},
				},
			},
			{
				"address": "aws_s3_bucket.assets",
				"type":    "aws_s3_bucket",
				"change": map[string]any{
					"actions": []string{"create"},
					"after": map[string]any{
						"acl":  "public-read",
						"tags": map[string]any{"owner": "platform"},
					},
				},
			},
		},
		"output_changes": map[string]any{
			"endpoint_url": map[string]any{
				"actions": []string{"create"},
				"after":   "http://api.example.com",
			},
			"replicas": map[string]any{
				"actions": []string{"create"},
				"after":   outputAfter,
			},
		},
	}
	b, err := json.Marshal(plan)
	if err != nil {
		t.Fatalf("marshal plan: %v", err)
	}
	return b
}

func customConfig(t *testing.T, gateMode string, rules []map[string]any) *policyConfig {
	t.Helper()
	raw := map[string]any{"mode": gateMode, "custom_rules": rules}
	cfg := parsePolicyConfig(raw)
	if cfg == nil {
		t.Fatalf("parsePolicyConfig returned nil")
	}
	return cfg
}

func TestCustomRuleResourceMatchAndEquals(t *testing.T) {
	raw := customPlanJSON(t, nil)
	cfg := customConfig(t, "warn", []map[string]any{{
		"id":          "no_public_buckets",
		"enabled":     true,
		"severity":    "error",
		"enforcement": "block",
		"target":      "resource",
		"match":       map[string]any{"types": []string{"^aws_s3_bucket$"}},
		"checks":      []map[string]any{{"path": "acl", "operator": "equals", "value": "public-read"}},
		"message":     "S3 bucket must be private",
	}})
	res, err := evaluatePolicy(raw, cfg, "")
	if err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	}
	if !res.Blocked || len(res.BlockedBy) != 1 || res.BlockedBy[0] != "no_public_buckets" {
		t.Fatalf("got blocked=%v blocked_by=%v", res.Blocked, res.BlockedBy)
	}
	if len(res.Violations) != 1 || res.Violations[0].Address != "aws_s3_bucket.assets" {
		t.Fatalf("unexpected violations: %+v", res.Violations)
	}
}

func TestCustomRuleRegexAddressMatch(t *testing.T) {
	raw := customPlanJSON(t, nil)
	cfg := customConfig(t, "warn", []map[string]any{{
		"id":       "no_large_instances",
		"enabled":  true,
		"severity": "warning",
		"target":   "resource",
		"match":    map[string]any{"addresses": []string{"aws_instance\\.db\\.[0-9]+"}},
		"checks":   []map[string]any{{"path": "instance_type", "operator": "matches", "value": "^t3\\."}},
		"message":  "db instances must not be burstable t3",
	}})
	res, err := evaluatePolicy(raw, cfg, "")
	if err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	}
	if res.Verdict != "warn" || res.Blocked || len(res.Violations) != 1 {
		t.Fatalf("got verdict=%s blocked=%v violations=%d", res.Verdict, res.Blocked, len(res.Violations))
	}
}

func TestCustomRuleWildcardPath(t *testing.T) {
	raw := customPlanJSON(t, nil)
	cfg := customConfig(t, "enforce", []map[string]any{{
		"id":       "encrypt_disks",
		"enabled":  true,
		"severity": "error",
		"target":   "resource",
		"match":    map[string]any{"types": []string{"aws_instance"}},
		"checks":   []map[string]any{{"path": "root_block_device.*.encrypted", "operator": "equals", "value": true}},
		"message":  "root volumes must be encrypted",
	}})
	res, err := evaluatePolicy(raw, cfg, "")
	if err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	}
	// Second block device (encrypted: false) satisfies the wildcard -> violation.
	if res.Verdict != "fail" || !res.Blocked || len(res.Violations) != 1 {
		t.Fatalf("got verdict=%s blocked=%v violations=%d", res.Verdict, res.Blocked, len(res.Violations))
	}
}

func TestCustomRuleOutputTarget(t *testing.T) {
	raw := customPlanJSON(t, nil)
	cfg := customConfig(t, "warn", []map[string]any{{
		"id":       "no_http_endpoint",
		"enabled":  true,
		"severity": "warning",
		"target":   "output",
		"match":    map[string]any{"addresses": []string{"endpoint_url"}},
		"checks":   []map[string]any{{"path": "", "operator": "not_matches", "value": "^https://"}},
		"message":  "endpoint must be https",
	}})
	res, err := evaluatePolicy(raw, cfg, "")
	if err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	}
	if len(res.Violations) != 1 || res.Violations[0].Address != "output.endpoint_url" {
		t.Fatalf("unexpected violations: %+v", res.Violations)
	}
}

func TestCustomRuleSeverityInfoNeverBlocks(t *testing.T) {
	raw := customPlanJSON(t, nil)
	cfg := customConfig(t, "enforce", []map[string]any{{
		"id":       "info_only",
		"enabled":  true,
		"severity": "info",
		"target":   "resource",
		"checks":   []map[string]any{{"path": "instance_type", "operator": "equals", "value": "t3.large"}},
		"message":  "informational",
	}})
	res, err := evaluatePolicy(raw, cfg, "")
	if err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	}
	if res.Blocked || res.Warns != 1 || res.Denies != 0 {
		t.Fatalf("info must never block: blocked=%v warns=%d denies=%d", res.Blocked, res.Warns, res.Denies)
	}
}

func TestCustomRuleErrorBlockEnforcement(t *testing.T) {
	raw := customPlanJSON(t, nil)
	cfg := customConfig(t, "warn", []map[string]any{{
		"id":          "hard_error",
		"enabled":     true,
		"severity":    "error",
		"enforcement": "block",
		"target":      "resource",
		"match":       map[string]any{"types": []string{"aws_s3_bucket"}},
		"checks":      []map[string]any{{"path": "acl", "operator": "equals", "value": "public-read"}},
	}})
	res, err := evaluatePolicy(raw, cfg, "")
	if err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	}
	if !res.Blocked || len(res.BlockedBy) != 1 || res.Denies != 1 {
		t.Fatalf("error+block must block in warn mode: blocked=%v blocked_by=%v denies=%d", res.Blocked, res.BlockedBy, res.Denies)
	}
}

func TestCustomRuleConfigTarget(t *testing.T) {
	dir := t.TempDir()
	tfvars := "# comment\n" +
		"region = \"eu-west-1\"\n" +
		"instance_count = 3\n" +
		"enable_nat = true\n" +
		"zones = [\"a\", \"b\", \"c\"]\n" +
		"labels = {\n" +
		"  env = \"prod\"\n" +
		"}\n"
	if err := os.WriteFile(filepath.Join(dir, "terraform.tfvars"), []byte(tfvars), 0o600); err != nil {
		t.Fatalf("write tfvars: %v", err)
	}
	raw := customPlanJSON(t, nil)

	cfg := customConfig(t, "enforce", []map[string]any{{
		"id":       "prod_requires_tier",
		"enabled":  true,
		"severity": "error",
		"target":   "config",
		"checks": []map[string]any{
			{"path": "labels.env", "operator": "equals", "value": "prod"},
			{"path": "labels.tier", "operator": "not_exists"},
		},
		"message": "prod configs must set labels.tier",
	}})
	res, err := evaluatePolicy(raw, cfg, dir)
	if err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	}
	if len(res.Violations) != 1 || res.Violations[0].Address != "config" {
		t.Fatalf("unexpected violations: %+v", res.Violations)
	}
}

func TestCustomRuleConfigMissingTfvarsSkips(t *testing.T) {
	// No terraform.tfvars in the (empty) temp dir -> rule silently skips.
	dir := t.TempDir()
	raw := customPlanJSON(t, nil)
	cfg := customConfig(t, "enforce", []map[string]any{{
		"id":       "needs_tfvars",
		"enabled":  true,
		"severity": "error",
		"target":   "config",
		"checks":   []map[string]any{{"path": "anything", "operator": "exists"}},
	}})
	res, err := evaluatePolicy(raw, cfg, dir)
	if err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	}
	if len(res.Violations) != 0 {
		t.Fatalf("expected no violations, got %+v", res.Violations)
	}
}

func TestCustomRuleInvalidRegexSurfacesError(t *testing.T) {
	raw := customPlanJSON(t, nil)
	cfg := customConfig(t, "warn", []map[string]any{{
		"id":       "broken",
		"enabled":  true,
		"severity": "error",
		"target":   "resource",
		"match":    map[string]any{"types": []string{"[unclosed"}},
		"checks":   []map[string]any{{"path": "acl", "operator": "equals", "value": "public-read"}},
	}})
	res, err := evaluatePolicy(raw, cfg, "")
	if err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	}
	if len(res.Errors) != 1 || !strings.Contains(res.Errors[0], "broken") {
		t.Fatalf("expected a surfaced config error, got %+v", res.Errors)
	}
}

func TestParseTfvars(t *testing.T) {
	src := "a = \"hello\"\n" +
		"b = 42\n" +
		"c = -1.5\n" +
		"d = true\n" +
		"e = [\"x\", 1, false]\n" +
		"f = {\n  inner = \"v\"\n}\n" +
		"quoted = \"escaped \\\"quote\\\" and \\\\ slash\"\n"
	vals, err := parseTfvars(src)
	if err != nil {
		t.Fatalf("parseTfvars: %v", err)
	}
	if vals["a"] != "hello" {
		t.Fatalf("a=%v", vals["a"])
	}
	if v, _ := toFloat(vals["b"]); v != 42 {
		t.Fatalf("b=%v", vals["b"])
	}
	if v, _ := toFloat(vals["c"]); v != -1.5 {
		t.Fatalf("c=%v", vals["c"])
	}
	if vals["d"] != true {
		t.Fatalf("d=%v", vals["d"])
	}
	list, ok := vals["e"].([]any)
	if !ok || len(list) != 3 || list[0] != "x" || list[2] != false {
		t.Fatalf("e=%v", vals["e"])
	}
	obj, ok := vals["f"].(map[string]any)
	if !ok || obj["inner"] != "v" {
		t.Fatalf("f=%v", vals["f"])
	}
	if vals["quoted"] != `escaped "quote" and \ slash` {
		t.Fatalf("quoted=%q", vals["quoted"])
	}
}

func TestParseTfvarsBadInput(t *testing.T) {
	for _, src := range []string{
		"a = \"unterminated\n",
		"a = [1, 2\n",
		"= 5\n",
		"a = \n",
		"a 5\n",
	} {
		if _, err := parseTfvars(src); err == nil {
			t.Fatalf("expected error for input %q", src)
		}
	}
}
