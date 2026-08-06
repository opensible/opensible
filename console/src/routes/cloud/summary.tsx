import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Terminal, X, Download } from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge, statusToVariant } from "@/components/ui/badge";
import { LogViewer } from "@/components/cloud/LogViewer";
import { PlanDiff } from "@/components/cloud/PlanDiff";
import { RunFlowGraph } from "@/components/cloud/RunFlowGraph";
import { api } from "@/lib/api";
import { qk } from "@/lib/query";
import { cn } from "@/lib/utils";
import { ProviderCell } from "@/lib/providers";
import { Pager } from "@/components/ui/pager";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/cloud/summary")({ component: SummaryPage });

type Run = {
  run_id: string;
  stack: string;
  action: string;
  status: string;
  env?: string | null;
  cloud_project?: string | null;
  provider?: string | null;
  started_at?: number;
  finished_at?: number;
  mtime?: number;
  returncode?: number | null;
};

type RunDetail = Run & { log?: string };

function fmtRel(ts: number | undefined, t: (k: string, v?: Record<string, string | number>) => string): string {
  if (!ts) return "—";
  const diff = Math.floor(Date.now() / 1000) - Number(ts);
  if (diff < 5) return t("summary.justNow");
  if (diff < 60) return t("summary.agoSeconds", { n: diff });
  if (diff < 3600) return t("summary.agoMinutes", { n: Math.floor(diff / 60) });
  if (diff < 86400) return t("summary.agoHours", { n: Math.floor(diff / 3600) });
  return t("summary.agoDays", { n: Math.floor(diff / 86400) });
}
function fmtDateTime(ts?: number): string {
  if (!ts) return "—";
  try { return new Date(Number(ts) * 1000).toLocaleString(); } catch { return "—"; }
}
function fmtDur(start?: number, end?: number): string {
  if (!start) return "—";
  const e = end || Math.floor(Date.now() / 1000);
  const d = Math.max(0, e - start);
  const m = Math.floor(d / 60), s = d % 60;
  return m ? `${m}m ${s}s` : `${s}s`;
}

function ProviderBadge({ provider }: { provider: string }) {
  return <ProviderCell id={provider} />;
}

