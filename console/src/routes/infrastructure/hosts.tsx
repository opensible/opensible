import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import {
  Server, RefreshCw, Plus, Search, Trash2, Edit3, CheckCircle2, XCircle,
  CircleDashed, Activity, FileText, Download, Layers, FolderTree, Variable, KeyRound,
  AlertTriangle,
} from "lucide-react";
import { toast } from "sonner";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { api } from "@/lib/api";
import { YamlEditor } from "@/components/ui/yaml-editor";
import { ImportFromCloudDialog } from "@/components/infrastructure/ImportFromCloudDialog";
import { Boxes } from "lucide-react";
import { useT } from "@/lib/i18n";

// Names that almost certainly hold secret material — used to warn when a
// variable is stored in plain YAML that will be synced to git.
const SENSITIVE_KEY_RE = /(password|passwd|secret|token|api[_-]?key|private[_-]?key|credential)/i;
const VAULT_HEADER_RE = /\$ANSIBLE_VAULT/;

/** True when a host_vars/group_vars object contains a sensitive-looking key
 *  whose value is not ansible-vault encrypted. */
function hasPlaintextSecret(vars: Record<string, unknown> | undefined | null): boolean {
  if (!vars) return false;
  for (const [k, v] of Object.entries(vars)) {
    if (!SENSITIVE_KEY_RE.test(k)) continue;
    if (k === "connectionSecret") continue; // reference only, not a credential
    if (typeof v === "string" && VAULT_HEADER_RE.test(v)) continue;
    if (v == null || v === "") continue;
    return true;
  }
  return false;
}

/** Same check but on raw YAML text (for the editors). */
function yamlHasPlaintextSecret(text: string): boolean {
  if (!text) return false;
  const lines = text.split(/\r?\n/);
  let inVaultBlock = false;
  for (const raw of lines) {
    const line = raw.trimStart();
    if (VAULT_HEADER_RE.test(line)) { inVaultBlock = true; continue; }
    if (inVaultBlock) {
      if (line === "" || /^\S/.test(raw)) inVaultBlock = false;
      else continue;
    }
    const m = /^([A-Za-z_][\w-]*)\s*:\s*(.*)$/.exec(line);
    if (!m) continue;
    const key = m[1]!;
    const val = m[2]!.trim();
    if (!SENSITIVE_KEY_RE.test(key)) continue;
    if (key === "connectionSecret") continue;
    if (!val || val.startsWith("#")) continue;
    if (val.startsWith("!vault")) continue;
    return true;
  }
  return false;
}

function PlaintextSecretBanner({ what }: { what: string }) {
  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400 flex gap-2 mb-2">
      <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
      <div>
        <div className="font-medium">Plaintext secret detected in {what}.</div>
        <div className="text-[11px] opacity-90 mt-0.5">
          Keys like <span className="font-mono">password</span>, <span className="font-mono">token</span>,{" "}
          <span className="font-mono">api_key</span>, <span className="font-mono">private_key</span> should be
          ansible-vault encrypted (<span className="font-mono">$ANSIBLE_VAULT;1.1;AES256;…</span>) or moved to a
          Global Secret referenced via <span className="font-mono">connectionSecret</span>. Anything else here is
          pushed to your inventory repo in cleartext.
        </div>
      </div>
    </div>
  );
}

export const Route = createFileRoute("/infrastructure/hosts")({ component: HostsPage });

// ---------- Types ----------
type HostStatus = "ok" | "online" | "failed" | "error" | "offline" | "checking" | "unknown";
type HostInfo = {
  inventory_file?: string;
  vars_file?: string;
  status?: HostStatus;
  last_checked_at?: string | null;
  status_expires_at?: string | null;
};
type GroupData = { hosts?: string[]; children?: string[]; inventory_file?: string };
type AllData = {
  success: boolean;
  hosts: string[];
  hosts_info: Record<string, HostInfo>;
  hosts_status?: Record<string, { status?: HostStatus; last_checked_at?: string }>;
  groups: Record<string, GroupData>;
  group_vars_by_group?: Record<string, Record<string, unknown>>;
  host_vars?: Record<string, Record<string, unknown>>;
  all_keys?: string[];
};
type InventoryFile = { name: string; path: string; relative_path?: string; type?: string };
type InventoryListResp = { success: boolean; files?: InventoryFile[]; inventories?: InventoryFile[] };

