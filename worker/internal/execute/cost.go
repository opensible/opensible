// Cost estimation for Cloud Provisioning stacks.
package execute

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

// ---------------------------------------------------------------------------
// Config (mirrors backend services/cloud_cost.py)
// ---------------------------------------------------------------------------

type costConfig struct {
	Provider       string             `json:"provider"`
	Currency       string             `json:"currency"`
	HoursPerMonth  float64            `json:"hours_per_month"`
	CatalogVersion any                `json:"catalog_version"`
	Compute        map[string]float64 `json:"-"`
	Storage        map[string]float64 `json:"-"`
	Network        map[string]float64 `json:"-"`
	Managed        map[string]float64 `json:"-"`
}

// CostLine is a single priced resource in the plan.
type CostLine struct {
	Address  string  `json:"address"`
	Type     string  `json:"type"`
	Kind     string  `json:"kind"`
	Action   string  `json:"action"` // create | update | replace | delete
	Before   float64 `json:"monthly_before"`
	After    float64 `json:"monthly_after"`
	Delta    float64 `json:"monthly_delta"`
}

// CostResult is the structured estimate reported back to the backend.
type CostResult struct {
	Provider       string     `json:"provider"`
	Currency       string     `json:"currency"`
	MonthlyCurrent float64    `json:"monthly_current"`
	MonthlyPlanned float64    `json:"monthly_planned"`
	MonthlyDelta   float64    `json:"monthly_delta"`
	YearlyDelta    float64    `json:"yearly_delta"`
	Priced         int        `json:"priced"`
	Unpriced       int        `json:"unpriced"`
	Lines          []CostLine `json:"lines"`
}

func numMap(v any) map[string]float64 {
	out := map[string]float64{}
	m, ok := v.(map[string]any)
	if !ok {
		return out
	}
	for k, raw := range m {
		if f, ok := raw.(float64); ok {
			out[k] = f
		}
	}
	return out
}

// parseCostConfig returns nil when the backend did not ship a cost payload.
func parseCostConfig(raw any) *costConfig {
	m, ok := raw.(map[string]any)
	if !ok || len(m) == 0 {
		return nil
	}
	cfg := &costConfig{Provider: "unknown", Currency: "USD", HoursPerMonth: 730}
	if s, ok := m["provider"].(string); ok && s != "" {
		cfg.Provider = s
	}
	if s, ok := m["currency"].(string); ok && s != "" {
		cfg.Currency = s
	}
	if f, ok := m["hours_per_month"].(float64); ok && f > 0 {
		cfg.HoursPerMonth = f
	}
	cfg.CatalogVersion = m["catalog_version"]
	pricing, _ := m["pricing"].(map[string]any)
	if pricing == nil {
		return nil
	}
	cfg.Compute = numMap(pricing["compute"])
	cfg.Storage = numMap(pricing["storage"])
	cfg.Network = numMap(pricing["network"])
	cfg.Managed = numMap(pricing["managed"])
	return cfg
}

// ---------------------------------------------------------------------------
// Plan JSON (with before + after, unlike the policy view)
// ---------------------------------------------------------------------------

type costChange struct {
	Actions []string       `json:"actions"`
	Before  map[string]any `json:"before"`
	After   map[string]any `json:"after"`
}

type costResourceChange struct {
	Address string     `json:"address"`
	Type    string     `json:"type"`
	Name    string     `json:"name"`
	Change  costChange `json:"change"`
}

type costPlanJSON struct {
	ResourceChanges []costResourceChange `json:"resource_changes"`
}

// ---------------------------------------------------------------------------
// Resource type -> billable kind (mirrors backend /api/cost/extract/plan)
// ---------------------------------------------------------------------------

