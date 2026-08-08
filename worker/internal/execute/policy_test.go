package execute

import (
	"encoding/json"
	"testing"
)

func samplePlanJSON(t *testing.T) []byte {
	t.Helper()
	plan := map[string]any{
		"resource_changes": []map[string]any{
			{
				"address": "aws_security_group.web",
				"type":    "aws_security_group",
				"change": map[string]any{
					"actions": []string{"create"},
					"after": map[string]any{
						"ingress": []map[string]any{
							{"from_port": 22, "to_port": 22, "protocol": "tcp", "cidr_blocks": []string{"0.0.0.0/0"}},
						},
					},
				},
			},
		},
	}
	b, err := json.Marshal(plan)
	if err != nil {
		t.Fatalf("marshal plan: %v", err)
	}
	return b
}

func planConfig(t *testing.T, gateMode string, enforcement string) *policyConfig {
	t.Helper()
	raw := map[string]any{
		"mode": gateMode,
		"rules": map[string]any{
			"deny_public_ingress": map[string]any{
				"enabled":     true,
				"severity":    "deny",
				"enforcement": enforcement,
				"ports":       []int{22},
			},
		},
	}
	cfg := parsePolicyConfig(raw)
	if cfg == nil {
		t.Fatalf("parsePolicyConfig returned nil")
	}
	return cfg
}

func TestPolicyDenyInheritBlocksOnlyInEnforce(t *testing.T) {
	raw := samplePlanJSON(t)

	// warn mode + deny + inherit -> reported, denies counted, but not blocking.
	if res, err := evaluatePolicy(raw, planConfig(t, "warn", ""), ""); err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	} else if res.Blocked || res.Denies != 1 || len(res.BlockedBy) != 0 {
		t.Fatalf("gate=warn inherit: got blocked=%v denies=%d blocked_by=%v", res.Blocked, res.Denies, res.BlockedBy)
	}

	// enforce mode + deny + inherit -> blocks.
	if res, err := evaluatePolicy(raw, planConfig(t, "enforce", ""), ""); err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	} else if !res.Blocked || len(res.BlockedBy) != 1 || res.BlockedBy[0] != "deny_public_ingress" {
		t.Fatalf("gate=enforce inherit: got blocked=%v blocked_by=%v", res.Blocked, res.BlockedBy)
	}
}

func TestPolicyRuleLevelBlockAndReport(t *testing.T) {
	raw := samplePlanJSON(t)

	// rule-level block fires even when the gate runs in warn mode.
	if res, err := evaluatePolicy(raw, planConfig(t, "warn", "block"), ""); err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	} else if !res.Blocked || len(res.BlockedBy) != 1 {
		t.Fatalf("rule=block gate=warn: got blocked=%v blocked_by=%v", res.Blocked, res.BlockedBy)
	}

	// rule-level report never blocks, even in enforce mode.
	if res, err := evaluatePolicy(raw, planConfig(t, "enforce", "report"), ""); err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	} else if res.Blocked || len(res.BlockedBy) != 0 {
		t.Fatalf("rule=report gate=enforce: got blocked=%v blocked_by=%v", res.Blocked, res.BlockedBy)
	}
}

func TestPolicyViolationCarriesBlockingInfo(t *testing.T) {
	raw := samplePlanJSON(t)
	res, err := evaluatePolicy(raw, planConfig(t, "warn", "block"), "")
	if err != nil {
		t.Fatalf("evaluatePolicy: %v", err)
	}
	if len(res.Violations) != 1 {
		t.Fatalf("expected 1 violation, got %d", len(res.Violations))
	}
	if !res.Violations[0].Blocking || res.Violations[0].Rule != "deny_public_ingress" {
		t.Fatalf("violation missing blocking info: %+v", res.Violations[0])
	}
}

func TestParsePolicyConfigWithOnlyCustomRules(t *testing.T) {
	raw := map[string]any{
		"mode": "warn",
		"custom_rules": []map[string]any{{
			"id": "c1", "enabled": true, "severity": "error",
			"checks": []map[string]any{{"path": "a", "operator": "exists"}},
		}},
	}
	if cfg := parsePolicyConfig(raw); cfg == nil {
		t.Fatalf("parsePolicyConfig returned nil for enabled custom rule")
	}
}

func TestParsePolicyConfigDisabledRulesIsNil(t *testing.T) {
	raw := map[string]any{
		"mode": "enforce",
		"rules": map[string]any{
			"deny_destroy": map[string]any{"enabled": false},
		},
		"custom_rules": []map[string]any{{
			"id": "c1", "enabled": false, "severity": "error",
			"checks": []map[string]any{{"path": "a", "operator": "exists"}},
		}},
	}
	if cfg := parsePolicyConfig(raw); cfg != nil {
		t.Fatalf("parsePolicyConfig should return nil when nothing is enabled")
	}
}
