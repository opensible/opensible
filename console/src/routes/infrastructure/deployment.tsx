import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Play, RefreshCw, Folder, Server, Lock, ClipboardList, LayoutTemplate, X, Terminal } from "lucide-react";
import { toast } from "sonner";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { YamlEditor } from "@/components/ui/yaml-editor";
import { RunLogDialog } from "@/components/infrastructure/RunLogDialog";
import { Pager } from "@/components/ui/pager";
import { TemplateDialog } from "@/components/infrastructure/TemplateDialog";
import { cn } from "@/lib/utils";

import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/infrastructure/deployment")({
  component: DeploymentPage,
});

type RolesTreeNode = { pack?: string; role?: string; path?: string; children?: RolesTreeNode[] };
type InventoryFile = { name: string; path: string; relative_path?: string };
type Playbook = { id: string; name: string };
type Vault = { id: string; name?: string; file?: string };
type ExecutionRecord = {
  id?: string; execution_id?: string; status?: string;
  started_at?: string | number; finished_at?: string | number;
  startedAt?: number; finishedAt?: number; queuedAt?: number; createdAt?: number;
  duration?: number;
  play_name?: string; playbook_name?: string; playbookName?: string; runName?: string;
  type?: string; mode?: string; stage?: string;
  hosts?: string[]; limit?: string; user?: string;
  description?: string;
  runParams?: { hosts?: string[]; limit?: string; execution_type?: string };
  selectionSnapshot?: { hosts?: any[]; groups?: any[]; limit?: string };
  inventorySnapshot?: { groups?: { groupName?: string; hosts?: { hostId?: string; ip?: string }[] }[] };
};

function flattenRoles(tree: RolesTreeNode[] | undefined): { id: string; label: string }[] {
  const out: { id: string; label: string }[] = [];
  const walk = (n: RolesTreeNode) => {
    if (n.role) out.push({ id: n.path || `${n.pack}/${n.role}`, label: `${n.pack ?? ""}/${n.role}` });
    (n.children || []).forEach(walk);
  };
  (tree || []).forEach(walk);
  return out;
}

function toMillis(ts?: string | number): number | undefined {
  if (ts === undefined || ts === null || ts === "") return undefined;
  if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
  const n = Number(ts);
  if (!isNaN(n)) return n < 1e12 ? n * 1000 : n;
  const d = new Date(ts).getTime();
  return isNaN(d) ? undefined : d;
}
function fmtDateTime(ts?: string | number): string {
  const ms = toMillis(ts);
  if (ms === undefined) return "—";
  try { return new Date(ms).toLocaleString(); } catch { return "—"; }
}
function fmtDur(start?: string | number, end?: string | number, fallbackSec?: number): string {
  const s = toMillis(start);
  if (s === undefined) {
    if (fallbackSec && fallbackSec > 0) {
      const m = Math.floor(fallbackSec / 60), sec = fallbackSec % 60;
      return m ? `${m}m ${sec}s` : `${sec}s`;
    }
    return "—";
  }
  const e = toMillis(end) ?? Date.now();
  const d = Math.max(0, Math.floor((e - s) / 1000));
  const m = Math.floor(d / 60), sec = d % 60;
  return m ? `${m}m ${sec}s` : `${sec}s`;
}
function fmtRel(ts?: string | number): string {
  const ms = toMillis(ts);
  if (ms === undefined) return "—";
  const diff = Math.floor((Date.now() - ms) / 1000);
  if (diff < 5) return "just now";
  if (diff < 60) return diff + "s ago";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  return Math.floor(diff / 86400) + "d ago";
}
function statusVariant(status: string): "success" | "warning" | "destructive" | "default" {
  const s = status.toUpperCase();
  if (s === "SUCCESS" || s === "COMPLETED") return "success";
  if (s === "RUNNING" || s === "PENDING" || s === "QUEUED") return "warning";
  if (s === "FAILED" || s === "ERROR" || s === "CANCELED" || s === "CANCELLED") return "destructive";
  return "default";
}