func kindForType(ttype string) string {
	t := strings.ToLower(ttype)
	switch {
	case strings.Contains(t, "instance") && !strings.Contains(t, "db_instance") && !strings.Contains(t, "rds_instance"):
		return "instance"
	case strings.HasSuffix(t, "_vm") || strings.HasSuffix(t, "_server"):
		return "instance"
	case strings.Contains(t, "kubernetes") || strings.HasSuffix(t, "_cluster"):
		return "kubernetes_cluster"
	case strings.Contains(t, "load_balancer") || strings.HasSuffix(t, "_lb") || strings.Contains(t, "_elb"):
		return "load_balancer"
	case strings.Contains(t, "nat"):
		return "nat_gateway"
	case strings.Contains(t, "vpn"):
		return "vpn_gateway"
	case strings.Contains(t, "public_ip") || strings.HasSuffix(t, "_eip"):
		return "public_ip"
	case strings.Contains(t, "bucket") || strings.Contains(t, "object_storage"):
		return "object_storage"
	case strings.Contains(t, "snapshot"):
		return "snapshot"
	case strings.Contains(t, "volume") || strings.Contains(t, "disk"):
		return "ssd"
	case strings.Contains(t, "database") || strings.HasSuffix(t, "_db_instance") || strings.HasSuffix(t, "_rds_instance"):
		return "database"
	case strings.Contains(t, "dns_zone") || strings.HasSuffix(t, "_zone"):
		return "dns_zone"
	case strings.Contains(t, "cdn") || strings.Contains(t, "cloudfront"):
		return "cdn"
	case strings.Contains(t, "waf"):
		return "waf"
	}
	return ""
}

func attrFloat(vals map[string]any, keys ...string) float64 {
	for _, k := range keys {
		if v, ok := vals[k]; ok {
			switch n := v.(type) {
			case float64:
				return n
			case string:
				var f float64
				if _, err := fmt.Sscanf(n, "%f", &f); err == nil {
					return f
				}
			}
		}
	}
	return 0
}

func monthlyFor(cfg *costConfig, kind string, vals map[string]any) float64 {
	if vals == nil {
		return 0
	}
	h := cfg.HoursPerMonth
	switch kind {
	case "instance":
		vcpu := attrFloat(vals, "vcpus", "cpu", "cores", "vcpu")
		ram := attrFloat(vals, "memory_gb", "ram_gb")
		if ram == 0 {
			if mb := attrFloat(vals, "memory"); mb > 0 {
				ram = mb / 1024
			}
		}
		if vcpu == 0 {
			vcpu = 2
		}
		if ram == 0 {
			ram = 4
		}
		return (vcpu*cfg.Compute["vcpu_hour"] + ram*cfg.Compute["ram_gb_hour"]) * h
	case "ssd":
		size := attrFloat(vals, "size_gb", "size")
		if size == 0 {
			size = 50
		}
		return size * cfg.Storage["ssd_gb_month"]
	case "object_storage":
		size := attrFloat(vals, "size_gb")
		if size == 0 {
			size = 100
		}
		return size * cfg.Storage["object_gb_month"]
	case "snapshot":
		size := attrFloat(vals, "size_gb", "size")
		if size == 0 {
			size = 20
		}
		return size * cfg.Storage["snapshot_gb_month"]
	case "public_ip":
		return cfg.Network["public_ip_month"]
	case "nat_gateway":
		return cfg.Network["nat_gateway_hour"] * h
	case "load_balancer":
		return cfg.Network["load_balancer_hour"] * h
	case "vpn_gateway":
		return cfg.Network["vpn_gateway_month"]
	case "kubernetes_cluster":
		return cfg.Managed["kubernetes_cluster_month"]
	case "database":
		return cfg.Managed["database_instance_month"]
	case "dns_zone":
		return cfg.Managed["dns_zone_month"]
	case "cdn":
		gb := attrFloat(vals, "bandwidth_gb")
		if gb == 0 {
			gb = 100
		}
		return gb * cfg.Managed["cdn_gb"]
	case "waf":
		return cfg.Managed["waf_month"]
	}
	return 0
}

func costAction(actions []string) string {
	has := func(a string) bool {
		for _, x := range actions {
			if x == a {
				return true
			}
		}
		return false
	}
	switch {
	case has("create") && has("delete"):
		return "replace"
	case has("create"):
		return "create"
	case has("delete"):
		return "delete"
	case has("update"):
		return "update"
	}
	return "no-op"
}