function SummaryPage() {
  const t = useT();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("");
  const [action, setAction] = useState("");
  const [providerFilter, setProviderFilter] = useState("");
  const [stackFilter, setStackFilter] = useState("");
  const [cloudProjectFilter, setCloudProjectFilter] = useState("");
  const [envFilter, setEnvFilter] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<{ stack: string; runId: string } | null>(null);
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 15;

  const runsQuery = useQuery({
    queryKey: qk.runs,
    queryFn: () => api<{ runs: Run[] }>("GET", "/api/cloud/runs"),
    refetchInterval: 4000,
  });
  const runs = runsQuery.data?.runs ?? [];

  function loadRuns() {
    queryClient.invalidateQueries({ queryKey: qk.runs });
  }

  const providerOptions = Array.from(new Set(runs.map(r => r.provider).filter(Boolean) as string[])).sort();
  const stackOptions = Array.from(new Set(runs.map(r => r.stack).filter(Boolean))).sort();
  const cloudProjectOptions = Array.from(new Set(runs.map(r => r.cloud_project).filter(Boolean) as string[])).sort();
  const envOptions = Array.from(new Set(runs.map(r => r.env).filter(Boolean) as string[])).sort();

  const filtered = runs.filter(r => {
    if (status && r.status !== status) return false;
    if (action && r.action !== action) return false;
    if (providerFilter && (r.provider || "") !== providerFilter) return false;
    if (stackFilter && r.stack !== stackFilter) return false;
    if (cloudProjectFilter && r.cloud_project !== cloudProjectFilter) return false;
    if (envFilter && r.env !== envFilter) return false;
    if (q) {
      const blob = `${r.stack} ${r.run_id} ${r.action} ${r.env ?? ""} ${r.cloud_project ?? ""} ${r.provider ?? ""}`.toLowerCase();
      if (!blob.includes(q.toLowerCase())) return false;
    }
    return true;
  });

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  useEffect(() => { setPage(1); }, [q, status, action, providerFilter, stackFilter, cloudProjectFilter, envFilter]);
  const pageItems = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);


  const counts = {
    total: runs.length,
    running: runs.filter(r => r.status === "running").length,
    queued: runs.filter(r => r.status === "queued").length,
    succeeded: runs.filter(r => r.status === "succeeded").length,
    failed: runs.filter(r => r.status === "failed").length,
  };

  const statCards = [
    { key: "total", label: t("summary.total"), value: counts.total, tone: "neutral" },
    { key: "running", label: t("summary.running"), value: counts.running, tone: "info" },
    { key: "queued", label: t("summary.queued"), value: counts.queued, tone: "muted" },
    { key: "succeeded", label: t("summary.succeeded"), value: counts.succeeded, tone: "success" },
    { key: "failed", label: t("summary.failed"), value: counts.failed, tone: "danger" },
  ];
  const toneClass = (t: string) =>
    t === "success" ? "text-emerald-600" :
    t === "danger" ? "text-red-600" :
    t === "info" ? "text-blue-600" :
    t === "muted" ? "text-amber-600" :
    "text-[var(--color-foreground)]";

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: t("nav.summary") }]} />
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">{t("page.summary.title")}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)] mt-1">{t("page.summary.subtitle")}</p>
        </div>
        <Button variant="outline" size="sm" onClick={loadRuns}><RefreshCw className="h-4 w-4" /> {t("common.refresh")}</Button>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        {statCards.map(s => (
          <div key={s.key} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-3">
            <div className="text-xs uppercase tracking-wide text-[var(--color-muted-foreground)]">{s.label}</div>
            <div className={cn("text-2xl font-semibold mt-1", toneClass(s.tone))}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3 space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <Input
            placeholder={t("summary.searchPlaceholder")}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="max-w-xs"
          />
          <select className="h-9 px-2 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">{t("summary.allStatuses")}</option>
            <option>running</option><option>queued</option>
            <option>succeeded</option><option>failed</option><option>canceled</option>
          </select>
          <select className="h-9 px-2 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] text-sm" value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">{t("summary.allActions")}</option>
            <option>init</option><option>plan</option><option>apply</option>
            <option>destroy</option><option>validate</option><option>fmt</option><option>refresh</option>
          </select>
          <select className="h-9 px-2 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] text-sm" value={providerFilter} onChange={(e) => setProviderFilter(e.target.value)}>
            <option value="">{t("summary.allProviders")}</option>
            {providerOptions.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="h-9 px-2 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] text-sm" value={cloudProjectFilter} onChange={(e) => setCloudProjectFilter(e.target.value)}>
            <option value="">{t("summary.allCloudProjects")}</option>
            {cloudProjectOptions.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="h-9 px-2 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] text-sm" value={envFilter} onChange={(e) => setEnvFilter(e.target.value)}>
            <option value="">{t("summary.allEnvironments")}</option>
            {envOptions.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="h-9 px-2 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] text-sm" value={stackFilter} onChange={(e) => setStackFilter(e.target.value)}>
            <option value="">{t("summary.allStacks")}</option>
            {stackOptions.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <span className="ml-auto text-xs text-[var(--color-muted-foreground)]">{t("summary.runsCount", { shown: filtered.length, total: runs.length })}</span>
        </div>
      </div>

      {/* Full table */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-[var(--color-muted)]/40 text-xs uppercase tracking-wide text-[var(--color-muted-foreground)]">
              <tr>
                <th className="text-left px-3 py-2.5 font-medium">{t("summary.col.provider")}</th>
                <th className="text-left px-3 py-2.5 font-medium">{t("summary.col.project")}</th>
                <th className="text-left px-3 py-2.5 font-medium">{t("summary.col.env")}</th>
                <th className="text-left px-3 py-2.5 font-medium">{t("summary.col.dateTime")}</th>
                <th className="text-left px-3 py-2.5 font-medium">{t("summary.col.job")}</th>
                <th className="text-left px-3 py-2.5 font-medium">{t("summary.col.duration")}</th>
                <th className="text-left px-3 py-2.5 font-medium">{t("summary.col.age")}</th>
                <th className="text-left px-3 py-2.5 font-medium">{t("summary.col.status")}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-3 py-10 text-center text-[var(--color-muted-foreground)]">
                    {t("summary.noRuns")}
                  </td>
                </tr>
              )}
              {pageItems.map(r => (
                <tr
                  key={r.run_id}
                  onClick={() => setSelected({ stack: r.stack, runId: r.run_id })}
                  className="border-t border-[var(--color-border)] hover:bg-[var(--color-muted)]/40 cursor-pointer transition-colors"
                >
                  <td className="px-3 py-2.5">
                    <ProviderBadge provider={r.provider || "bytedc"} />
                  </td>
                  <td className="px-3 py-2.5 font-medium">{r.cloud_project || <span className="text-[var(--color-muted-foreground)]">—</span>}</td>
                  <td className="px-3 py-2.5">{r.env || <span className="text-[var(--color-muted-foreground)]">—</span>}</td>
                  <td className="px-3 py-2.5 whitespace-nowrap text-[var(--color-muted-foreground)]">{fmtDateTime(r.started_at || r.mtime)}</td>
                  <td className="px-3 py-2.5">
                    <div className="font-mono text-xs">tofu {r.action} · {r.stack}</div>
                    <div className="text-[10px] text-[var(--color-muted-foreground)] font-mono">{r.run_id.slice(0, 12)}</div>
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap text-[var(--color-muted-foreground)]">{fmtDur(r.started_at, r.finished_at)}</td>
                  <td className="px-3 py-2.5 whitespace-nowrap text-[var(--color-muted-foreground)]">{fmtRel(r.mtime || r.started_at, t)}</td>
                  <td className="px-3 py-2.5"><Badge variant={statusToVariant(r.status)}>{r.status}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {filtered.length > 0 && (
          <Pager
            page={currentPage}
            totalPages={totalPages}
            total={filtered.length}
            pageSize={PAGE_SIZE}
            onPage={setPage}
          />
        )}
      </div>

      {selected && (
        <ProvisioningLogDialog
          stack={selected.stack}
          runId={selected.runId}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

export function ProvisioningLogDialog({
  stack, runId, onClose,
}: { stack: string; runId: string; onClose: () => void }) {
  const t = useT();
  const detailQuery = useQuery({
    queryKey: qk.run(stack, runId),
    queryFn: () => api<RunDetail>("GET", `/api/cloud/stacks/${encodeURIComponent(stack)}/runs/${encodeURIComponent(runId)}`),
    refetchInterval: (q) => {
      const d = q.state.data as RunDetail | undefined;
      if (!d) return 1500;
      return (d.status === "running" || d.status === "queued") ? 1500 : false;
    },
  });
  const detail = detailQuery.data;

  const download = () => {
    if (!detail?.log) return;
    const blob = new Blob([detail.log], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${runId}.log`; a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-start justify-center overflow-y-auto p-4 sm:p-8" onClick={onClose}>
      <div className="bg-[var(--color-card)] text-[var(--color-card-foreground)] rounded-lg shadow-2xl w-full max-w-5xl border border-[var(--color-border)] flex flex-col max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--color-border)]">
          <div className="flex items-center gap-3 min-w-0">
            <Terminal className="h-4 w-4 text-[var(--color-muted-foreground)] shrink-0" />
            <div className="min-w-0">
              <h2 className="text-base font-semibold truncate">
                {detail ? <>tofu {detail.action} · {detail.stack}</> : t("summary.loadingRun")}
              </h2>
              <div className="text-xs text-[var(--color-muted-foreground)] flex items-center gap-2 mt-0.5 flex-wrap">
                <span className="font-mono truncate">{runId}</span>
                {detail?.status && <Badge variant={statusToVariant(detail.status)} className="text-[10px]">{detail.status}</Badge>}
                {detail?.cloud_project && <span>· {detail.cloud_project}</span>}
                {detail?.env && <span>· env: {detail.env}</span>}
                {detail?.returncode != null && <span>· {t("summary.exit")} {detail.returncode}</span>}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => detailQuery.refetch()}>
              <RefreshCw className="h-4 w-4" />
            </Button>
            <Button variant="outline" size="sm" onClick={download} disabled={!detail?.log}>
              <Download className="h-4 w-4" />
            </Button>
            <button onClick={onClose} className="text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] text-2xl leading-none px-1">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>
        <div className="p-3 flex-1 overflow-auto space-y-3">
          {detail?.action && (
            <RunFlowGraph action={detail.action} log={detail.log || ""} status={detail.status} />
          )}
          <PlanDiff log={detail?.log || ""} action={detail?.action} />
          <InventorySection stack={stack} />
          <LogViewer text={detail?.log || t("summary.loadingLog")} className="max-h-[60vh]" />
        </div>
      </div>
    </div>
  );
}

type VM = {
  hostname?: string; instance_id?: string; status?: string; az?: string;
  flavor_id?: string;
  private_ip?: string; public_ip?: string | null;
  subnet_name?: string; subnet_cidr?: string;
  vpc_name?: string; vpc_cidr?: string;
  system_disk_type?: string; system_disk_size?: number;
};
type Inventory = { vms: VM[]; count: number; generated_at?: number; state_present?: boolean; message?: string };

function InventorySection({ stack }: { stack: string }) {
  const t = useT();
  const [open, setOpen] = useState(true);
  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: ["cloud", "stack", stack, "inventory"],
    queryFn: () => api<Inventory>("GET", `/api/cloud/stacks/${encodeURIComponent(stack)}/inventory`),
    enabled: open,
  });
  const vms = data?.vms || [];

  return (
    <div className="rounded-md border border-[var(--color-border)]">
      <div className="flex items-center justify-between px-3 py-2 border-b border-[var(--color-border)] bg-[var(--color-muted)]/30">
        <button onClick={() => setOpen((v) => !v)} className="text-sm font-medium flex items-center gap-2">
          <span>{open ? "▾" : "▸"}</span>
          {t("summary.vmInventory")}
          {data && <span className="text-xs text-[var(--color-muted-foreground)]">· {t(data.count === 1 ? "summary.vmCountOne" : "summary.vmCount", { count: data.count })}</span>}
        </button>
        {open && (
          <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? "animate-spin" : ""}`} />
          </Button>
        )}
      </div>
      {open && (
        <div className="p-3">
          {isLoading && <div className="text-xs text-[var(--color-muted-foreground)]">{t("summary.loadingInventory")}</div>}
          {error && <div className="text-xs text-[var(--color-destructive)]">{(error as any)?.message || t("summary.inventoryError")}</div>}
          {!isLoading && !error && vms.length === 0 && (
            <div className="text-xs text-[var(--color-muted-foreground)]">
              {data?.message || t("summary.noVms")}
            </div>
          )}
          {vms.length > 0 && (
            <div className="overflow-x-auto rounded border border-[var(--color-border)]">
              <table className="w-full text-xs">
                <thead className="bg-[var(--color-muted)] text-[var(--color-muted-foreground)] uppercase tracking-wide">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium">{t("summary.vm.hostname")}</th>
                    <th className="text-left px-3 py-2 font-medium">{t("summary.vm.status")}</th>
                    <th className="text-left px-3 py-2 font-medium">{t("summary.vm.privateIp")}</th>
                    <th className="text-left px-3 py-2 font-medium">{t("summary.vm.publicIp")}</th>
                    <th className="text-left px-3 py-2 font-medium">{t("summary.vm.subnet")}</th>
                    <th className="text-left px-3 py-2 font-medium">{t("summary.vm.vpc")}</th>
                    <th className="text-left px-3 py-2 font-medium">{t("summary.vm.az")}</th>
                    <th className="text-left px-3 py-2 font-medium">{t("summary.vm.flavor")}</th>
                    <th className="text-left px-3 py-2 font-medium">{t("summary.vm.disk")}</th>
                  </tr>
                </thead>
                <tbody>
                  {vms.map((vm) => (
                    <tr key={vm.instance_id || vm.hostname} className="border-t border-[var(--color-border)] hover:bg-[var(--color-muted)]/40">
                      <td className="px-3 py-2 font-mono">
                        <div className="font-semibold text-[var(--color-foreground)]">{vm.hostname || "—"}</div>
                        {vm.instance_id && <div className="text-[10px] text-[var(--color-muted-foreground)]">{vm.instance_id}</div>}
                      </td>
                      <td className="px-3 py-2"><Badge variant={vm.status === "ACTIVE" ? "success" : "default"}>{vm.status || "—"}</Badge></td>
                      <td className="px-3 py-2 font-mono">{vm.private_ip || "—"}</td>
                      <td className="px-3 py-2 font-mono">{vm.public_ip || <span className="text-[var(--color-muted-foreground)]">—</span>}</td>
                      <td className="px-3 py-2">
                        <div>{vm.subnet_name || "—"}</div>
                        <div className="text-[10px] text-[var(--color-muted-foreground)] font-mono">{vm.subnet_cidr}</div>
                      </td>
                      <td className="px-3 py-2">
                        <div>{vm.vpc_name || "—"}</div>
                        <div className="text-[10px] text-[var(--color-muted-foreground)] font-mono">{vm.vpc_cidr}</div>
                      </td>
                      <td className="px-3 py-2 font-mono">{vm.az || "—"}</td>
                      <td className="px-3 py-2 font-mono">{vm.flavor_id || "—"}</td>
                      <td className="px-3 py-2 font-mono">
                        {vm.system_disk_size ? `${vm.system_disk_size} GB` : "—"}
                        {vm.system_disk_type && <span className="text-[var(--color-muted-foreground)]"> · {vm.system_disk_type}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