// ---------- Modal ----------
function Modal({
  open, title, onClose, children, footer, width = "max-w-lg",
}: {
  open: boolean; title: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode; width?: string;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 pt-20 overflow-y-auto" onClick={onClose}>
      <div className={`bg-[var(--color-card)] rounded-lg shadow-2xl w-full ${width} border border-[var(--color-border)]`} onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-3 border-b border-[var(--color-border)] flex items-center justify-between">
          <h2 className="text-base font-semibold">{title}</h2>
          <button className="text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="p-5 max-h-[70vh] overflow-y-auto">{children}</div>
        {footer && <div className="px-5 py-3 border-t border-[var(--color-border)] flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

// ---------- Status pill ----------
function StatusPill({ status, lastChecked }: { status?: HostStatus; lastChecked?: string | null }) {
  const map: Record<string, { label: string; icon: React.ReactNode; cls: string }> = {
    ok:       { label: "Online",   icon: <CheckCircle2 className="h-3 w-3" />, cls: "bg-emerald-500/10 text-emerald-500 border-emerald-500/20" },
    online:   { label: "Online",   icon: <CheckCircle2 className="h-3 w-3" />, cls: "bg-emerald-500/10 text-emerald-500 border-emerald-500/20" },
    failed:   { label: "Failed",   icon: <XCircle className="h-3 w-3" />,      cls: "bg-red-500/10 text-red-500 border-red-500/20" },
    error:    { label: "Failed",   icon: <XCircle className="h-3 w-3" />,      cls: "bg-red-500/10 text-red-500 border-red-500/20" },
    offline:  { label: "Offline",  icon: <XCircle className="h-3 w-3" />,      cls: "bg-red-500/10 text-red-500 border-red-500/20" },
    checking: { label: "Checking", icon: <Activity className="h-3 w-3 animate-pulse" />, cls: "bg-amber-500/10 text-amber-500 border-amber-500/20" },
    unknown:  { label: "Unknown",  icon: <CircleDashed className="h-3 w-3" />, cls: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20" },
  };
  const cfg = map[status || "unknown"] ?? map.unknown!;
  const tip = lastChecked ? `Last checked: ${new Date(lastChecked).toLocaleString()}` : `Status: ${cfg.label}`;
  return (
    <span title={tip} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] border ${cfg.cls}`}>
      {cfg.icon} {cfg.label}
    </span>
  );
}

// ---------- Page ----------
function HostsPage() {
  const t = useT();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"hosts" | "groups" | "vars" | "host-vars" | "files">("hosts");
  const [selectedHostVar, setSelectedHostVar] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Dialogs
  const [addOpen, setAddOpen] = useState(false);
  const [editHost, setEditHost] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [bulkDelete, setBulkDelete] = useState(false);
  const [addGroupOpen, setAddGroupOpen] = useState(false);
  const [deleteGroup, setDeleteGroup] = useState<string | null>(null);
  const [editConn, setEditConn] = useState<string | null>(null);
  const [importCloudOpen, setImportCloudOpen] = useState(false);

  // Data
  const all = useQuery({
    queryKey: ["inv", "all_data"],
    queryFn: () => api<AllData>("GET", "/api/all_data"),
    refetchInterval: 15000,
  });

  const inv = useQuery({
    queryKey: ["inv", "list"],
    queryFn: () => api<InventoryListResp>("GET", "/api/inventory/list"),
  });
  const inventoryFiles: InventoryFile[] = useMemo(() => {
    const d = inv.data;
    if (!d) return [];
    return d.files || d.inventories || [];
  }, [inv.data]);

  const hosts = all.data?.hosts || [];
  const hostsInfo = all.data?.hosts_info || {};
  const hostVars = all.data?.host_vars || {};
  const groups = all.data?.groups || {};
  const groupVarsByGroup = all.data?.group_vars_by_group || {};

  // Connection secret per host (from host_vars.connectionSecret)
  const hostConnSecret: Record<string, string | null> = useMemo(() => {
    const m: Record<string, string | null> = {};
    Object.entries(hostVars).forEach(([h, v]) => {
      const cs = (v as any)?.connectionSecret;
      if (cs) m[h] = String(cs);
    });
    return m;
  }, [hostVars]);

  // Secrets meta for picker
  const secretsQ = useQuery({
    queryKey: ["secrets-meta"],
    queryFn: () => api<{ success?: boolean; secrets?: { name: string; type: string; username?: string }[] }>("GET", "/api/secrets/meta"),
  });
  const secretsList = secretsQ.data?.secrets || [];

  // Host -> groups map
  const hostGroups: Record<string, string[]> = useMemo(() => {
    const map: Record<string, string[]> = {};
    Object.entries(groups).forEach(([gName, gData]) => {
      (gData?.hosts || []).forEach((h) => {
        if (!map[h]) map[h] = [];
        map[h].push(gName);
      });
    });
    return map;
  }, [groups]);

  const filteredHosts = useMemo(() => {
    return hosts.filter((h) => {
      if (selectedGroup && !(hostGroups[h] || []).includes(selectedGroup)) return false;
      if (!search) return true;
      const hay = `${h} ${(hostGroups[h] || []).join(" ")} ${hostsInfo[h]?.inventory_file || ""}`.toLowerCase();
      return hay.includes(search.toLowerCase());
    });
  }, [hosts, search, selectedGroup, hostGroups, hostsInfo]);

  // Mutations
  const addHost = useMutation({
    mutationFn: (payload: { host_name: string; host_ip?: string; group_name?: string; inventory_file?: string }) =>
      api("POST", "/api/inventory/add_host", payload),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["inv"] }); setAddOpen(false); },
  });

  // Remove a host by clearing all its group memberships via the backend
  // (uses the YAML loader so it can't corrupt the inventory file the way a
  // line-based strip does when the host is the only entry under `hosts:`).
  const removeHostFromInventory = async (host: string) => {
    await api("PUT", `/api/inventory/hosts/${encodeURIComponent(host)}/groups`, { groups: [] });
    // Best-effort: also drop the per-host vars file. Ignore 404s.
    try {
      await api("POST", "/api/host_vars/delete", { file: `${host}.yml` });
    } catch {
      /* no host_vars file — fine */
    }
  };


  const delHost = useMutation({
    mutationFn: (host_name: string) => removeHostFromInventory(host_name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["inv"] }); setConfirmDelete(null); toast.success("Host deleted"); },
    onError: (e: Error) => toast.error(e.message),
  });

  const bulkDel = useMutation({
    mutationFn: async (names: string[]) => {
      let ok = 0; const fails: string[] = [];
      for (const n of names) {
        try { await removeHostFromInventory(n); ok++; }
        catch (e) { fails.push(`${n}: ${(e as Error).message}`); }
      }
      return { ok, fails };
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["inv"] });
      setSelected(new Set()); setBulkDelete(false);
      if (r.fails.length === 0) toast.success(`Deleted ${r.ok} host(s)`);
      else if (r.ok > 0) toast.warning(`Deleted ${r.ok}, ${r.fails.length} failed`);
      else toast.error(`Failed to delete hosts`);
    },
  });

  const checkHosts = useMutation({
    mutationFn: async (host_names: string[]) => {
      const targets = Array.from(new Set(host_names.map((h) => String(h).trim()).filter(Boolean)));
      if (targets.length === 0) {
        throw new Error("No hosts to check.");
      }
      // Send per-host connection secrets when we have them on the client; the backend
      // will fall back to group_vars / all_vars inheritance for hosts that don't have
      // an explicit one (this matches how the old frontend behaved and lets you check
      // hosts that inherit their credentials from the group referenced by a playbook).
      const connection_secrets: Record<string, string> = {};
      targets.forEach((h) => { if (hostConnSecret[h]) connection_secrets[h] = hostConnSecret[h]!; });
      const inv_files = Array.from(new Set(
        targets.map((h) => hostsInfo[h]?.inventory_file).filter(Boolean) as string[]
      ));
      const ansible_config = (typeof window !== "undefined" && localStorage.getItem("selected_ansible_config")) || "ansible.cfg";
      return api<{ success?: boolean; executionId?: string; error?: string }>("POST", "/api/check_hosts", {
        hosts: targets,
        host_names: targets,
        inventory_files: inv_files.length ? inv_files : ["inventory.yml"],
        ansible_config,
        connection_secrets,
      });
    },
    onSuccess: (res) => {
      const id = (res as any)?.executionId;
      toast.success(id ? `Host check queued (id: ${id})` : "Host check queued");
      setTimeout(() => qc.invalidateQueries({ queryKey: ["inv"] }), 3000);
      setTimeout(() => qc.invalidateQueries({ queryKey: ["inv"] }), 10000);
    },
    onError: (e: Error) => toast.error(e.message),
  });


  const setConnSecret = useMutation({
    mutationFn: ({ host, secretName, ansibleUser, port }: { host: string; secretName: string | null; ansibleUser: string; port: string }) =>
      api("PUT", `/api/hosts/${encodeURIComponent(host)}/connection-secret`, { secretName, ansibleUser, port }),
    onSuccess: () => {
      toast.success("Connection saved");
      qc.invalidateQueries({ queryKey: ["inv"] });
      setEditConn(null);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const updateHostGroups = useMutation({
    mutationFn: ({ host, groupNames }: { host: string; groupNames: string[] }) =>
      api("PUT", `/api/inventory/hosts/${encodeURIComponent(host)}/groups`, { groups: groupNames }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["inv"] }); setEditHost(null); },
  });

  const addGroup = useMutation({
    mutationFn: (group_name: string) => api("POST", "/api/inventory/groups", { group_name }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["inv"] }); setAddGroupOpen(false); },
  });

  const delGroup = useMutation({
    mutationFn: (g: string) => api("DELETE", `/api/inventory/groups/${encodeURIComponent(g)}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["inv"] }); setDeleteGroup(null); },
  });

  const toggleSelect = (h: string) => {
    const next = new Set(selected);
    if (next.has(h)) next.delete(h); else next.add(h);
    setSelected(next);
  };
  const toggleAll = () => {
    if (selected.size === filteredHosts.length) setSelected(new Set());
    else setSelected(new Set(filteredHosts));
  };

  const exportCsv = () => {
    const rows = [["host", "groups", "inventory_file", "vars_file", "status"]];
    filteredHosts.forEach((h) => {
      const info = hostsInfo[h] || {};
      rows.push([h, (hostGroups[h] || []).join("|"), info.inventory_file || "", info.vars_file || "", info.status || ""]);
    });
    const csv = rows.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "hosts.csv";
    a.click();
  };

  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "Infrastructure" }, { label: "Hosts & Groups", icon: <Server className="h-3.5 w-3.5" /> }]} />
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold">{t("page.hosts.title")}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">{t("page.hosts.subtitle")}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => { all.refetch(); inv.refetch(); }}>
            <RefreshCw className={`h-4 w-4 mr-1.5 ${all.isFetching ? "animate-spin" : ""}`} /> Refresh
          </Button>
          {tab === "hosts" && (
            <>
              <Button variant="outline" size="sm" onClick={() => checkHosts.mutate(filteredHosts)} disabled={checkHosts.isPending || filteredHosts.length === 0}>
                <Activity className="h-4 w-4 mr-1.5" /> Check all
              </Button>
              <Button variant="outline" size="sm" onClick={() => setImportCloudOpen(true)}>
                <Boxes className="h-4 w-4 mr-1.5" /> Import from Cloud
              </Button>
              <Button size="sm" onClick={() => setAddOpen(true)}><Plus className="h-4 w-4 mr-1.5" /> Add Host</Button>
            </>
          )}
          {tab === "groups" && (
            <Button size="sm" onClick={() => setAddGroupOpen(true)}><Plus className="h-4 w-4 mr-1.5" /> Add Group</Button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-[var(--color-border)] flex gap-1">
        {[
          { k: "hosts", label: "Hosts", icon: <Server className="h-3.5 w-3.5" />, count: hosts.length },
          { k: "groups", label: "Groups", icon: <Layers className="h-3.5 w-3.5" />, count: Object.keys(groups).length },
          { k: "vars", label: "Group Vars", icon: <Variable className="h-3.5 w-3.5" />, count: Object.keys(groupVarsByGroup).length },
          { k: "host-vars", label: "Host Vars", icon: <Variable className="h-3.5 w-3.5" />, count: Object.keys(hostVars).length },
          { k: "files", label: "Inventory Files", icon: <FolderTree className="h-3.5 w-3.5" />, count: inventoryFiles.length },
        ].map((t) => (
          <button
            key={t.k}
            onClick={() => setTab(t.k as typeof tab)}
            className={`px-3 py-2 text-sm border-b-2 -mb-px flex items-center gap-1.5 transition ${
              tab === t.k
                ? "border-[var(--color-primary)] text-[var(--color-primary)]"
                : "border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
            }`}
          >
            {t.icon} {t.label} <span className="text-[10px] opacity-60">({t.count})</span>
          </button>
        ))}
      </div>

      {tab === "hosts" && (
        <div className="grid grid-cols-1 lg:grid-cols-[240px_1fr] gap-4">
          {/* Groups sidebar */}
          <Card>
            <CardHeader className="py-3"><CardTitle className="text-sm">Filter by group</CardTitle></CardHeader>
            <CardContent className="p-2 space-y-0.5">
              <button
                className={`w-full text-left px-3 py-1.5 rounded-md text-sm hover:bg-[var(--color-accent)] ${!selectedGroup ? "bg-[var(--color-accent)]" : ""}`}
                onClick={() => setSelectedGroup(null)}
              >All hosts <span className="text-[var(--color-muted-foreground)] text-xs">({hosts.length})</span></button>
              {Object.entries(groups).filter(([gName]) => gName !== "all").map(([gName, gData]) => (
                <button
                  key={gName}
                  className={`w-full text-left px-3 py-1.5 rounded-md text-sm hover:bg-[var(--color-accent)] ${selectedGroup === gName ? "bg-[var(--color-accent)]" : ""}`}
                  onClick={() => setSelectedGroup(gName)}
                >
                  {gName} <span className="text-[var(--color-muted-foreground)] text-xs">({(gData?.hosts || []).length})</span>
                </button>
              ))}
            </CardContent>
          </Card>

          {/* Hosts table */}
          <Card>
            <CardHeader className="py-3 flex flex-row items-center justify-between gap-2 flex-wrap">
              <div className="flex items-center gap-2">
                <CardTitle className="text-sm">{selectedGroup ?? "All hosts"}</CardTitle>
                {selected.size > 0 && (
                  <Badge variant="default" className="text-[11px]">{selected.size} selected</Badge>
                )}
              </div>
              <div className="flex items-center gap-2">
                {selected.size > 0 && (
                  <>
                    <Button size="sm" variant="outline" onClick={() => checkHosts.mutate(Array.from(selected))}>
                      <Activity className="h-3.5 w-3.5 mr-1" /> Check
                    </Button>
                    <Button size="sm" variant="outline" onClick={exportCsv}>
                      <Download className="h-3.5 w-3.5 mr-1" /> Export
                    </Button>
                    <Button size="sm" variant="destructive" onClick={() => setBulkDelete(true)}>
                      <Trash2 className="h-3.5 w-3.5 mr-1" /> Delete
                    </Button>
                  </>
                )}
                <div className="relative w-56">
                  <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
                  <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search hosts…" className="h-8 pl-7 text-sm" />
                </div>
              </div>
            </CardHeader>
            <CardContent className="p-0 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left border-b text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
                    <th className="px-3 py-2 w-8">
                      <input type="checkbox" checked={selected.size > 0 && selected.size === filteredHosts.length} onChange={toggleAll} />
                    </th>
                    <th className="px-3 py-2 font-medium">Host</th>
                    <th className="px-3 py-2 font-medium">Groups</th>
                    <th className="px-3 py-2 font-medium">Connection</th>
                    <th className="px-3 py-2 font-medium">Inventory</th>
                    <th className="px-3 py-2 font-medium">Status</th>
                    <th className="px-3 py-2 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {all.isLoading && (
                    <tr><td colSpan={6} className="px-3 py-8 text-center text-[var(--color-muted-foreground)]">Loading…</td></tr>
                  )}
                  {!all.isLoading && filteredHosts.length === 0 && (
                    <tr><td colSpan={6} className="px-3 py-8 text-center text-[var(--color-muted-foreground)]">No hosts found.</td></tr>
                  )}
                  {filteredHosts.map((h) => {
                    const info = hostsInfo[h] || {};
                    const hg = hostGroups[h] || [];
                    return (
                      <tr key={h} className="border-b hover:bg-[var(--color-accent)]/40">
                        <td className="px-3 py-2">
                          <input type="checkbox" checked={selected.has(h)} onChange={() => toggleSelect(h)} />
                        </td>
                        <td className="px-3 py-2 font-medium">{h}</td>
                        <td className="px-3 py-2">
                          <div className="flex flex-wrap gap-1">
                            {hg.length === 0 && <span className="text-[var(--color-muted-foreground)]">—</span>}
                            {hg.slice(0, 3).map((g) => <Badge key={g} variant="default" className="text-[10px]">{g}</Badge>)}
                            {hg.length > 3 && <Badge variant="default" className="text-[10px]" title={hg.join(", ")}>+{hg.length - 3}</Badge>}
                          </div>
                        </td>
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-2">
                            {hostConnSecret[h] ? (
                              <span className="inline-flex items-center gap-1 text-xs">
                                <KeyRound className="h-3 w-3 text-emerald-500" />
                                <span className="font-mono">{hostConnSecret[h]}</span>
                              </span>
                            ) : (
                              <span className="text-xs text-amber-500">— none —</span>
                            )}
                            {hasPlaintextSecret(hostVars[h] as Record<string, unknown>) && (
                              <span
                                title="host_vars contains a sensitive-looking key that is not vault-encrypted. It will be pushed to git in cleartext."
                                className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full border border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                              >
                                <AlertTriangle className="h-3 w-3" />
                                plaintext
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-3 py-2 text-[var(--color-muted-foreground)] text-xs font-mono">{info.inventory_file || "—"}</td>
                        <td className="px-3 py-2"><StatusPill status={info.status} lastChecked={info.last_checked_at} /></td>
                        <td className="px-3 py-2 text-right">
                          <div className="flex justify-end gap-1">
                            <Button size="sm" variant="ghost" onClick={() => setEditConn(h)} title={t("hosts.setConnectionSecret")}>
                              <KeyRound className="h-3.5 w-3.5" />
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => checkHosts.mutate([h])} title={t("hosts.checkConnectivity")}>
                              <Activity className="h-3.5 w-3.5" />
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setEditHost(h)} title={t("hosts.editGroups")}>
                              <Edit3 className="h-3.5 w-3.5" />
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(h)} title={t("hosts.deleteHost")}>
                              <Trash2 className="h-3.5 w-3.5 text-red-500" />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </div>
      )}

      {tab === "groups" && (
        <Card>
          <CardContent className="p-0 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
                  <th className="px-3 py-2 font-medium">Group</th>
                  <th className="px-3 py-2 font-medium">Hosts</th>
                  <th className="px-3 py-2 font-medium">Vars</th>
                  <th className="px-3 py-2 font-medium">Inventory file</th>
                  <th className="px-3 py-2 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(groups).length === 0 && (
                  <tr><td colSpan={5} className="px-3 py-8 text-center text-[var(--color-muted-foreground)]">No groups yet.</td></tr>
                )}
                {Object.entries(groups).map(([gName, gData]) => {
                  const hostsArr = gData?.hosts || [];
                  const vars = groupVarsByGroup[gName] || {};
                  return (
                    <tr key={gName} className="border-b hover:bg-[var(--color-accent)]/40">
                      <td className="px-3 py-2 font-medium">{gName}</td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {hostsArr.length === 0 && <span className="text-[var(--color-muted-foreground)]">—</span>}
                          {hostsArr.slice(0, 4).map((h) => <Badge key={h} variant="default" className="text-[10px]">{h}</Badge>)}
                          {hostsArr.length > 4 && <Badge variant="default" className="text-[10px]" title={hostsArr.join(", ")}>+{hostsArr.length - 4}</Badge>}
                        </div>
                      </td>
                      <td className="px-3 py-2 text-[var(--color-muted-foreground)] text-xs">{Object.keys(vars).length} keys</td>
                      <td className="px-3 py-2 text-[var(--color-muted-foreground)] text-xs font-mono">{gData?.inventory_file || "—"}</td>
                      <td className="px-3 py-2 text-right">
                        <Button size="sm" variant="ghost" onClick={() => { setTab("vars"); setSelectedGroup(gName); }}>
                          <Variable className="h-3.5 w-3.5" />
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setDeleteGroup(gName)}>
                          <Trash2 className="h-3.5 w-3.5 text-red-500" />
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {tab === "vars" && (
        <GroupVarsTab
          groups={Object.keys(groups)}
          selected={selectedGroup}
          setSelected={setSelectedGroup}
          groupVarsByGroup={groupVarsByGroup}
          groupsData={groups}
        />
      )}

      {tab === "host-vars" && (
        <HostVarsTab
          hosts={hosts}
          selected={selectedHostVar}
          setSelected={setSelectedHostVar}
          hostVars={hostVars}
          hostsInfo={hostsInfo}
        />
      )}

      {tab === "files" && (
        <InventoryFilesTab files={inventoryFiles} loading={inv.isLoading} />
      )}

      {/* Add Host dialog */}
      <AddHostDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        groups={Object.keys(groups)}
        inventoryFiles={inventoryFiles}
        busy={addHost.isPending}
        onSubmit={(p) => addHost.mutate(p)}
      />

      {/* Edit Host groups dialog */}
      <EditHostDialog
        host={editHost}
        groups={Object.keys(groups)}
        currentGroups={editHost ? (hostGroups[editHost] || []) : []}
        onClose={() => setEditHost(null)}
        onSubmit={(gs) => updateHostGroups.mutate({ host: editHost!, groupNames: gs })}
        busy={updateHostGroups.isPending}
      />

      {/* Add Group */}
      <AddGroupDialog open={addGroupOpen} onClose={() => setAddGroupOpen(false)} onSubmit={(n) => addGroup.mutate(n)} busy={addGroup.isPending} />

      {/* Set Connection Secret */}
      <SetConnectionDialog
        host={editConn}
        secrets={secretsList}
        currentSecret={editConn ? hostConnSecret[editConn] || null : null}
        currentUser={editConn ? (hostVars[editConn] as any)?.ansible_user || "root" : "root"}
        currentPort={editConn ? String((hostVars[editConn] as any)?.ansible_port ?? 22) : "22"}
        busy={setConnSecret.isPending}
        onClose={() => setEditConn(null)}
        onSubmit={(payload) => setConnSecret.mutate({ host: editConn!, ...payload })}
      />

      {/* Confirm dialogs */}
      <ConfirmDialog
        open={!!confirmDelete}
        title={t("hosts.deleteHost")}
        description={<>Are you sure you want to remove <span className="font-mono">{confirmDelete}</span> from the inventory?</>}
        variant="destructive"
        confirmLabel={t("common.delete")}
        busy={delHost.isPending}
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => confirmDelete && delHost.mutate(confirmDelete)}
      />
      <ConfirmDialog
        open={bulkDelete}
        title={t("hosts.deleteSelected")}
        description={<>This will remove {selected.size} hosts from the inventory.</>}
        variant="destructive"
        confirmLabel={t("common.delete")}
        busy={bulkDel.isPending}
        onCancel={() => setBulkDelete(false)}
        onConfirm={() => bulkDel.mutate(Array.from(selected))}
      />
      <ConfirmDialog
        open={!!deleteGroup}
        title={t("hosts.deleteGroup")}
        description={<>Remove group <span className="font-mono">{deleteGroup}</span>? Hosts will not be deleted.</>}
        variant="destructive"
        confirmLabel={t("common.delete")}
        busy={delGroup.isPending}
        onCancel={() => setDeleteGroup(null)}
        onConfirm={() => deleteGroup && delGroup.mutate(deleteGroup)}
      />

      <ImportFromCloudDialog
        open={importCloudOpen}
        onClose={() => setImportCloudOpen(false)}
        onImported={() => { all.refetch(); inv.refetch(); }}
        inventoryFiles={inventoryFiles}
        defaultInventoryFile={inventoryFiles[0]?.relative_path || inventoryFiles[0]?.path}
      />
    </div>
  );
}

// ---------- Add Host Dialog ----------
function AddHostDialog({
  open, onClose, groups, inventoryFiles, busy, onSubmit,
}: {
  open: boolean; onClose: () => void; groups: string[]; inventoryFiles: InventoryFile[]; busy: boolean;
  onSubmit: (p: { host_name: string; host_ip?: string; group_name?: string; inventory_file?: string }) => void;
}) {
  const t = useT();
  const [name, setName] = useState("");
  const [ip, setIp] = useState("");
  const [group, setGroup] = useState("all");
  const [file, setFile] = useState("inventory.yml");

  useEffect(() => {
    if (open) {
      setName(""); setIp(""); setGroup("all");
      const first = inventoryFiles[0]?.relative_path || inventoryFiles[0]?.path || "inventory.yml";
      setFile(first);
    }
  }, [open, inventoryFiles]);

  return (
    <Modal
      open={open}
      title={t("hosts.addHost")}
      onClose={onClose}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={busy}>{t("common.cancel")}</Button>
          <Button onClick={() => onSubmit({ host_name: name.trim(), host_ip: ip.trim() || undefined, group_name: group, inventory_file: file })} disabled={busy || !name.trim()}>
            {busy ? t("hosts.adding") : t("hosts.addHost")}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label={t("common.name") + " *"}>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="web-01" />
        </Field>
        <Field label="IP / ansible_host">
          <Input value={ip} onChange={(e) => setIp(e.target.value)} placeholder="10.0.0.5 (optional)" />
        </Field>
        <Field label={t("common.group")}>
          <select value={group} onChange={(e) => setGroup(e.target.value)} className="w-full h-9 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-3 text-sm">
            <option value="all">all</option>
            {groups.filter((g) => g !== "all").map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
        </Field>
        <Field label={t("hosts.inventoryFile")}>
          <select value={file} onChange={(e) => setFile(e.target.value)} className="w-full h-9 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-3 text-sm">
            {inventoryFiles.length === 0 && <option value="inventory.yml">inventory.yml</option>}
            {inventoryFiles.map((f) => (
              <option key={f.path} value={f.relative_path || f.path}>{f.relative_path || f.path}</option>
            ))}
          </select>
        </Field>
      </div>
    </Modal>
  );
}

// ---------- Edit Host Dialog ----------
function EditHostDialog({
  host, groups, currentGroups, onClose, onSubmit, busy,
}: {
  host: string | null; groups: string[]; currentGroups: string[]; onClose: () => void; onSubmit: (g: string[]) => void; busy: boolean;
}) {
  const t = useT();
  const [sel, setSel] = useState<Set<string>>(new Set());
  useEffect(() => { if (host) setSel(new Set(currentGroups)); }, [host, currentGroups]);
  const toggle = (g: string) => {
    const n = new Set(sel);
    if (n.has(g)) n.delete(g); else n.add(g);
    setSel(n);
  };
  return (
    <Modal
      open={!!host}
      title={host ? `${t("hosts.editGroups")} · ${host}` : t("common.edit")}
      onClose={onClose}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={busy}>{t("common.cancel")}</Button>
          <Button onClick={() => onSubmit(Array.from(sel))} disabled={busy}>{busy ? t("hosts.saving") : t("common.save")}</Button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-2 max-h-72 overflow-y-auto">
        {groups.length === 0 && <div className="col-span-2 text-sm text-[var(--color-muted-foreground)]">No groups defined.</div>}
        {groups.map((g) => (
          <label key={g} className="flex items-center gap-2 text-sm px-2 py-1.5 rounded hover:bg-[var(--color-accent)] cursor-pointer">
            <input type="checkbox" checked={sel.has(g)} onChange={() => toggle(g)} />
            <span>{g}</span>
          </label>
        ))}
      </div>
    </Modal>
  );
}

// ---------- Add Group Dialog ----------
function AddGroupDialog({ open, onClose, onSubmit, busy }: { open: boolean; onClose: () => void; onSubmit: (n: string) => void; busy: boolean }) {
  const t = useT();
  const [name, setName] = useState("");
  useEffect(() => { if (open) setName(""); }, [open]);
  return (
    <Modal
      open={open} title={t("hosts.addGroup")} onClose={onClose}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={busy}>{t("common.cancel")}</Button>
          <Button onClick={() => onSubmit(name.trim())} disabled={busy || !name.trim()}>{busy ? t("hosts.adding") : t("hosts.addGroup")}</Button>
        </>
      }
    >
      <Field label={t("hosts.groupName")}>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="webservers" />
      </Field>
    </Modal>
  );
}

// ---------- Group Vars Tab ----------
function GroupVarsTab({
  groups, selected, setSelected, groupVarsByGroup, groupsData,
}: {
  groups: string[]; selected: string | null; setSelected: (n: string | null) => void;
  groupVarsByGroup: Record<string, Record<string, unknown>>;
  groupsData: Record<string, GroupData>;
}) {
  const qc = useQueryClient();
  const fileName = selected ? `${selected}.yml` : null;
  const inventoryFile = selected ? groupsData[selected]?.inventory_file : undefined;

  const fileQ = useQuery({
    queryKey: ["group_vars", "get", selected, inventoryFile ?? ""],
    queryFn: async () => {
      const qp = new URLSearchParams({ file: fileName! });
      if (inventoryFile) qp.set("inventory_file", inventoryFile);
      try {
        const r = await api<{ success: boolean; content?: string }>("GET", `/api/group_vars/get?${qp.toString()}`);
        return r.content || "";
      } catch {
        // file doesn't exist yet — start with empty stub
        return `---\n# group_vars for ${selected}\n`;
      }
    },
    enabled: !!selected,
  });

  const [draft, setDraft] = useState("");
  const [original, setOriginal] = useState("");
  useEffect(() => {
    if (fileQ.data !== undefined) { setDraft(fileQ.data); setOriginal(fileQ.data); }
  }, [fileQ.data]);

  const saveMut = useMutation({
    mutationFn: () =>
      api("POST", "/api/group_vars/save", { file: fileName, content: draft, inventory_file: inventoryFile }),
    onSuccess: () => {
      setOriginal(draft);
      qc.invalidateQueries({ queryKey: ["inv"] });
      qc.invalidateQueries({ queryKey: ["group_vars"] });
    },
  });
  const dirty = draft !== original;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[220px_1fr] gap-4">
      <Card>
        <CardHeader className="py-3"><CardTitle className="text-sm">Groups</CardTitle></CardHeader>
        <CardContent className="p-2 space-y-0.5 max-h-[70vh] overflow-auto">
          {groups.length === 0 && <div className="px-3 py-2 text-xs text-[var(--color-muted-foreground)]">No groups.</div>}
          {groups.map((g) => (
            <button
              key={g}
              onClick={() => setSelected(g)}
              className={`w-full text-left px-3 py-1.5 rounded-md text-sm hover:bg-[var(--color-accent)] ${selected === g ? "bg-[var(--color-accent)] text-[var(--color-primary)] font-medium" : ""}`}
            >
              {g}
              <span className="text-[10px] ml-2 text-[var(--color-muted-foreground)]">
                {Object.keys(groupVarsByGroup[g] || {}).length} keys
              </span>
            </button>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="py-3 flex flex-row items-center justify-between gap-2">
          <CardTitle className="text-sm font-mono truncate">
            {selected ? `group_vars/${fileName}` : "Select a group"}
          </CardTitle>
          {selected && (
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => fileQ.refetch()} disabled={fileQ.isFetching}>
                <RefreshCw className={`h-3.5 w-3.5 mr-1 ${fileQ.isFetching ? "animate-spin" : ""}`} /> Reload
              </Button>
              <Button size="sm" disabled={!dirty || saveMut.isPending} onClick={() => saveMut.mutate()}>
                {saveMut.isPending ? "Saving…" : dirty ? "Save" : "Saved"}
              </Button>
            </div>
          )}
        </CardHeader>
        <CardContent className="p-3 space-y-2">
          {!selected && <div className="text-sm text-[var(--color-muted-foreground)] p-4 text-center">Pick a group from the left to edit its variables.</div>}
          {selected && fileQ.isLoading && <div className="text-sm text-[var(--color-muted-foreground)] p-4">Loading…</div>}
          {selected && !fileQ.isLoading && (
            <>
              {yamlHasPlaintextSecret(draft) && <PlaintextSecretBanner what="group_vars" />}
              <YamlEditor value={draft} onChange={setDraft} height={460} />
            </>
          )}
          {saveMut.isError && <div className="text-xs text-red-500">{(saveMut.error as Error).message}</div>}
        </CardContent>
      </Card>
    </div>
  );
}

// ---------- Host Vars Tab ----------
function HostVarsTab({
  hosts, selected, setSelected, hostVars, hostsInfo,
}: {
  hosts: string[];
  selected: string | null;
  setSelected: (n: string | null) => void;
  hostVars: Record<string, Record<string, unknown>>;
  hostsInfo: Record<string, HostInfo>;
}) {
  const qc = useQueryClient();
  const fileName = selected ? `${selected}.yml` : null;
  const inventoryFile = selected ? hostsInfo[selected]?.inventory_file : undefined;

  const fileQ = useQuery({
    queryKey: ["host_vars", "get", selected, inventoryFile ?? ""],
    queryFn: async () => {
      const qp = new URLSearchParams({ file: fileName! });
      if (inventoryFile) qp.set("inventory_file", inventoryFile);
      try {
        const r = await api<{ success: boolean; content?: string }>("GET", `/api/host_vars/get?${qp.toString()}`);
        return r.content || "";
      } catch {
        return `---\n# host_vars for ${selected}\n`;
      }
    },
    enabled: !!selected,
  });

  const [draft, setDraft] = useState("");
  const [original, setOriginal] = useState("");
  useEffect(() => {
    if (fileQ.data !== undefined) { setDraft(fileQ.data); setOriginal(fileQ.data); }
  }, [fileQ.data]);

  const saveMut = useMutation({
    mutationFn: () =>
      api("POST", "/api/host_vars/save", { file: fileName, content: draft, inventory_file: inventoryFile }),
    onSuccess: () => {
      setOriginal(draft);
      qc.invalidateQueries({ queryKey: ["inv"] });
      qc.invalidateQueries({ queryKey: ["host_vars"] });
      toast.success(`Saved host_vars/${fileName}`);
    },
    onError: (e) => toast.error((e as Error).message),
  });
  const dirty = draft !== original;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[240px_1fr] gap-4">
      <Card>
        <CardHeader className="py-3"><CardTitle className="text-sm">Hosts</CardTitle></CardHeader>
        <CardContent className="p-2 space-y-0.5 max-h-[70vh] overflow-auto">
          {hosts.length === 0 && <div className="px-3 py-2 text-xs text-[var(--color-muted-foreground)]">No hosts.</div>}
          {hosts.map((h) => {
            const keyCount = Object.keys(hostVars[h] || {}).length;
            const hasSecret = hasPlaintextSecret(hostVars[h] as Record<string, unknown>);
            return (
              <button
                key={h}
                onClick={() => setSelected(h)}
                className={`w-full text-left px-3 py-1.5 rounded-md text-sm hover:bg-[var(--color-accent)] flex items-center justify-between gap-2 ${selected === h ? "bg-[var(--color-accent)] text-[var(--color-primary)] font-medium" : ""}`}
              >
                <span className="truncate font-mono text-xs">{h}</span>
                <span className="flex items-center gap-1 flex-shrink-0">
                  {hasSecret && <AlertTriangle className="h-3 w-3 text-amber-500" />}
                  <span className="text-[10px] text-[var(--color-muted-foreground)]">{keyCount}</span>
                </span>
              </button>
            );
          })}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="py-3 flex flex-row items-center justify-between gap-2">
          <CardTitle className="text-sm font-mono truncate">
            {selected ? `host_vars/${fileName}` : "Select a host"}
          </CardTitle>
          {selected && (
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => fileQ.refetch()} disabled={fileQ.isFetching}>
                <RefreshCw className={`h-3.5 w-3.5 mr-1 ${fileQ.isFetching ? "animate-spin" : ""}`} /> Reload
              </Button>
              <Button size="sm" disabled={!dirty || saveMut.isPending} onClick={() => saveMut.mutate()}>
                {saveMut.isPending ? "Saving…" : dirty ? "Save" : "Saved"}
              </Button>
            </div>
          )}
        </CardHeader>
        <CardContent className="p-3 space-y-2">
          {!selected && <div className="text-sm text-[var(--color-muted-foreground)] p-4 text-center">Pick a host from the left to view or edit its variables.</div>}
          {selected && fileQ.isLoading && <div className="text-sm text-[var(--color-muted-foreground)] p-4">Loading…</div>}
          {selected && !fileQ.isLoading && (
            <>
              {yamlHasPlaintextSecret(draft) && <PlaintextSecretBanner what="host_vars" />}
              <YamlEditor value={draft} onChange={setDraft} height={460} />
            </>
          )}
          {saveMut.isError && <div className="text-xs text-red-500">{(saveMut.error as Error).message}</div>}
        </CardContent>
      </Card>
    </div>
  );
}

function InventoryFilesTab({ files, loading }: { files: InventoryFile[]; loading: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(files[0]?.relative_path || files[0]?.path || null);
  useEffect(() => {
    if (!open && files[0]) setOpen(files[0].relative_path || files[0].path);
  }, [files, open]);

  const fileQ = useQuery({
    queryKey: ["inventory", "get", open],
    queryFn: async () => {
      const r = await api<{ success: boolean; content?: string }>("GET", `/api/inventory/get?file=${encodeURIComponent(open!)}`);
      return r.content || "";
    },
    enabled: !!open,
  });

  const [draft, setDraft] = useState("");
  const [original, setOriginal] = useState("");
  useEffect(() => {
    if (fileQ.data !== undefined) { setDraft(fileQ.data); setOriginal(fileQ.data); }
  }, [fileQ.data]);

  const saveMut = useMutation({
    mutationFn: () => api("POST", "/api/inventory/save", { file: open, content: draft }),
    onSuccess: () => { setOriginal(draft); qc.invalidateQueries({ queryKey: ["inv"] }); },
  });
  const dirty = draft !== original;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">
      <Card>
        <CardHeader className="py-3"><CardTitle className="text-sm">Inventory Files</CardTitle></CardHeader>
        <CardContent className="p-2 space-y-0.5 max-h-[70vh] overflow-auto">
          {loading && <div className="px-3 py-2 text-xs text-[var(--color-muted-foreground)]">Loading…</div>}
          {!loading && files.length === 0 && <div className="px-3 py-2 text-xs text-[var(--color-muted-foreground)]">No inventory files.</div>}
          {files.map((f) => {
            const path = f.relative_path || f.path;
            const sel = open === path;
            return (
              <button
                key={f.path}
                onClick={() => setOpen(path)}
                className={`w-full flex items-center gap-2 px-2 py-1.5 rounded text-left text-sm hover:bg-[var(--color-accent)]/60 ${sel ? "bg-[var(--color-accent)] text-[var(--color-primary)]" : ""}`}
              >
                <FileText className="h-3.5 w-3.5 shrink-0 text-[var(--color-muted-foreground)]" />
                <span className="truncate font-mono text-xs">{f.name}</span>
              </button>
            );
          })}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="py-3 flex flex-row items-center justify-between gap-2">
          <CardTitle className="text-sm font-mono truncate">{open ?? "Select a file"}</CardTitle>
          {open && (
            <div className="flex gap-2">
              <a
                href={`/api/inventory/download?file=${encodeURIComponent(open)}`}
                className="inline-flex items-center text-xs gap-1 px-2.5 h-8 rounded-md border border-[var(--color-border)] hover:bg-[var(--color-accent)]"
              >
                <Download className="h-3.5 w-3.5" /> Download
              </a>
              <Button size="sm" variant="outline" onClick={() => fileQ.refetch()} disabled={fileQ.isFetching}>
                <RefreshCw className={`h-3.5 w-3.5 mr-1 ${fileQ.isFetching ? "animate-spin" : ""}`} /> Reload
              </Button>
              <Button size="sm" disabled={!dirty || saveMut.isPending} onClick={() => saveMut.mutate()}>
                {saveMut.isPending ? "Saving…" : dirty ? "Save" : "Saved"}
              </Button>
            </div>
          )}
        </CardHeader>
        <CardContent className="p-3 space-y-2">
          {!open && <div className="text-sm text-[var(--color-muted-foreground)] p-4 text-center">Pick a file to edit.</div>}
          {open && fileQ.isLoading && <div className="text-sm text-[var(--color-muted-foreground)] p-4">Loading…</div>}
          {open && !fileQ.isLoading && (
            <>
              {yamlHasPlaintextSecret(draft) && <PlaintextSecretBanner what="inventory file" />}
              <YamlEditor value={draft} onChange={setDraft} height={520} />
            </>
          )}
          {saveMut.isError && <div className="text-xs text-red-500">{(saveMut.error as Error).message}</div>}
        </CardContent>
      </Card>
    </div>
  );
}


// ---------- helpers ----------
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="text-xs font-medium text-[var(--color-muted-foreground)]">{label}</label>
      {children}
    </div>
  );
}