// ---------------------------------------------------------------------------
// Estimation
// ---------------------------------------------------------------------------

func estimateCost(planRaw []byte, cfg *costConfig) (*CostResult, error) {
	var plan costPlanJSON
	if err := json.Unmarshal(planRaw, &plan); err != nil {
		return nil, err
	}
	res := &CostResult{Provider: cfg.Provider, Currency: cfg.Currency}
	for _, rc := range plan.ResourceChanges {
		act := costAction(rc.Change.Actions)
		if act == "no-op" {
			continue
		}
		kind := kindForType(rc.Type)
		if kind == "" {
			res.Unpriced++
			continue
		}
		before := monthlyFor(cfg, kind, rc.Change.Before)
		after := monthlyFor(cfg, kind, rc.Change.After)
		if act == "delete" {
			after = 0
		}
		if act == "create" {
			before = 0
		}
		delta := after - before
		if before == 0 && after == 0 {
			res.Unpriced++
			continue
		}
		res.Priced++
		res.MonthlyCurrent += before
		res.MonthlyPlanned += after
		res.Lines = append(res.Lines, CostLine{
			Address: rc.Address, Type: rc.Type, Kind: kind, Action: act,
			Before: round2(before), After: round2(after), Delta: round2(delta),
		})
	}
	res.MonthlyCurrent = round2(res.MonthlyCurrent)
	res.MonthlyPlanned = round2(res.MonthlyPlanned)
	res.MonthlyDelta = round2(res.MonthlyPlanned - res.MonthlyCurrent)
	res.YearlyDelta = round2(res.MonthlyDelta * 12)
	sort.SliceStable(res.Lines, func(i, j int) bool {
		return res.Lines[i].Delta > res.Lines[j].Delta
	})
	return res, nil
}

func round2(f float64) float64 {
	return float64(int64(f*100+signHalf(f))) / 100
}

func signHalf(f float64) float64 {
	if f < 0 {
		return -0.5
	}
	return 0.5
}

func signed(f float64) string {
	if f > 0 {
		return fmt.Sprintf("+%.2f", f)
	}
	return fmt.Sprintf("%.2f", f)
}

func formatCostReport(r *CostResult) string {
	var b strings.Builder
	b.WriteString(fmt.Sprintf("\n[cost] estimate for provider %s (%s, OpenSible pricing catalog)\n", r.Provider, r.Currency))
	if len(r.Lines) == 0 {
		b.WriteString("[cost] no billable resource changes detected in this plan.\n")
	}
	for _, l := range r.Lines {
		sym := "~"
		switch l.Action {
		case "create":
			sym = "+"
		case "delete":
			sym = "-"
		case "replace":
			sym = "±"
		}
		b.WriteString(fmt.Sprintf("[cost]   %s %-52s %-20s %s/mo\n", sym, l.Address, l.Kind, signed(l.Delta)))
	}
	if r.Unpriced > 0 {
		b.WriteString(fmt.Sprintf("[cost]   %d changed resource(s) are not billable or unknown to the catalog.\n", r.Unpriced))
	}
	b.WriteString(fmt.Sprintf("[cost] monthly delta: %s %s/mo  (current %.2f → projected %.2f)\n",
		signed(r.MonthlyDelta), r.Currency, r.MonthlyCurrent, r.MonthlyPlanned))
	b.WriteString(fmt.Sprintf("[cost] summary currency=%s current=%.2f projected=%.2f delta=%.2f yearly_delta=%.2f priced=%d unpriced=%d provider=%s\n",
		r.Currency, r.MonthlyCurrent, r.MonthlyPlanned, r.MonthlyDelta, r.YearlyDelta, r.Priced, r.Unpriced, r.Provider))
	b.WriteString("[cost] note: estimates only — list prices from the catalog, taxes/commitments/discounts excluded.\n")
	return b.String()
}
