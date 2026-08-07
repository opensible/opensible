import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import {
  BarChart, Bar, PieChart, Pie, Cell, ResponsiveContainer,
  XAxis, YAxis, Tooltip, CartesianGrid, LineChart, Line, Legend,
} from "recharts";
import {
  Plus, Trash2, Save, FileUp, Download, RefreshCw, AlertTriangle,
  TrendingUp, DollarSign, Calendar, Layers, Lightbulb,
} from "lucide-react";

import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

import awsLogo from "@/assets/aws-logo.svg";
import eksLogo from "@/assets/eks-logo.svg";
import gcpLogo from "@/assets/google-cloud.svg";
import gkeLogo from "@/assets/gke-logo.svg";
import azureLogo from "@/assets/azure-logo.png";
import cloudflareLogo from "@/assets/cloudflare.svg";
import hetznerLogo from "@/assets/hetzner-h.svg";
import kubernetesLogo from "@/assets/kubernetes-logo.svg";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/cloud/cost")({ component: CostAnalysisPage });

const PROVIDERS: { id: string; name: string; logo?: string; color: string }[] = [
  { id: "aws", name: "AWS", logo: awsLogo, color: "#ff9900" },
  { id: "eks", name: "Amazon EKS", logo: eksLogo, color: "#ff9900" },
  { id: "gcp", name: "Google Cloud", logo: gcpLogo, color: "#4285f4" },
  { id: "gke", name: "Google GKE", logo: gkeLogo, color: "#4285f4" },
  { id: "azure", name: "Azure", logo: azureLogo, color: "#0078d4" },
  { id: "hetzner", name: "Hetzner Cloud", logo: hetznerLogo, color: "#d50c2d" },
  { id: "cloudflare", name: "Cloudflare", logo: cloudflareLogo, color: "#f48120" },
  { id: "bytedc", name: "ByteDC", color: "#16a34a" },
  { id: "kubernetes", name: "Kubernetes", logo: kubernetesLogo, color: "#326ce5" },
];

function ProviderLogo({ id, size = 28 }: { id: string; size?: number }) {
  const p = PROVIDERS.find((x) => x.id === id);
  if (p?.logo) return <img src={p.logo} alt={p.name} style={{ height: size, width: size }} className="object-contain" />;
  return (
    <div
      className="rounded-md flex items-center justify-center text-white font-bold"
      style={{ height: size, width: size, background: p?.color || "#475569", fontSize: size * 0.42 }}
    >
      {(p?.name || "?").slice(0, 2).toUpperCase()}
    </div>
  );
}

type TabId = "pricing" | "calculator" | "reports";

function CostAnalysisPage() {
  const t = useT();
  const [tab, setTab] = useState<TabId>("pricing");
  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "Cost Analysis" }]} />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t("page.cost.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("page.cost.subtitle")}</p>
        </div>
      </div>

      <div className="border-b flex gap-1">
        {[
          { id: "pricing", label: "Provider Pricing Settings" },
          { id: "calculator", label: "Provisioning Cost Calculator" },
          { id: "reports", label: "Cost Reports" },
        ].map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id as TabId)}
            className={
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors " +
              (tab === t.id
                ? "border-[var(--color-primary)] text-[var(--color-primary)]"
                : "border-transparent text-muted-foreground hover:text-foreground")
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "pricing" && <PricingTab />}
      {tab === "calculator" && <CalculatorTab />}
      {tab === "reports" && <ReportsTab />}
    </div>
  );
}


/* ============================================================================
 * Tab 1 — Provider Pricing Settings
 * ========================================================================== */

type Catalog = {
  provider: string;
  currency: string;
  version: number;
  effective_date: string;
  updated_at: number;
  compute: { vcpu_hour: number; ram_gb_hour: number; gpu_hour: number; instance_templates: Array<{ name: string; vcpu: number; ram_gb: number; monthly: number }> };
  storage: { ssd_gb_month: number; hdd_gb_month: number; object_gb_month: number; snapshot_gb_month: number };
  network: { public_ip_month: number; bandwidth_gb: number; nat_gateway_hour: number; load_balancer_hour: number; vpn_gateway_month: number };
  managed: { kubernetes_cluster_month: number; database_instance_month: number; dns_zone_month: number; cdn_gb: number; waf_month: number };
};