// ---------- Set Connection Dialog ----------
function SetConnectionDialog({
  host, secrets, currentSecret, currentUser, currentPort, busy, onClose, onSubmit,
}: {
  host: string | null;
  secrets: { name: string; type: string; username?: string }[];
  currentSecret: string | null;
  currentUser: string;
  currentPort: string;
  busy: boolean;
  onClose: () => void;
  onSubmit: (p: { secretName: string | null; ansibleUser: string; port: string }) => void;
}) {
  const t = useT();
  const [secretName, setSecretName] = useState<string>("");
  const [ansibleUser, setAnsibleUser] = useState("root");
  const [port, setPort] = useState("22");

  useEffect(() => {
    if (host) {
      setSecretName(currentSecret || "");
      setAnsibleUser(currentUser || "root");
      setPort(currentPort || "22");
    }
  }, [host, currentSecret, currentUser, currentPort]);

  return (
    <Modal
      open={!!host}
      title={host ? `${t("hosts.connection")} · ${host}` : t("hosts.connection")}
      onClose={onClose}
      footer={
        <>
          {currentSecret && (
            <Button variant="outline" disabled={busy} onClick={() => onSubmit({ secretName: null, ansibleUser, port })}>
              {t("common.reset")}
            </Button>
          )}
          <Button variant="outline" onClick={onClose} disabled={busy}>{t("common.cancel")}</Button>
          <Button disabled={busy || !secretName} onClick={() => onSubmit({ secretName, ansibleUser, port })}>
            {busy ? t("hosts.saving") : t("common.save")}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Secret (SSH key or password)">
          <select value={secretName} onChange={(e) => setSecretName(e.target.value)}
            className="w-full h-9 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-3 text-sm">
            <option value="">— select secret —</option>
            {secrets.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name} {s.type ? `(${s.type})` : ""}{s.username ? ` · ${s.username}` : ""}
              </option>
            ))}
          </select>
          {secrets.length === 0 && (
            <div className="text-xs text-amber-500 mt-1">No secrets defined. Create one under Infrastructure → Secrets.</div>
          )}
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("hosts.ansibleUser")}>
            <Input value={ansibleUser} onChange={(e) => setAnsibleUser(e.target.value)} placeholder="root" />
          </Field>
          <Field label={t("hosts.sshPort")}>
            <Input value={port} onChange={(e) => setPort(e.target.value)} placeholder="22" />
          </Field>
        </div>
        <div className="rounded-md border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-600 dark:text-emerald-400 flex gap-2">
          <KeyRound className="h-4 w-4 mt-0.5 shrink-0" />
          <div>
            <div className="font-medium">Credentials stay out of git.</div>
            <div className="text-[11px] opacity-90 mt-0.5">
              Only <span className="font-mono">connectionSecret: {secretName || "<name>"}</span> is written to{" "}
              <span className="font-mono">host_vars/&lt;host&gt;.yml</span>. The password / SSH key is resolved from the
              encrypted secrets store at run-time — never persisted to the inventory repo.
            </div>
          </div>
        </div>
      </div>
    </Modal>
  );
}