function DeploymentPage() {
  const t = useT();
  const [kind, setKind] = useState<"playbook" | "role" | "template">("playbook");
  const [openTemplateId, setOpenTemplateId] = useState<string | null>(null);
  const [selectedPlaybooks, setSelectedPlaybooks] = useState<string[]>([]);
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);
  const [selectedTemplates, setSelectedTemplates] = useState<string[]>([]);
  const [playName, setPlayName] = useState("Ad-hoc deployment");
  const [selectedGroups, setSelectedGroups] = useState<string[]>([]);
  const [selectedHosts, setSelectedHosts] = useState<string[]>([]);
  const [remoteUser, setRemoteUser] = useState("");
  const [become, setBecome] = useState(true);
  const [strategy, setStrategy] = useState("linear");
  const [vaultId, setVaultId] = useState("");
  const [inventoryFiles, setInventoryFiles] = useState<string[]>([]);
  const [ansibleConfig, setAnsibleConfig] = useState<string>(() => {
    if (typeof window !== "undefined") {
      return window.localStorage.getItem("selected_ansible_config") || "ansible.cfg";
    }
    return "ansible.cfg";
  });
  const [extraVars, setExtraVars] = useState("---\n# extra vars (YAML)\n");
  const [checkMode, setCheckMode] = useState(false);
  const [verbosity, setVerbosity] = useState("");
  const [forks, setForks] = useState<string>("");
  const [forceHandlers, setForceHandlers] = useState(false);
  const [connTimeout, setConnTimeout] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [liveLogId, setLiveLogId] = useState<string | null>(null);
  const [viewLogId, setViewLogId] = useState<string | null>(null);
  const [showLaunch, setShowLaunch] = useState(false);
  

  // Filters
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 15;

  const playbooksQ = useQuery({
    queryKey: ["playbooks"],
    queryFn: () => api<{ success?: boolean; playbooks?: Playbook[] }>("GET", "/api/projects/_current/playbooks"),
  });
  const templatesQ = useQuery({
    queryKey: ["templates"],
    queryFn: () => api<{ templates?: { id: string; name: string; category?: string }[] }>("GET", "/api/templates"),
  });
  const rolesQ = useQuery({
    queryKey: ["roles-storage"],
    queryFn: () => api<{ tree?: RolesTreeNode[] }>("GET", "/api/roles/storage"),
  });
  const inventoryQ = useQuery({
    queryKey: ["inventory-list"],
    queryFn: () => api<{ files?: InventoryFile[]; inventories?: InventoryFile[] }>("GET", "/api/inventory/list"),
  });
  const groupsQ = useQuery({
    queryKey: ["inventory-groups"],
    queryFn: () => api<{ success?: boolean; groups?: Record<string, { hosts?: string[] }> }>(
      "GET", "/api/inventory/groups"
    ),
  });
  const vaultsQ = useQuery({
    queryKey: ["project-vaults"],
    queryFn: async () => {
      try {
        return await api<{ success?: boolean; vaults?: Vault[]; vaultFiles?: Vault[] }>(
          "GET", "/api/projects/_current/vaults"
        );
      } catch {
        return { success: false, vaults: [] as Vault[], vaultFiles: [] as Vault[] };
      }
    },
  });
  const executionsQ = useQuery({
    queryKey: ["executions-deployment"],
    queryFn: () => api<{ success?: boolean; executions?: ExecutionRecord[] }>(
      "GET", "/api/executions?limit=100"
    ),
    refetchInterval: 5000,
  });
  const executions: ExecutionRecord[] = (executionsQ.data?.executions || [])
    .filter((e: ExecutionRecord) => e.mode !== "TOFU");

  const playbooks = playbooksQ.data?.playbooks || [];
  const roles = useMemo(() => flattenRoles(rolesQ.data?.tree), [rolesQ.data]);
  const invFiles: InventoryFile[] = inventoryQ.data?.files || inventoryQ.data?.inventories || [];
  const groupsMap = groupsQ.data?.groups || {};
  const groupNames = Object.keys(groupsMap);
  const allHosts = useMemo(() => {
    const set = new Set<string>();
    Object.values(groupsMap).forEach((g) => (g.hosts || []).forEach((h) => set.add(h)));
    return Array.from(set);
  }, [groupsMap]);
  const vaults: Vault[] = vaultsQ.data?.vaults || vaultsQ.data?.vaultFiles || [];

  const templates = templatesQ.data?.templates || [];

  useEffect(() => {
    if (!selectedGroups.length && groupNames.includes("all")) setSelectedGroups(["all"]);
  }, [groupNames.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (cur: string[], setter: (v: string[]) => void, value: string) => {
    setter(cur.includes(value) ? cur.filter(x => x !== value) : [...cur, value]);
  };

  const computedLimit = useMemo(() => {
    const parts = [...selectedGroups, ...selectedHosts];
    return parts.length ? parts.join(":") : "";
  }, [selectedGroups, selectedHosts]);

  const launch = async () => {
    if (kind === "template") {
      if (selectedTemplates.length === 0) { toast.error("Pick at least one template"); return; }
      if (selectedTemplates.length > 1) {
        toast.info("Opening wizard for the first selected template. Templates run one at a time.");
      }
      setOpenTemplateId(selectedTemplates[0] || null);
      setShowLaunch(false);
      return;
    }
    if (kind === "playbook" && selectedPlaybooks.length === 0) { toast.error("Pick at least one playbook"); return; }
    if (kind === "role" && selectedRoles.length === 0) { toast.error("Pick at least one role"); return; }
    if (!selectedGroups.length && !selectedHosts.length) {
      toast.error("Select at least one group or host");
      return;
    }
    setRunning(true);
    try {
      const invList = inventoryFiles.length ? inventoryFiles : ["inventory.yml"];
      const commonPlaybook = {
        inventory_files: invList,
        ansible_config: ansibleConfig || "ansible.cfg",
        check_mode: checkMode,
        verbosity: verbosity || undefined,
        forks: forks ? Number(forks) : undefined,
        force_handlers: forceHandlers,
        connection_timeout: connTimeout ? Number(connTimeout) : undefined,
        extra_vars: extraVars,
        limit: computedLimit || undefined,
        play_name: playName || undefined,
        remote_user: remoteUser || undefined,
        become,
        strategy,
        vault_id: vaultId || undefined,
      };
      let lastId: string | undefined;
      if (kind === "playbook") {
        for (const pbId of selectedPlaybooks) {
          const res: any = await api("POST", `/api/projects/_current/playbooks/${encodeURIComponent(pbId)}/run`, commonPlaybook);
          lastId = res?.executionId || res?.execution_id || res?.run_id || res?.id;
        }
      } else {
        const res: any = await api("POST", "/api/run_ansible", {
          hosts: [...selectedGroups, ...selectedHosts],
          roles: selectedRoles,
          inventory_files: invList,
          ansible_config: ansibleConfig || "ansible.cfg",
          check_mode: checkMode,
          verbosity: verbosity || undefined,
          forks: forks ? Number(forks) : undefined,
          force_handlers: forceHandlers,
          connection_timeout: connTimeout ? Number(connTimeout) : undefined,
          extra_vars: extraVars,
          play_name: playName || undefined,
          remote_user: remoteUser || undefined,
          become,
          strategy,
          vault_id: vaultId || undefined,
        });
        lastId = res?.executionId || res?.execution_id || res?.run_id || res?.id;
      }
      if (lastId) {
        setShowLaunch(false);
        setLiveLogId(String(lastId));
        toast.success(`Run queued (${lastId})`);
        executionsQ.refetch();
      } else {
        toast.success("Run queued");
      }
    } catch (e) {
      toast.error((e as Error).message);
    } finally { setRunning(false); }
  };


  const counts = {
    total: executions.length,
    running: executions.filter(e => ["RUNNING", "PENDING", "QUEUED"].includes((e.status || "").toUpperCase())).length,
    succeeded: executions.filter(e => ["SUCCESS", "COMPLETED"].includes((e.status || "").toUpperCase())).length,
    failed: executions.filter(e => ["FAILED", "ERROR", "CANCELED", "CANCELLED"].includes((e.status || "").toUpperCase())).length,
  };

  const filtered = executions.filter((e) => {
    const s = (e.status || "").toUpperCase();
    if (statusFilter && s !== statusFilter) return false;
    if (typeFilter && (e.type || "").toUpperCase() !== typeFilter) return false;
    if (q) {
      const blob = `${e.play_name || ""} ${e.playbook_name || ""} ${e.id || e.execution_id || ""} ${(e.hosts || []).join(",")} ${e.limit || ""}`.toLowerCase();
      if (!blob.includes(q.toLowerCase())) return false;
    }
    return true;
  });

  const typeOptions = Array.from(new Set(executions.map(e => (e.type || "").toUpperCase()).filter(Boolean)));

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  useEffect(() => { setPage(1); }, [q, statusFilter, typeFilter]);
  const pageItems = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);


  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: "Infrastructure" }, { label: "Deployment" }]} />

      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">{t("page.deployment.title")}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)] mt-1">{t("page.deployment.subtitle")}</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Button size="sm" onClick={() => setShowLaunch(true)}>
            <Play className="h-4 w-4 mr-1.5" /> Launch a run
          </Button>
          <Button variant="outline" size="sm" onClick={() => executionsQ.refetch()}>
            <RefreshCw className="h-4 w-4" /> Refresh
          </Button>
        </div>
      </div>


      {/* Stat cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: "Total", value: counts.total, tone: "" },
          { label: "Running", value: counts.running, tone: "text-blue-600" },
          { label: "Succeeded", value: counts.succeeded, tone: "text-emerald-600" },
          { label: "Failed", value: counts.failed, tone: "text-red-600" },
        ].map(s => (
          <div key={s.label} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-3">
            <div className="text-xs uppercase tracking-wide text-[var(--color-muted-foreground)]">{s.label}</div>
            <div className={cn("text-2xl font-semibold mt-1", s.tone)}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3">
        <div className="flex flex-wrap items-center gap-3">
          <Input placeholder={t("deployment.searchPlaceholder")} value={q} onChange={(e) => setQ(e.target.value)} className="max-w-xs" />
          <select className="h-9 px-2 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] text-sm" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">{t("status.all")}</option>
            <option value="RUNNING">{t("status.running")}</option>
            <option value="PENDING">{t("status.pending")}</option>
            <option value="SUCCESS">{t("status.success")}</option>
            <option value="COMPLETED">{t("status.completed")}</option>
            <option value="FAILED">{t("status.failed")}</option>
            <option value="ERROR">{t("status.error")}</option>
          </select>
          {typeOptions.length > 0 && (
            <select className="h-9 px-2 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] text-sm" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
              <option value="">{t("status.allTypes")}</option>
              {typeOptions.map(tp => <option key={tp} value={tp}>{tp}</option>)}
            </select>
          )}
          <span className="ml-auto text-xs text-[var(--color-muted-foreground)]">{t("deployment.runsCount").replace("{filtered}", String(filtered.length)).replace("{total}", String(executions.length))}</span>
        </div>
      </div>

      {/* Table */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-[var(--color-muted)]/40 text-xs uppercase tracking-wide text-[var(--color-muted-foreground)]">
              <tr>
                <th className="text-left px-3 py-2.5 font-medium">{t("table.type")}</th>
                <th className="text-left px-3 py-2.5 font-medium">{t("table.playPlaybook")}</th>
                <th className="text-left px-3 py-2.5 font-medium">{t("table.target")}</th>
                <th className="text-left px-3 py-2.5 font-medium">{t("table.dateTime")}</th>
                <th className="text-left px-3 py-2.5 font-medium">{t("table.duration")}</th>
                <th className="text-left px-3 py-2.5 font-medium">{t("table.age")}</th>
                <th className="text-left px-3 py-2.5 font-medium">{t("common.status")}</th>
              </tr>
            </thead>
            <tbody>
              {executionsQ.isLoading && (
                <tr><td colSpan={7} className="px-3 py-10 text-center text-[var(--color-muted-foreground)]">{t("common.loading")}</td></tr>
              )}
              {!executionsQ.isLoading && filtered.length === 0 && (
                <tr><td colSpan={7} className="px-3 py-10 text-center text-[var(--color-muted-foreground)]">{t("deployment.noRuns")}</td></tr>
              )}
              {pageItems.map(e => {
                const id = e.id || e.execution_id || "";
                const status = (e.status || "UNKNOWN").toUpperCase();
                const invHosts: string[] = [];
                (e.inventorySnapshot?.groups || []).forEach(g => {
                  (g.hosts || []).forEach(h => {
                    const v = h?.hostId || h?.ip;
                    if (v) invHosts.push(String(v));
                  });
                });
                const selHosts = Array.isArray(e.selectionSnapshot?.hosts)
                  ? e.selectionSnapshot!.hosts!.map((x: any) => x?.hostId || x?.ip || x).filter(Boolean).map(String)
                  : [];
                const descHosts = (() => {
                  const d = e.description || "";
                  const m = d.match(/:\s*(.+?)(?:\.\.\.)?$/);
                  if (!m) return [] as string[];
                  return (m[1] || "").split(",").map(s => s.trim()).filter(Boolean);
                })();
                const limitStr = e.limit || e.selectionSnapshot?.limit || e.runParams?.limit || "";
                const targets = (
                  limitStr ? limitStr.split(/[,:]/).map(s => s.trim()).filter(Boolean) :
                  e.runParams?.hosts?.length ? e.runParams!.hosts! :
                  e.hosts?.length ? e.hosts :
                  selHosts.length ? selHosts :
                  invHosts.length ? invHosts :
                  descHosts.length ? descHosts :
                  []
                );
                const target = targets.length ? targets.join(", ") : "—";
                const started = e.started_at ?? e.startedAt ?? e.queuedAt ?? e.createdAt;
                const finished = e.finished_at ?? e.finishedAt;
                const playName = e.play_name || e.runName || e.playbook_name || e.playbookName || "Ansible run";
                const kind = e.type || e.mode || "ANSIBLE";
                return (
                  <tr
                    key={id}
                    onClick={() => setViewLogId(id)}
                    className="border-t border-[var(--color-border)] hover:bg-[var(--color-muted)]/40 cursor-pointer transition-colors"
                  >
                    <td className="px-3 py-2.5">
                      <Badge variant="default" className="text-[10px] uppercase">{kind}</Badge>
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="font-medium truncate max-w-[320px]">{playName}</div>
                      <div className="text-[10px] font-mono text-[var(--color-muted-foreground)]">{id.slice(0, 18)}</div>
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs truncate max-w-[220px]" title={target}>{target}</td>
                    <td className="px-3 py-2.5 whitespace-nowrap text-[var(--color-muted-foreground)]">{fmtDateTime(started)}</td>
                    <td className="px-3 py-2.5 whitespace-nowrap text-[var(--color-muted-foreground)]">{fmtDur(started, finished, e.duration)}</td>
                    <td className="px-3 py-2.5 whitespace-nowrap text-[var(--color-muted-foreground)]">{fmtRel(started)}</td>
                    <td className="px-3 py-2.5"><Badge variant={statusVariant(status)}>{status}</Badge></td>
                  </tr>
                );
              })}
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

      {/* Launch dialog */}
      {showLaunch && (
        <DialogShell title="Launch a run" icon={<Play className="h-4 w-4" />} onClose={() => setShowLaunch(false)}
          maxWidth="max-w-[95vw]"
          footer={
            <div className="flex justify-end w-full gap-2">
              <Button variant="outline" size="sm" onClick={() => setShowLaunch(false)}>Cancel</Button>
              <Button onClick={launch} disabled={running}>
                <Play className="h-4 w-4 mr-1.5" /> {running ? "Launching…" : "Launch run"}
              </Button>
            </div>
          }>
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_340px]">
            <div className="p-4 space-y-5 min-w-0">

            <div>
              <Label>Type</Label>
              <div className="flex flex-wrap gap-2 mt-1">
                {(["playbook", "role", "template"] as const).map((k) => (
                  <button key={k} onClick={() => setKind(k)}
                    className={`px-3 py-1.5 rounded-md text-sm border ${kind === k ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]" : "border-[var(--color-border)] hover:bg-[var(--color-accent)]"}`}>
                    {k === "playbook" ? "Playbook" : k === "role" ? "Roles" : (<span className="inline-flex items-center gap-1"><LayoutTemplate className="h-3.5 w-3.5" />Template</span>)}
                  </button>
                ))}
              </div>
            </div>

            {kind === "playbook" && (
              <div>
                <Label>Playbooks (multi-select)</Label>
                <div className="mt-1 flex flex-wrap gap-2 max-h-48 overflow-auto p-2 border border-[var(--color-border)] rounded-md">
                  {playbooks.length === 0 && <span className="text-xs text-[var(--color-muted-foreground)]">No playbooks found. Sync your repo first.</span>}
                  {playbooks.map((p) => {
                    const on = selectedPlaybooks.includes(p.id);
                    return (
                      <label key={p.id} className={`flex items-center gap-2 px-3 py-1.5 rounded-md border text-sm cursor-pointer ${on ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]" : "border-[var(--color-border)] hover:bg-[var(--color-accent)]"}`}>
                        <input type="checkbox" checked={on} onChange={() => toggle(selectedPlaybooks, setSelectedPlaybooks, p.id)} />
                        <span className="font-mono text-xs">{p.name}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}

            {kind === "role" && (
              <div>
                <Label>Roles (multi-select)</Label>
                <div className="mt-1 flex flex-wrap gap-2 max-h-48 overflow-auto p-2 border border-[var(--color-border)] rounded-md">
                  {roles.length === 0 && <span className="text-xs text-[var(--color-muted-foreground)]">No roles found. Sync your repo first.</span>}
                  {roles.map((r) => {
                    const on = selectedRoles.includes(r.id);
                    return (
                      <label key={r.id} className={`flex items-center gap-2 px-3 py-1.5 rounded-md border text-sm cursor-pointer ${on ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]" : "border-[var(--color-border)] hover:bg-[var(--color-accent)]"}`}>
                        <input type="checkbox" checked={on} onChange={() => toggle(selectedRoles, setSelectedRoles, r.id)} />
                        <span className="font-mono text-xs">{r.label}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}

            {kind === "template" && (
              <div>
                <div className="flex items-center justify-between">
                  <Label>Templates (multi-select)</Label>
                  <Button size="sm" disabled={selectedTemplates.length === 0} onClick={() => { setOpenTemplateId(selectedTemplates[0] || null); setShowLaunch(false); }}>
                    <LayoutTemplate className="h-4 w-4 mr-1.5" /> Open wizard
                  </Button>
                </div>
                <div className="mt-1 flex flex-wrap gap-2 max-h-48 overflow-auto p-2 border border-[var(--color-border)] rounded-md">
                  {templates.length === 0 && <span className="text-xs text-[var(--color-muted-foreground)]">No templates available.</span>}
                  {templates.map((t) => {
                    const on = selectedTemplates.includes(t.id);
                    return (
                      <label key={t.id} className={`flex items-center gap-2 px-3 py-1.5 rounded-md border text-sm cursor-pointer ${on ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]" : "border-[var(--color-border)] hover:bg-[var(--color-accent)]"}`}>
                        <input type="checkbox" checked={on} onChange={() => toggle(selectedTemplates, setSelectedTemplates, t.id)} />
                        <span className="text-xs">{t.category ? `[${t.category}] ` : ""}{t.name}</span>
                      </label>
                    );
                  })}
                </div>
                {selectedTemplates.length > 1 && (
                  <div className="text-xs text-[var(--color-muted-foreground)] mt-1">Wizard runs one template at a time; the first selected will open.</div>
                )}
              </div>
            )}


            <div>
              <Label>Play name</Label>
              <Input className="mt-1" value={playName} onChange={(e) => setPlayName(e.target.value)} placeholder="e.g. Rolling Docker Engine upgrade" />
            </div>

            <div>
              <Label><Folder className="inline h-3.5 w-3.5 mr-1" />Groups</Label>
              <div className="mt-1 flex flex-wrap gap-2 p-2 border border-[var(--color-border)] rounded-md">
                {groupNames.length === 0 && <span className="text-xs text-[var(--color-muted-foreground)]">No groups (sync inventory).</span>}
                {groupNames.map((g) => {
                  const on = selectedGroups.includes(g);
                  return (
                    <label key={g} className={`flex items-center gap-2 px-3 py-1.5 rounded-md border text-sm cursor-pointer ${on ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]" : "border-[var(--color-border)] hover:bg-[var(--color-accent)]"}`}>
                      <input type="checkbox" checked={on} onChange={() => toggle(selectedGroups, setSelectedGroups, g)} />
                      {g}
                    </label>
                  );
                })}
              </div>
            </div>

            <div>
              <Label><Server className="inline h-3.5 w-3.5 mr-1" />Hosts</Label>
              <div className="mt-1 flex flex-wrap gap-2 p-2 border border-[var(--color-border)] rounded-md max-h-40 overflow-auto">
                {allHosts.length === 0 && <span className="text-xs text-[var(--color-muted-foreground)]">No hosts.</span>}
                {allHosts.map((h) => {
                  const on = selectedHosts.includes(h);
                  return (
                    <label key={h} className={`flex items-center gap-2 px-3 py-1.5 rounded-md border text-sm cursor-pointer ${on ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]" : "border-[var(--color-border)] hover:bg-[var(--color-accent)]"}`}>
                      <input type="checkbox" checked={on} onChange={() => toggle(selectedHosts, setSelectedHosts, h)} />
                      <span className="font-mono text-xs">{h}</span>
                    </label>
                  );
                })}
              </div>
              <div className="text-xs text-[var(--color-muted-foreground)] mt-1">
                Selected: {[...selectedGroups, ...selectedHosts].join(", ") || "—"}
              </div>
            </div>

            <div>
              <Label>Remote user</Label>
              <Input className="mt-1" value={remoteUser} onChange={(e) => setRemoteUser(e.target.value)} placeholder="Leave empty for default" />
            </div>

            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={become} onChange={(e) => setBecome(e.target.checked)} />
              <span className="font-medium">Become (sudo/su)</span>
            </label>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <Label>Strategy</Label>
                <select value={strategy} onChange={(e) => setStrategy(e.target.value)}
                  className="mt-1 h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-2 text-sm">
                  <option value="linear">Linear</option>
                  <option value="free">Free</option>
                  <option value="debug">Debug</option>
                  <option value="host_pinned">Host pinned</option>
                  <option value="mitogen_linear">Mitogen linear</option>
                  <option value="mitogen_free">Mitogen free</option>
                  <option value="mitogen_host_pinned">Mitogen host pinned</option>
                </select>
              </div>
              <div>
                <Label><Lock className="inline h-3.5 w-3.5 mr-1" />Vaults</Label>
                <select value={vaultId} onChange={(e) => setVaultId(e.target.value)}
                  className="mt-1 h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-2 text-sm">
                  <option value="">— None —</option>
                  {vaults.map((v) => (
                    <option key={v.id} value={v.id}>{v.name || v.file || v.id}</option>
                  ))}
                </select>
                <div className="text-xs text-[var(--color-muted-foreground)] mt-1">vault vars_files (vars/name.yml)</div>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <Label>Ansible config</Label>
                <Input className="mt-1" value={ansibleConfig} onChange={(e) => setAnsibleConfig(e.target.value)} placeholder="ansible.cfg" />
              </div>
              <div>
                <Label>Inventory files</Label>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {invFiles.length === 0 && <div className="text-xs text-[var(--color-muted-foreground)]">Default: inventory.yml</div>}
                  {invFiles.map((f) => {
                    const path = f.relative_path || f.path;
                    const on = inventoryFiles.includes(path);
                    return (
                      <button key={path} onClick={() => toggle(inventoryFiles, setInventoryFiles, path)}
                        className={`px-2 py-1 rounded text-xs border font-mono ${on ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]" : "border-[var(--color-border)] hover:bg-[var(--color-accent)]"}`}>
                        {f.name}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 items-end">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={checkMode} onChange={(e) => setCheckMode(e.target.checked)} />
                Check mode (dry-run)
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={forceHandlers} onChange={(e) => setForceHandlers(e.target.checked)} />
                Force handlers
              </label>
              <div>
                <Label>Verbosity</Label>
                <select value={verbosity} onChange={(e) => setVerbosity(e.target.value)}
                  className="mt-1 h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-2 text-sm">
                  <option value="">default</option>
                  <option value="-v">-v</option>
                  <option value="-vv">-vv</option>
                  <option value="-vvv">-vvv</option>
                  <option value="-vvvv">-vvvv</option>
                </select>
              </div>
              <div>
                <Label>Forks</Label>
                <Input className="mt-1" value={forks} onChange={(e) => setForks(e.target.value)} placeholder="default" />
              </div>
              <div>
                <Label>Connection timeout (s)</Label>
                <Input className="mt-1" value={connTimeout} onChange={(e) => setConnTimeout(e.target.value)} placeholder="default" />
              </div>
            </div>

            <div>
              <Label>Extra vars (YAML)</Label>
              <div className="mt-1 border border-[var(--color-border)] rounded-md overflow-hidden">
                <YamlEditor value={extraVars} onChange={setExtraVars} height={160} />
              </div>
            </div>
            </div>
            {/* Run Summary side panel */}

            <div className="border-t lg:border-t-0 lg:border-l border-[var(--color-border)] bg-[var(--color-muted)]/20 p-4 text-sm">
              <div className="flex items-center gap-2 mb-3">
                <ClipboardList className="h-4 w-4 text-[var(--color-muted-foreground)]" />
                <h3 className="font-semibold">Run Summary</h3>
              </div>
              <div className="space-y-2">
                {([
                  ["Type", <span className="capitalize">{kind}</span>],
                  [kind === "playbook" ? "Playbooks" : kind === "role" ? "Roles" : "Templates",
                    (kind === "playbook" ? selectedPlaybooks : kind === "role" ? selectedRoles : selectedTemplates).join(", ") || "—"],
                  ["Play name", playName || "—"],
                  ["Limit", computedLimit || "—"],
                  ["Remote user", remoteUser || "default"],
                  ["Become", become ? "Yes" : "No"],
                  ["Strategy", <span className="capitalize">{strategy}</span>],
                  ["Vault", vaultId ? (vaults.find(v => v.id === vaultId)?.name || vaultId) : "None"],
                  ["Inventory", inventoryFiles.join(", ") || "inventory.yml"],
                  ["Ansible config", ansibleConfig || "ansible.cfg"],
                  ...(checkMode ? [["Check mode", "Yes"]] as any : []),
                  ...(verbosity ? [["Verbosity", verbosity]] as any : []),
                  ...(forks ? [["Forks", forks]] as any : []),
                  ...(forceHandlers ? [["Force handlers", "Yes"]] as any : []),
                  ...(connTimeout ? [["Timeout", `${connTimeout}s`]] as any : []),
                ] as [string, React.ReactNode][]).map(([k, v], i) => (
                  <div key={i} className="flex justify-between gap-2 border-b border-[var(--color-border)]/60 pb-1.5">
                    <span className="text-[var(--color-muted-foreground)]">{k}</span>
                    <span className="font-medium text-right truncate max-w-[60%]" title={typeof v === "string" ? v : undefined}>{v}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </DialogShell>
      )}


      {liveLogId && (
        <RunLogDialog
          executionId={liveLogId}
          live
          title="Live run logs"
          onClose={() => { setLiveLogId(null); executionsQ.refetch(); }}
        />
      )}
      {viewLogId && (
        <RunLogDialog
          executionId={viewLogId}
          onClose={() => setViewLogId(null)}
        />
      )}
      {openTemplateId && (
        <TemplateDialog
          templateId={openTemplateId}
          onClose={() => setOpenTemplateId(null)}
          onQueued={(id) => { setLiveLogId(id); executionsQ.refetch(); }}
        />
      )}
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <label className="text-xs font-medium text-[var(--color-muted-foreground)]">{children}</label>;
}

function DialogShell({
  title, icon, onClose, children, footer, maxWidth = "max-w-3xl",
}: {
  title: string;
  icon?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  maxWidth?: string;
}) {
  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-start justify-center overflow-y-auto p-4 sm:p-8" onClick={onClose}>
      <div className={cn("bg-[var(--color-card)] text-[var(--color-card-foreground)] rounded-lg shadow-2xl w-full border border-[var(--color-border)] flex flex-col max-h-[90vh]", maxWidth)} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--color-border)]">
          <div className="flex items-center gap-2 min-w-0">
            {icon || <Terminal className="h-4 w-4 text-[var(--color-muted-foreground)]" />}
            <h2 className="text-base font-semibold truncate">{title}</h2>
          </div>
          <button onClick={onClose} className="text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="flex-1 overflow-auto">{children}</div>
        {footer && (
          <div className="border-t border-[var(--color-border)] px-4 py-3 flex items-center">{footer}</div>
        )}
      </div>
    </div>
  );
}