function PricingTab() {
  const t = useT();
  const qc = useQueryClient();
  const [providerId, setProviderId] = useState("bytedc");
  const [draft, setDraft] = useState<Catalog | null>(null);

  const q = useQuery({
    queryKey: ["cost", "pricing", providerId],
    queryFn: async () => {
      const cat = await api<Catalog>("GET", `/api/cost/pricing/${providerId}`);
      setDraft(cat);
      return cat;
    },
  });

  const save = useMutation({
    mutationFn: async (cat: Catalog) => api<Catalog>("PUT", `/api/cost/pricing/${providerId}`, cat),
    onSuccess: (saved) => {
      setDraft(saved);
      qc.invalidateQueries({ queryKey: ["cost", "pricing", providerId] });
    },
  });

  const data = draft || q.data || null;
  const provider = PROVIDERS.find((p) => p.id === providerId)!;

  function setField(cat: "compute" | "storage" | "network" | "managed", field: string, value: number) {
    setDraft((d) => {
      if (!d) return d;
      const section = { ...(d[cat] as Record<string, unknown>), [field]: value };
      return { ...d, [cat]: section } as Catalog;
    });
  }

  return (
    <div className="grid grid-cols-12 gap-4">
      {/* Provider selector */}
      <Card className="col-span-12 lg:col-span-3 h-fit">
        <CardHeader className="py-3">
          <CardTitle className="text-sm">{t("cost.providers")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1 pt-0">
          {PROVIDERS.map((p) => (
            <button
              key={p.id}
              onClick={() => setProviderId(p.id)}
              className={
                "w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors " +
                (providerId === p.id ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)] font-medium" : "hover:bg-muted")
              }
            >
              <ProviderLogo id={p.id} size={28} />
              <span className="flex-1 text-left">{p.name}</span>
              {data && providerId === p.id && (
                <Badge variant="default" className="text-[10px]">v{data.version}</Badge>
              )}
            </button>
          ))}
        </CardContent>
      </Card>

      {/* Catalog editor */}
      <div className="col-span-12 lg:col-span-9 space-y-4">
        <Card>
          <CardContent className="p-4 flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-3">
              <ProviderLogo id={providerId} size={40} />
              <div>
                <div className="font-semibold">{provider.name} Pricing</div>
                <div className="text-xs text-muted-foreground">
                  {data ? <>v{data.version} · effective {data.effective_date} · currency {data.currency}</> : "Loading…"}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={() => q.refetch()}><RefreshCw className="h-4 w-4 mr-1" /> Reload</Button>
              <Button
                size="sm"
                disabled={!draft || save.isPending}
                onClick={() => draft && save.mutate(draft)}
              >
                <Save className="h-4 w-4 mr-1" /> {save.isPending ? "Saving…" : "Save Changes"}
              </Button>
            </div>
          </CardContent>
        </Card>

        {q.isLoading || !data ? (
          <Card><CardContent className="p-8 text-center text-sm text-muted-foreground">Loading pricing…</CardContent></Card>
        ) : (
          <>
            <CategoryCard title={t("page.cost.compute")}>
              <PriceField label="vCPU" unit="per core/hour" value={data.compute.vcpu_hour} onChange={(v) => setField("compute", "vcpu_hour", v)} />
              <PriceField label="RAM" unit="per GB/hour" value={data.compute.ram_gb_hour} onChange={(v) => setField("compute", "ram_gb_hour", v)} />
              <PriceField label="GPU" unit="per GPU/hour" value={data.compute.gpu_hour} onChange={(v) => setField("compute", "gpu_hour", v)} />
            </CategoryCard>

            <CategoryCard title={t("page.cost.storage")}>
              <PriceField label="SSD Block Storage" unit="GB/month" value={data.storage.ssd_gb_month} onChange={(v) => setField("storage", "ssd_gb_month", v)} />
              <PriceField label="HDD Storage" unit="GB/month" value={data.storage.hdd_gb_month} onChange={(v) => setField("storage", "hdd_gb_month", v)} />
              <PriceField label="Object Storage" unit="GB/month" value={data.storage.object_gb_month} onChange={(v) => setField("storage", "object_gb_month", v)} />
              <PriceField label="Snapshot Storage" unit="GB/month" value={data.storage.snapshot_gb_month} onChange={(v) => setField("storage", "snapshot_gb_month", v)} />
            </CategoryCard>

            <CategoryCard title={t("page.cost.network")}>
              <PriceField label="Public IP" unit="per IP/month" value={data.network.public_ip_month} onChange={(v) => setField("network", "public_ip_month", v)} />
              <PriceField label="Bandwidth" unit="per GB" value={data.network.bandwidth_gb} onChange={(v) => setField("network", "bandwidth_gb", v)} />
              <PriceField label="NAT Gateway" unit="hourly" value={data.network.nat_gateway_hour} onChange={(v) => setField("network", "nat_gateway_hour", v)} />
              <PriceField label="Load Balancer" unit="hourly" value={data.network.load_balancer_hour} onChange={(v) => setField("network", "load_balancer_hour", v)} />
              <PriceField label="VPN Gateway" unit="monthly" value={data.network.vpn_gateway_month} onChange={(v) => setField("network", "vpn_gateway_month", v)} />
            </CategoryCard>

            <CategoryCard title={t("page.cost.managed")}>
              <PriceField label="Kubernetes Cluster" unit="per month" value={data.managed.kubernetes_cluster_month} onChange={(v) => setField("managed", "kubernetes_cluster_month", v)} />
              <PriceField label="Database Instance" unit="per month" value={data.managed.database_instance_month} onChange={(v) => setField("managed", "database_instance_month", v)} />
              <PriceField label="DNS Zone" unit="per month" value={data.managed.dns_zone_month} onChange={(v) => setField("managed", "dns_zone_month", v)} />
              <PriceField label="CDN" unit="per GB" value={data.managed.cdn_gb} onChange={(v) => setField("managed", "cdn_gb", v)} />
              <PriceField label="WAF" unit="per month" value={data.managed.waf_month} onChange={(v) => setField("managed", "waf_month", v)} />
            </CategoryCard>
          </>
        )}
      </div>
    </div>
  );
}

function CategoryCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader className="py-3"><CardTitle className="text-sm">{title}</CardTitle></CardHeader>
      <CardContent className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 pt-0">
        {children}
      </CardContent>
    </Card>
  );
}

function PriceField({ label, unit, value, onChange }: { label: string; unit: string; value: number; onChange: (v: number) => void }) {
  // Local string buffer so users can freely type "0.", "0.0", clear the field, etc.
  // Only re-sync from `value` when the input is NOT focused — prevents the
  // upstream state from clobbering what the user is currently typing.
  const [text, setText] = useState<string>(() => String(value ?? 0));
  const focusedRef = useRef(false);
  const prevValueRef = useRef(value);

  if (!focusedRef.current && prevValueRef.current !== value) {
    prevValueRef.current = value;
    const incoming = String(value ?? 0);
    if (incoming !== text) queueMicrotask(() => setText(incoming));
  }

  return (
    <div className="border rounded-md p-3 bg-background">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-[11px] text-muted-foreground/80 mb-1">{unit}</div>
      <div className="flex items-center gap-1">
        <span className="text-sm text-muted-foreground">$</span>
        <Input
          type="text"
          inputMode="decimal"
          value={text}
          onFocus={() => { focusedRef.current = true; }}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "" || /^[0-9]*\.?[0-9]*$/.test(v)) {
              setText(v);
              // Don't propagate partial states ("", ".", "0.") — they'd be
              // parsed as 0/NaN and clobber the user's in-progress decimal.
              if (v !== "" && v !== "." && !v.endsWith(".")) {
                const n = parseFloat(v);
                if (Number.isFinite(n)) onChange(n);
              }
            }
          }}
          onBlur={() => {
            focusedRef.current = false;
            const n = parseFloat(text);
            const safe = Number.isFinite(n) ? n : 0;
            setText(String(safe));
            prevValueRef.current = safe;
            onChange(safe);
          }}
          className="h-8 text-sm"
        />
      </div>
    </div>
  );
}


/* ============================================================================
 * Tab 2 — Provisioning Cost Calculator
 * ========================================================================== */

type Resource = {
  id: string;
  kind: string;
  name: string;
  quantity: number;
  vcpu?: number;
  ram_gb?: number;
  size_gb?: number;
  bandwidth_gb?: number;
};

type EstimateResult = {
  provider: string;
  currency: string;
  monthly_total: number;
  yearly_total: number;
  one_time_total: number;
  line_items: Array<{ kind: string; name: string; quantity: number; unit: string; unit_price: number; monthly: number; yearly: number }>;
  warnings: string[];
  insights: string[];
  computed_at: number;
};

const RESOURCE_KINDS: { value: string; label: string; needsCpu?: boolean; needsSize?: boolean; needsBw?: boolean }[] = [
  { value: "instance", label: "Compute Instance", needsCpu: true },
  { value: "gpu", label: "GPU" },
  { value: "ssd", label: "SSD Storage", needsSize: true },
  { value: "hdd", label: "HDD Storage", needsSize: true },
  { value: "object_storage", label: "Object Storage", needsSize: true },
  { value: "snapshot", label: "Snapshot", needsSize: true },
  { value: "public_ip", label: "Public IP" },
  { value: "bandwidth", label: "Bandwidth", needsBw: true },
  { value: "nat_gateway", label: "NAT Gateway" },
  { value: "load_balancer", label: "Load Balancer" },
  { value: "vpn_gateway", label: "VPN Gateway" },
  { value: "kubernetes_cluster", label: "Kubernetes Cluster" },
  { value: "database", label: "Database Instance" },
  { value: "dns_zone", label: "DNS Zone" },
  { value: "cdn", label: "CDN", needsBw: true },
  { value: "waf", label: "WAF" },
];

function uid() { return Math.random().toString(36).slice(2, 10); }

function CalculatorTab() {
  const t = useT();
  const [providerId, setProviderId] = useState("bytedc");
  const [source, setSource] = useState<"stack" | "plan" | "git" | "manual">("manual");
  const [stackId, setStackId] = useState<string>("");
  const [planText, setPlanText] = useState<string>("");
  const [resources, setResources] = useState<Resource[]>([
    { id: uid(), kind: "instance", name: "web-1", quantity: 2, vcpu: 4, ram_gb: 8 },
    { id: uid(), kind: "load_balancer", name: "edge-lb", quantity: 1 },
    { id: uid(), kind: "ssd", name: "data-vol", quantity: 1, size_gb: 500 },
    { id: uid(), kind: "object_storage", name: "assets", quantity: 1, size_gb: 300 },
    { id: uid(), kind: "public_ip", name: "ingress-ip", quantity: 2 },
    { id: uid(), kind: "nat_gateway", name: "egress", quantity: 1 },
  ]);
  const [result, setResult] = useState<EstimateResult | null>(null);
  const [search, setSearch] = useState("");

  const stacksQ = useQuery({
    queryKey: ["cloud", "stacks"],
    queryFn: () => api<{ stacks: Array<{ stack: string; provider: string }> }>("GET", "/api/cloud/stacks").catch(() => ({ stacks: [] })),
    enabled: source === "stack",
  });

  const estimate = useMutation({
    mutationFn: async () =>
      api<EstimateResult>("POST", "/api/cost/estimate", {
        provider: providerId,
        resources: resources.map(({ id: _id, ...r }) => r),
      }),
    onSuccess: (r) => setResult(r),
  });

  const extract = useMutation({
    mutationFn: async () => {
      let plan: unknown;
      try { plan = JSON.parse(planText); } catch { throw new Error("Plan must be valid JSON"); }
      return api<{ resources: Array<Omit<Resource, "id">> }>("POST", "/api/cost/extract/plan", { plan });
    },
    onSuccess: (r) => setResources(r.resources.map((x) => ({ id: uid(), ...x }))),
  });

  const saveEstimate = useMutation({
    mutationFn: async () =>
      api("POST", "/api/cost/estimates", {
        provider: providerId,
        source,
        stack_id: stackId || null,
        result,
        resources,
      }),
  });

  function update(id: string, patch: Partial<Resource>) {
    setResources((rs) => rs.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }
  function add() {
    setResources((rs) => [...rs, { id: uid(), kind: "instance", name: "new-resource", quantity: 1, vcpu: 2, ram_gb: 4 }]);
  }
  function remove(id: string) {
    setResources((rs) => rs.filter((r) => r.id !== id));
  }

  function exportCsv() {
    if (!result) return;
    const rows = [
      ["Resource", "Kind", "Quantity", "Unit", "Unit Price", "Monthly", "Yearly"],
      ...result.line_items.map((li) => [li.name, li.kind, li.quantity, li.unit, li.unit_price, li.monthly, li.yearly]),
    ];
    const csv = rows.map((r) => r.map((c) => JSON.stringify(c ?? "")).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `cost-estimate-${providerId}-${Date.now()}.csv`; a.click();
    URL.revokeObjectURL(url);
  }

  // Derived analytics
  const summary = useMemo(() => {
    if (!result) return null;
    const byKind = new Map<string, number>();
    for (const li of result.line_items) byKind.set(li.kind, (byKind.get(li.kind) || 0) + li.monthly);
    const pie = Array.from(byKind, ([name, value]) => ({ name, value: Math.round(value * 100) / 100 }));
    const months = Array.from({ length: 12 }, (_, i) => ({
      m: new Date(2025, i, 1).toLocaleString("en-US", { month: "short" }),
      cost: Math.round(result.monthly_total * (1 + i * 0.02) * 100) / 100,
    }));
    return { pie, months };
  }, [result]);

  const filtered = useMemo(() => {
    if (!result) return [];
    if (!search) return result.line_items;
    const s = search.toLowerCase();
    return result.line_items.filter((li) => li.name.toLowerCase().includes(s) || li.kind.toLowerCase().includes(s));
  }, [result, search]);

  const provider = PROVIDERS.find((p) => p.id === providerId)!;

  return (
    <div className="space-y-4">
      {/* Step 1 — source & provider */}
      <Card>
        <CardHeader className="py-3"><CardTitle className="text-sm">{t("cost.step1")}</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-0">
          <div>
            <div className="text-xs text-muted-foreground mb-1">Provider</div>
            <Select
              value={providerId}
              onChange={setProviderId}
              options={PROVIDERS.map((p) => ({ value: p.id, label: p.name }))}
              prefix={<ProviderLogo id={providerId} size={18} />}
            />
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-1">Resource source</div>
            <Select
              value={source}
              onChange={(v) => setSource(v as typeof source)}
              options={[
                { value: "manual", label: "Manual design" },
                { value: "stack", label: "Existing stack" },
                { value: "plan", label: "OpenTofu / Terraform plan" },
                { value: "git", label: "Git repository (coming soon)", disabled: true },
              ]}
            />
          </div>
          {source === "stack" && (
            <div>
              <div className="text-xs text-muted-foreground mb-1">Stack</div>
              <Select
                value={stackId}
                onChange={setStackId}
                placeholder="Select a stack…"
                options={(stacksQ.data?.stacks || []).map((s) => ({ value: s.stack, label: `${s.stack} (${s.provider})` }))}
              />
            </div>
          )}
        </CardContent>

        {source === "plan" && (
          <CardContent className="pt-0">
            <div className="text-xs text-muted-foreground mb-1">Paste OpenTofu/Terraform plan JSON</div>
            <textarea
              className="w-full h-32 font-mono text-xs p-2 border rounded-md bg-background"
              value={planText}
              onChange={(e) => setPlanText(e.target.value)}
              placeholder='Output of `tofu show -json tfplan` …'
            />
            <div className="flex gap-2 mt-2">
              <Button size="sm" variant="outline" onClick={() => extract.mutate()} disabled={!planText || extract.isPending}>
                <FileUp className="h-4 w-4 mr-1" /> {extract.isPending ? "Parsing…" : "Extract resources"}
              </Button>
              {extract.error && <span className="text-xs text-red-500">{(extract.error as Error).message}</span>}
            </div>
          </CardContent>
        )}
      </Card>

      {/* Step 2 — resources */}
      <Card>
        <CardHeader className="py-3 flex flex-row items-center justify-between">
          <CardTitle className="text-sm">{t("cost.step2")}</CardTitle>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={add}><Plus className="h-4 w-4 mr-1" /> Add resource</Button>
            <Button size="sm" onClick={() => estimate.mutate()} disabled={estimate.isPending || resources.length === 0}>
              <TrendingUp className="h-4 w-4 mr-1" /> {estimate.isPending ? "Calculating…" : "Calculate Cost"}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="pt-0 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr className="border-b">
                <th className="text-left p-2">Name</th>
                <th className="text-left p-2">Kind</th>
                <th className="text-left p-2 w-20">Qty</th>
                <th className="text-left p-2 w-24">vCPU</th>
                <th className="text-left p-2 w-24">RAM (GB)</th>
                <th className="text-left p-2 w-28">Size (GB)</th>
                <th className="text-left p-2 w-28">BW (GB)</th>
                <th className="w-12"></th>
              </tr>
            </thead>
            <tbody>
              {resources.map((r) => {
                const kindDef = RESOURCE_KINDS.find((k) => k.value === r.kind);
                return (
                  <tr key={r.id} className="border-b last:border-0">
                    <td className="p-1"><Input value={r.name} onChange={(e) => update(r.id, { name: e.target.value })} className="h-8" /></td>
                    <td className="p-1">
                      <Select
                        value={r.kind}
                        onChange={(v) => update(r.id, { kind: v })}
                        options={RESOURCE_KINDS.map((k) => ({ value: k.value, label: k.label }))}
                        triggerClassName="h-8"
                      />
                    </td>
                    <td className="p-1"><Input type="number" min={1} value={r.quantity} onChange={(e) => update(r.id, { quantity: Number(e.target.value) || 1 })} className="h-8" /></td>
                    <td className="p-1"><Input type="number" min={0} value={r.vcpu ?? ""} disabled={!kindDef?.needsCpu} onChange={(e) => update(r.id, { vcpu: Number(e.target.value) || 0 })} className="h-8" /></td>
                    <td className="p-1"><Input type="number" min={0} value={r.ram_gb ?? ""} disabled={!kindDef?.needsCpu} onChange={(e) => update(r.id, { ram_gb: Number(e.target.value) || 0 })} className="h-8" /></td>
                    <td className="p-1"><Input type="number" min={0} value={r.size_gb ?? ""} disabled={!kindDef?.needsSize} onChange={(e) => update(r.id, { size_gb: Number(e.target.value) || 0 })} className="h-8" /></td>
                    <td className="p-1"><Input type="number" min={0} value={r.bandwidth_gb ?? ""} disabled={!kindDef?.needsBw} onChange={(e) => update(r.id, { bandwidth_gb: Number(e.target.value) || 0 })} className="h-8" /></td>
                    <td className="p-1 text-right">
                      <Button size="icon" variant="ghost" onClick={() => remove(r.id)}><Trash2 className="h-4 w-4 text-red-500" /></Button>
                    </td>
                  </tr>
                );
              })}
              {resources.length === 0 && (
                <tr><td colSpan={8} className="p-6 text-center text-muted-foreground text-sm">No resources — click "Add resource" to start.</td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {/* Results */}
      {result && summary && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <KpiCard icon={<DollarSign className="h-4 w-4" />} label="Monthly Cost" value={`$${result.monthly_total.toLocaleString()}`} accent={provider.color} />
            <KpiCard icon={<Calendar className="h-4 w-4" />} label="Yearly Cost" value={`$${result.yearly_total.toLocaleString()}`} accent={provider.color} />
            <KpiCard icon={<Layers className="h-4 w-4" />} label="Resources" value={String(result.line_items.length)} accent={provider.color} />
            <KpiCard icon={<TrendingUp className="h-4 w-4" />} label="Provider" value={provider.name} accent={provider.color} />
          </div>

          {(result.insights.length > 0 || result.warnings.length > 0) && (
            <Card>
              <CardContent className="p-4 space-y-2">
                {result.insights.map((i, idx) => (
                  <div key={"i" + idx} className="flex items-start gap-2 text-sm">
                    <Lightbulb className="h-4 w-4 text-amber-500 mt-0.5 shrink-0" /><span>{i}</span>
                  </div>
                ))}
                {result.warnings.map((w, idx) => (
                  <div key={"w" + idx} className="flex items-start gap-2 text-sm">
                    <AlertTriangle className="h-4 w-4 text-orange-500 mt-0.5 shrink-0" /><span>{w}</span>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <CardHeader className="py-3"><CardTitle className="text-sm">{t("cost.costByService")}</CardTitle></CardHeader>
              <CardContent className="pt-0 h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={summary.pie} dataKey="value" nameKey="name" outerRadius={90} label>
                      {summary.pie.map((_, i) => (
                        <Cell key={i} fill={["#16a34a", "#0ea5e9", "#f59e0b", "#ef4444", "#8b5cf6", "#14b8a6", "#f43f5e", "#64748b"][i % 8]} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(v: number) => `$${v.toLocaleString()}`} />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="py-3"><CardTitle className="text-sm">12-month forecast</CardTitle></CardHeader>
              <CardContent className="pt-0 h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={summary.months}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                    <XAxis dataKey="m" fontSize={11} />
                    <YAxis fontSize={11} />
                    <Tooltip formatter={(v: number) => `$${v.toLocaleString()}`} />
                    <Line type="monotone" dataKey="cost" stroke={provider.color} strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>

            <Card className="lg:col-span-2">
              <CardHeader className="py-3"><CardTitle className="text-sm">{t("cost.monthlyByResource")}</CardTitle></CardHeader>
              <CardContent className="pt-0 h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={[...result.line_items].sort((a, b) => b.monthly - a.monthly).slice(0, 12)}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                    <XAxis dataKey="name" fontSize={11} />
                    <YAxis fontSize={11} />
                    <Tooltip formatter={(v: number) => `$${v.toLocaleString()}`} />
                    <Bar dataKey="monthly" fill={provider.color} />
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader className="py-3 flex flex-row items-center justify-between gap-3 flex-wrap">
              <CardTitle className="text-sm">{t("cost.detailedBreakdown")}</CardTitle>
              <div className="flex items-center gap-2">
                <Input placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} className="h-8 w-48" />
                <Button size="sm" variant="outline" onClick={exportCsv}><Download className="h-4 w-4 mr-1" /> Export CSV</Button>
                <Button size="sm" onClick={() => saveEstimate.mutate()} disabled={saveEstimate.isPending}>
                  <Save className="h-4 w-4 mr-1" /> {saveEstimate.isPending ? "Saving…" : "Save estimate"}
                </Button>
              </div>
            </CardHeader>
            <CardContent className="pt-0 overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs text-muted-foreground">
                  <tr className="border-b">
                    <th className="text-left p-2">Resource</th>
                    <th className="text-left p-2">Kind</th>
                    <th className="text-right p-2">Qty</th>
                    <th className="text-left p-2">Unit</th>
                    <th className="text-right p-2">Unit Price</th>
                    <th className="text-right p-2">Monthly</th>
                    <th className="text-right p-2">Yearly</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((li, idx) => (
                    <tr key={idx} className="border-b last:border-0">
                      <td className="p-2 font-medium">{li.name}</td>
                      <td className="p-2"><Badge variant="default">{li.kind}</Badge></td>
                      <td className="p-2 text-right">{li.quantity}</td>
                      <td className="p-2 text-muted-foreground">{li.unit}</td>
                      <td className="p-2 text-right">${li.unit_price.toFixed(4)}</td>
                      <td className="p-2 text-right font-semibold">${li.monthly.toLocaleString()}</td>
                      <td className="p-2 text-right">${li.yearly.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="font-semibold">
                    <td className="p-2" colSpan={5}>Total</td>
                    <td className="p-2 text-right">${result.monthly_total.toLocaleString()}</td>
                    <td className="p-2 text-right">${result.yearly_total.toLocaleString()}</td>
                  </tr>
                </tfoot>
              </table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function KpiCard({ icon, label, value, accent }: { icon: React.ReactNode; label: string; value: string; accent: string }) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span style={{ color: accent }}>{icon}</span>
          {label}
        </div>
        <div className="text-2xl font-bold mt-1">{value}</div>
      </CardContent>
    </Card>
  );
}

/* ============================================================================
 * Tab 3 — Cost Reports (auto-generated on every Apply, plus manual saves)
 * ========================================================================== */

type ReportRow = {
  id: string;
  filename: string;
  created_at: number;
  provider: string;
  stack: string;
  env?: string | null;
  cloud_project?: string | null;
  source: string;
  run_id?: string | null;
  monthly_total: number;
  yearly_total: number;
  currency: string;
  resource_count: number;
};

type ReportDetail = ReportRow & {
  resources: Array<Record<string, unknown>>;
  result: EstimateResult;
};

function ReportsTab() {
  const qc = useQueryClient();
  const [stackFilter, setStackFilter] = useState<string>("");
  const [providerFilter, setProviderFilter] = useState<string>("");
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");
  const [open, setOpen] = useState<ReportDetail | null>(null);

  const list = useQuery({
    queryKey: ["cost", "reports"],
    queryFn: () => api<{ reports: ReportRow[] }>("GET", "/api/cost/reports"),
    refetchInterval: 15_000,
  });

  const del = useMutation({
    mutationFn: (id: string) => api("DELETE", `/api/cost/reports/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cost", "reports"] }),
  });

  const view = useMutation({
    mutationFn: (id: string) => api<ReportDetail>("GET", `/api/cost/reports/${id}`),
    onSuccess: (r) => setOpen(r),
  });

  const rows = list.data?.reports ?? [];
  const stacks = useMemo(() => Array.from(new Set(rows.map((r) => r.stack))).sort(), [rows]);
  const providers = useMemo(() => Array.from(new Set(rows.map((r) => r.provider))).sort(), [rows]);

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (stackFilter && r.stack !== stackFilter) return false;
      if (providerFilter && r.provider !== providerFilter) return false;
      const t = r.created_at * 1000;
      if (dateFrom && t < new Date(dateFrom).getTime()) return false;
      if (dateTo && t > new Date(dateTo).getTime() + 86_400_000) return false;
      return true;
    });
  }, [rows, stackFilter, providerFilter, dateFrom, dateTo]);

  function exportCsv() {
    const head = ["Date", "Stack", "Provider", "Env", "Cloud Project", "Source", "Resources", "Monthly (USD)", "Yearly (USD)"];
    const body = filtered.map((r) => [
      new Date(r.created_at * 1000).toISOString(),
      r.stack, r.provider, r.env ?? "", r.cloud_project ?? "",
      r.source, r.resource_count, r.monthly_total, r.yearly_total,
    ]);
    const csv = [head, ...body].map((row) => row.map((c) => JSON.stringify(c ?? "")).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `cost-reports-${Date.now()}.csv`; a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="p-4 flex flex-wrap items-end gap-3">
          <div className="min-w-[180px]">
            <div className="text-xs text-muted-foreground mb-1">Stack</div>
            <Select
              value={stackFilter}
              onChange={setStackFilter}
              placeholder="All stacks"
              options={[{ value: "", label: "All stacks" }, ...stacks.map((s) => ({ value: s, label: s }))]}
            />
          </div>
          <div className="min-w-[160px]">
            <div className="text-xs text-muted-foreground mb-1">Provider</div>
            <Select
              value={providerFilter}
              onChange={setProviderFilter}
              placeholder="All providers"
              options={[{ value: "", label: "All providers" }, ...providers.map((p) => ({ value: p, label: p }))]}
            />
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-1">From</div>
            <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="h-9" />
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-1">To</div>
            <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="h-9" />
          </div>
          <div className="ml-auto flex gap-2">
            <Button variant="outline" size="sm" onClick={() => list.refetch()}>
              <RefreshCw className="h-4 w-4 mr-1" /> Refresh
            </Button>
            <Button variant="outline" size="sm" onClick={exportCsv} disabled={!filtered.length}>
              <Download className="h-4 w-4 mr-1" /> Export CSV
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="py-3 flex flex-row items-center justify-between">
          <CardTitle className="text-sm">{filtered.length} reports</CardTitle>
          <div className="text-xs text-muted-foreground">Auto-saved after each successful Apply</div>
        </CardHeader>
        <CardContent className="p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b text-xs uppercase tracking-wider text-muted-foreground">
                <th className="px-4 py-2 font-medium">Date / Time</th>
                <th className="px-4 py-2 font-medium">Stack</th>
                <th className="px-4 py-2 font-medium">Provider</th>
                <th className="px-4 py-2 font-medium">Env</th>
                <th className="px-4 py-2 font-medium">Source</th>
                <th className="px-4 py-2 font-medium text-right">Resources</th>
                <th className="px-4 py-2 font-medium text-right">Monthly</th>
                <th className="px-4 py-2 font-medium text-right">Yearly</th>
                <th className="px-4 py-2 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {list.isLoading && (
                <tr><td colSpan={9} className="px-4 py-6 text-center text-muted-foreground">Loading…</td></tr>
              )}
              {!list.isLoading && filtered.length === 0 && (
                <tr><td colSpan={9} className="px-4 py-8 text-center text-muted-foreground">
                  No cost reports yet. Run <strong>Apply</strong> on a stack and open <strong>Inventory Resources</strong> — a timestamped report will appear here automatically.
                </td></tr>
              )}
              {filtered.map((r) => (
                <tr key={r.id} className="border-b hover:bg-muted/40">
                  <td className="px-4 py-2 font-mono text-xs whitespace-nowrap">
                    {new Date(r.created_at * 1000).toLocaleString()}
                  </td>
                  <td className="px-4 py-2 font-medium">{r.stack}</td>
                  <td className="px-4 py-2">
                    <div className="flex items-center gap-2">
                      <ProviderLogo id={r.provider} size={18} />
                      <span className="text-xs">{r.provider}</span>
                    </div>
                  </td>
                  <td className="px-4 py-2 text-xs">{r.env || "—"}</td>
                  <td className="px-4 py-2"><Badge variant="default" className="text-[10px]">{r.source}</Badge></td>
                  <td className="px-4 py-2 text-right">{r.resource_count}</td>
                  <td className="px-4 py-2 text-right font-medium">${r.monthly_total.toFixed(2)}</td>
                  <td className="px-4 py-2 text-right">${r.yearly_total.toFixed(2)}</td>
                  <td className="px-4 py-2 text-right whitespace-nowrap">
                    <Button size="sm" variant="ghost" onClick={() => view.mutate(r.id)}>View</Button>
                    <Button size="sm" variant="ghost" onClick={() => del.mutate(r.id)}>
                      <Trash2 className="h-3.5 w-3.5 text-red-500" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {open && <ReportDetailModal report={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function ReportDetailModal({ report, onClose }: { report: ReportDetail; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="bg-card rounded-lg shadow-2xl w-full max-w-4xl border max-h-[90vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-3 border-b flex items-center justify-between">
          <div>
            <div className="text-base font-semibold">{report.stack} — cost report</div>
            <div className="text-xs text-muted-foreground">
              {new Date(report.created_at * 1000).toLocaleString()} · {report.provider} · {report.source}
              {report.run_id ? ` · run ${report.run_id.slice(0, 8)}` : ""}
            </div>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground text-xl leading-none">×</button>
        </div>
        <div className="p-5 overflow-y-auto space-y-4">
          <div className="grid grid-cols-3 gap-3">
            <div className="border rounded-md p-3">
              <div className="text-xs text-muted-foreground">Monthly</div>
              <div className="text-2xl font-bold">${report.monthly_total.toFixed(2)}</div>
            </div>
            <div className="border rounded-md p-3">
              <div className="text-xs text-muted-foreground">Yearly</div>
              <div className="text-2xl font-bold">${report.yearly_total.toFixed(2)}</div>
            </div>
            <div className="border rounded-md p-3">
              <div className="text-xs text-muted-foreground">Resources</div>
              <div className="text-2xl font-bold">{report.resource_count}</div>
            </div>
          </div>

          <table className="w-full text-sm border rounded-md overflow-hidden">
            <thead>
              <tr className="text-left bg-muted/40 text-xs uppercase tracking-wider text-muted-foreground">
                <th className="px-3 py-2 font-medium">Resource</th>
                <th className="px-3 py-2 font-medium">Kind</th>
                <th className="px-3 py-2 font-medium text-right">Qty</th>
                <th className="px-3 py-2 font-medium">Unit</th>
                <th className="px-3 py-2 font-medium text-right">Unit Price</th>
                <th className="px-3 py-2 font-medium text-right">Monthly</th>
                <th className="px-3 py-2 font-medium text-right">Yearly</th>
              </tr>
            </thead>
            <tbody>
              {report.result.line_items.map((li, i) => (
                <tr key={i} className="border-t">
                  <td className="px-3 py-2 font-medium">{li.name}</td>
                  <td className="px-3 py-2 text-xs">{li.kind}</td>
                  <td className="px-3 py-2 text-right">{li.quantity}</td>
                  <td className="px-3 py-2 text-xs">{li.unit}</td>
                  <td className="px-3 py-2 text-right">${li.unit_price.toFixed(4)}</td>
                  <td className="px-3 py-2 text-right font-medium">${li.monthly.toFixed(2)}</td>
                  <td className="px-3 py-2 text-right">${li.yearly.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {report.result.insights?.length > 0 && (
            <div className="border rounded-md p-3 bg-muted/30">
              <div className="text-sm font-medium flex items-center gap-1"><Lightbulb className="h-4 w-4" /> Insights</div>
              <ul className="text-xs mt-2 space-y-1 list-disc pl-5">
                {report.result.insights.map((i, k) => <li key={k}>{i}</li>)}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
