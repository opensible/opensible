import { useState, useMemo, useEffect } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Play, Pencil, Copy as CopyIcon, Trash2, PackageOpen, ArrowUpDown, Layers,
  ChevronLeft, ChevronRight,
} from "lucide-react";
import {
  createColumnHelper, flexRender, getCoreRowModel, getFilteredRowModel,
  getPaginationRowModel, getSortedRowModel, useReactTable, type SortingState,
} from "@tanstack/react-table";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge, statusToVariant } from "@/components/ui/badge";
import { TemplateDialog } from "./TemplateDialog";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { api } from "@/lib/api";

type Instance = {
  path: string;
  id?: string;
  filename?: string;
  template_id?: string;
  environment?: string;
  updated_at?: number;
  playbook_id?: string;
};

type InstanceDetail = {
  template_id: string;
  environment: string;
  filename: string;
  values: Record<string, unknown>;
  targets: { groups?: string[]; hosts?: string[] };
  path?: string;
  rendered_yaml?: string;
};

type Execution = {
  id?: string;
  status?: string;
  stage?: string;
  play_name?: string;
  playbookName?: string;
  playbookId?: string;
  startedAt?: number | string;
  finishedAt?: number | string;
  createdAt?: number | string;
  statusUpdatedAt?: number | string;
  selectionSnapshot?: { playbookId?: string; playbookName?: string };
};

type OpenState = {
  templateId: string;
  instancePath?: string;
  initialValues?: Record<string, unknown>;
  initialTargets?: { groups?: string[]; hosts?: string[] };
  initialEnvironment?: string;
  initialFilename?: string;
  initialYaml?: string;
};


type Row = Instance & {
  last_status?: string;
  last_stage?: string;
  last_run_at?: number | string;
  execution_id?: string;
};

const col = createColumnHelper<Row>();

function extractStage(e: Execution): string | null {
  if (e.stage) return String(e.stage).toLowerCase();
  const s = (e.play_name || "").toLowerCase();
  const m = s.match(/\[(init|validate|plan|apply|deploy|destroy)\]/);
  return m ? m[1]! : null;
}

function normStatus(raw?: string): string {
  const s = (raw || "").toLowerCase();
  if (s === "success" || s === "succeeded" || s === "ok") return "success";
  if (s === "failed" || s === "error") return "failed";
  if (s === "running") return "running";
  if (s === "queued" || s === "pending") return "queued";
  if (s === "canceled" || s === "cancelled" || s === "canceling") return "canceled";
  return s || "";
}

function toMs(v: number | string | undefined): number {
  if (v === undefined || v === null || v === "") return 0;
  if (typeof v === "number") return v < 1e12 ? v * 1000 : v;
  const trimmed = String(v).trim();
  if (/^\d+(\.\d+)?$/.test(trimmed)) {
    const n = parseFloat(trimmed);
    return n < 1e12 ? n * 1000 : n;
  }
  const d = new Date(trimmed).getTime();
  return Number.isNaN(d) ? 0 : d;
}

function relTime(v: number | string | undefined): string {
  const ms = toMs(v);
  if (!ms) return "—";
  const diff = Date.now() - ms;
  const abs = Math.abs(diff);
  const s = Math.round(abs / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(ms).toLocaleDateString();
}

function StageBadge({ stage }: { stage?: string }) {
  if (!stage) return <span className="text-[var(--color-muted-foreground)]">—</span>;
  const map: Record<string, string> = {
    init: "bg-[var(--color-primary)]/15 text-[var(--color-primary)]",
    validate: "bg-blue-500/15 text-blue-500 dark:text-blue-400",
    plan: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
    apply: "bg-[var(--color-success)]/15 text-[var(--color-success)]",
    deploy: "bg-[var(--color-success)]/15 text-[var(--color-success)]",
    destroy: "bg-[var(--color-destructive)]/15 text-[var(--color-destructive)]",
  };
  const cls = map[stage.toLowerCase()] || "bg-[var(--color-muted)] text-[var(--color-muted-foreground)]";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${cls}`}>
      <Layers className="h-3 w-3" />
      {stage}
    </span>
  );
}

function StatusPill({ status }: { status?: string }) {
  if (!status) return <span className="text-[var(--color-muted-foreground)]">—</span>;
  const n = normStatus(status);
  const running = n === "running" || n === "queued";
  const variant = statusToVariant(n);
  const dotCls =
    n === "success" ? "bg-[var(--color-success)]" :
    n === "failed" ? "bg-[var(--color-destructive)]" :
    running ? "bg-[var(--color-warning)]" :
    "bg-[var(--color-muted-foreground)]";
  return (
    <Badge variant={variant} className="text-[10px] gap-1.5 pl-1.5">
      <span className="relative inline-flex h-1.5 w-1.5">
        {running && <span className={`absolute inline-flex h-full w-full rounded-full ${dotCls} opacity-60 animate-ping`} />}
        <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${dotCls}`} />
      </span>
      {n.toUpperCase()}
    </Badge>
  );
}

export function TemplateInstancesTable({
  onRunLog,
  filterText = "",
}: {
  onRunLog?: (executionId: string) => void;
  filterText?: string;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState<OpenState | null>(null);
  const [confirmDel, setConfirmDel] = useState<Instance | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [sorting, setSorting] = useState<SortingState>([{ id: "updated_at", desc: true }]);

  const listQ = useQuery({
    queryKey: ["template-instances"],
    queryFn: () => api<{ instances: Instance[] }>("GET", "/api/templates/instances"),
    refetchInterval: 15000,
  });
  const instances = listQ.data?.instances || [];

  const execQ = useQuery({
    queryKey: ["all-executions"],
    queryFn: () => api<{ executions?: Execution[] }>("GET", "/api/executions?limit=200"),
    refetchInterval: 4000,
  });
  const executions = execQ.data?.executions || [];

  // Index executions by every plausible key (playbookId from various shapes + playbookName).
  // Executions come sorted newest-first from the backend, so the first hit wins.
  const execByKey = useMemo(() => {
    const map: Record<string, Execution> = {};
    for (const e of executions) {
      const keys = [
        e.playbookId,
        e.selectionSnapshot?.playbookId,
        e.playbookName,
        e.selectionSnapshot?.playbookName,
        (e.playbookName || "").replace(/\.ya?ml$/i, ""),
      ].filter(Boolean) as string[];
      for (const k of keys) if (!map[k]) map[k] = e;
    }
    return map;
  }, [executions]);

  const rows: Row[] = useMemo(() => instances.map((inst) => {
    const stem = (inst.filename || "").replace(/\.ya?ml$/i, "");
    const e =
      (inst.playbook_id && execByKey[inst.playbook_id]) ||
      (inst.filename && execByKey[inst.filename]) ||
      (stem && execByKey[stem]) ||
      undefined;
    return {
      ...inst,
      last_status: e?.status,
      last_stage: e ? (extractStage(e) || undefined) : undefined,
      last_run_at: e?.finishedAt || e?.startedAt || e?.statusUpdatedAt || e?.createdAt,
      execution_id: e?.id,
    };
  }), [instances, execByKey]);



  const loadDetail = async (path: string): Promise<InstanceDetail | null> => {
    try {
      return await api<InstanceDetail>("GET", `/api/templates/instances/detail?path=${encodeURIComponent(path)}`);
    } catch (e) { toast.error((e as Error).message); return null; }
  };

  const openEdit = async (inst: Instance) => {
    const d = await loadDetail(inst.path);
    if (!d) return;
    setOpen({
      templateId: d.template_id, instancePath: inst.path,
      initialValues: d.values, initialTargets: d.targets,
      initialEnvironment: d.environment, initialFilename: d.filename,
      initialYaml: d.rendered_yaml,
    });
  };

  const openDuplicate = async (inst: Instance) => {
    const d = await loadDetail(inst.path);
    if (!d) return;
    setOpen({
      templateId: d.template_id,
      initialValues: d.values, initialTargets: d.targets,
      initialEnvironment: d.environment, initialFilename: undefined,
      initialYaml: d.rendered_yaml,
    });
    toast.info("Duplicated — change the filename and/or environment before saving.");
  };


  const runNow = async (inst: Instance) => {
    if (!inst.filename) return;
    let pbId = inst.playbook_id;
    const d = await loadDetail(inst.path);
    if (!d) return;
    const isStaleK8sPlaybook = d.template_id === "k8s-cluster" && (
      !(d.rendered_yaml || "").includes("# OpenSible k8s template generation: 2026-07-cgroup-fix-v7") ||
      (d.rendered_yaml || "").includes("Wait for first control-plane apiserver to be reachable") ||
      (d.rendered_yaml || "").includes("Probe first control-plane /healthz") ||
      (d.rendered_yaml || "").includes("https://{{ first_cp_ip }}:6443/healthz") ||
      (d.rendered_yaml || "").includes("Generate fresh worker join command") ||
      (d.rendered_yaml || "").includes("Generate fresh worker join token on first control-plane") ||
      (d.rendered_yaml || "").includes("delegate_to: \"{{ first_cp_ip }}\"") ||
      (d.rendered_yaml || "").includes("--kubeconfig=/etc/kubernetes/admin.conf") ||
      (d.rendered_yaml || "").includes("kubeadm_control_plane_join_argv") ||
      (d.rendered_yaml || "").includes("kubeadm_worker_join_argv") ||
      (d.rendered_yaml || "").includes("Reset dirty kubeadm/container runtime state") ||
      (d.rendered_yaml || "").includes("rm -rf /etc/kubernetes /var/lib/etcd /etc/cni/net.d /var/lib/cni /var/run/calico /root/.kube/config") ||
      (d.rendered_yaml || "").includes("Build control-plane join argv (using first control-plane credentials)") ||
      (d.rendered_yaml || "").includes("Build worker join argv (using first control-plane token)")
    );
    if (isStaleK8sPlaybook) {
      try {
        const saved = await api<{ playbook_id?: string; filename: string }>(
          "POST", `/api/templates/${d.template_id}/save`,
          { values: d.values, targets: d.targets, environment: d.environment, filename: d.filename, instance_path: inst.path },
        );
        pbId = saved.playbook_id || saved.filename.replace(/\.ya?ml$/i, "");
        toast.info("Kubernetes template refreshed before running.");
        await qc.invalidateQueries({ queryKey: ["template-instances"] });
      } catch (e) { toast.error((e as Error).message); return; }
    }
    if (!pbId) {
      try {
        const saved = await api<{ playbook_id?: string; filename: string }>(
          "POST", `/api/templates/${d.template_id}/save`,
          { values: d.values, targets: d.targets, environment: d.environment, filename: d.filename, instance_path: inst.path },
        );
        pbId = saved.playbook_id || saved.filename.replace(/\.ya?ml$/i, "");
      } catch (e) { toast.error((e as Error).message); return; }
    }
    try {
      const res = await api<{ executionId?: string; execution_id?: string }>(
        "POST", `/api/projects/_current/playbooks/${encodeURIComponent(pbId!)}/run`,
        {
          inventory_files: ["inventory.yml"],
          ansible_config: "ansible.cfg",
          play_name: `Template instance: ${inst.filename}`,
          become: true, strategy: "linear",
        },
      );
      const id = res.executionId || res.execution_id;
      if (id) { toast.success(`Run queued (${id})`); onRunLog?.(String(id)); }
      else toast.success("Run queued");
    } catch (e) { toast.error((e as Error).message); }
  };

  const doDelete = async (inst: Instance) => {
    setDeleting(true);
    try {
      const res = await api<{ ok?: boolean; removed?: string[] }>(
        "DELETE", `/api/templates/instances?path=${encodeURIComponent(inst.path)}`,
      );
      const removed = res?.removed?.length ? ` (${res.removed.join(", ")})` : "";
      toast.success(`Deleted${removed}`);
      qc.setQueryData<{ instances: Instance[] } | undefined>(
        ["template-instances"],
        (prev) => prev
          ? { ...prev, instances: prev.instances.filter((i) => i.path !== inst.path) }
          : prev,
      );
      await qc.invalidateQueries({ queryKey: ["template-instances"] });
      await qc.invalidateQueries({ queryKey: ["all-executions"] });
      await qc.refetchQueries({ queryKey: ["template-instances"], type: "active" });
      await qc.refetchQueries({ queryKey: ["all-executions"], type: "active" });
      setConfirmDel(null);
    } catch (e) { toast.error(`Delete failed: ${(e as Error).message}`); }
    finally { setDeleting(false); }
  };

  const filteredRows = useMemo(() => {
    const s = filterText.trim().toLowerCase();
    if (!s) return rows;
    return rows.filter((inst) =>
      (inst.filename || "").toLowerCase().includes(s) ||
      (inst.path || "").toLowerCase().includes(s) ||
      (inst.template_id || "").toLowerCase().includes(s) ||
      (inst.environment || "").toLowerCase().includes(s) ||
      (inst.last_status || "").toLowerCase().includes(s)
    );
  }, [rows, filterText]);

  const columns = useMemo(() => [
    col.accessor("id", {
      header: "Job ID",
      cell: (c) => {
        const v = c.getValue();
        const short = v ? String(v).slice(-6) : "—";
        return (
          <span className="text-[var(--color-warning)] font-mono text-xs" title={v ? String(v) : ""}>
            {short}
          </span>
        );
      },
    }),
    col.accessor("environment", {
      header: "Env",
      cell: (c) => {
        const v = c.getValue();
        return <Badge variant={v && v !== "default" ? "success" : "default"} className="text-[10px]">{v || "default"}</Badge>;
      },
    }),
    col.accessor("filename", {
      header: "Instance",
      cell: (c) => {
        const name = c.getValue() || c.row.original.path;
        return (
          <div className="flex flex-col min-w-0 max-w-[360px]">
            <span
              className="font-medium text-[var(--color-foreground)] truncate hover:underline"
              title={String(name)}
            >
              {name}
            </span>
          </div>
        );
      },
    }),
    col.accessor("template_id", {
      header: "Template",
      cell: (c) => <Badge variant="default" className="text-[10px]">{c.getValue() || "—"}</Badge>,
    }),
    col.accessor("last_stage", {
      header: "Last Stage",
      cell: (c) => <StageBadge stage={c.getValue()} />,
    }),
    col.accessor("last_status", {
      header: "Status",
      cell: (c) => <StatusPill status={c.getValue()} />,
    }),
    col.accessor("last_run_at", {
      header: "Last Run",
      cell: (c) => {
        const v = c.getValue();
        return (
          <span className="text-[var(--color-muted-foreground)]" title={v ? new Date(toMs(v)).toLocaleString() : ""}>
            {v ? relTime(v) : "—"}
          </span>
        );
      },
    }),
    col.accessor("updated_at", {
      header: "Updated",
      cell: (c) => {
        const v = c.getValue();
        return (
          <span className="text-[var(--color-muted-foreground)]" title={v ? new Date(v).toLocaleString() : ""}>
            {v ? relTime(v) : "—"}
          </span>
        );
      },
    }),

    col.display({
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      cell: (c) => {
        const inst = c.row.original;
        const stop = (e: React.MouseEvent) => e.stopPropagation();
        return (
          <div className="inline-flex gap-1" onClick={stop}>
            <Button size="sm" variant="outline" onClick={() => openEdit(inst)} title="Edit values & re-save"><Pencil className="h-3.5 w-3.5" /></Button>
            <Button size="sm" variant="outline" onClick={() => runNow(inst)} title="Run this instance"><Play className="h-3.5 w-3.5" /></Button>
            <Button size="sm" variant="outline" onClick={() => openDuplicate(inst)} title="Duplicate"><CopyIcon className="h-3.5 w-3.5" /></Button>
            <Button size="sm" variant="outline" onClick={() => setConfirmDel(inst)} title="Delete"><Trash2 className="h-3.5 w-3.5" /></Button>
          </div>
        );
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], []);

  const [pageIndex, setPageIndex] = useState(0);
  const pageSize = 15;

  useEffect(() => { setPageIndex(0); }, [filterText]);

  const table = useReactTable({
    data: filteredRows,
    columns,
    state: { sorting, pagination: { pageIndex, pageSize } },
    onSortingChange: setSorting,
    onPaginationChange: (updater) => {
      const next = typeof updater === "function"
        ? updater({ pageIndex, pageSize })
        : updater;
      setPageIndex(next.pageIndex);
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  const totalRows = filteredRows.length;
  const pageCount = table.getPageCount() || 1;
  const currentPage = Math.min(pageIndex, Math.max(pageCount - 1, 0));
  const rangeStart = totalRows === 0 ? 0 : currentPage * pageSize + 1;
  const rangeEnd = Math.min((currentPage + 1) * pageSize, totalRows);

  return (
    <div className="space-y-4">
      <div className="text-sm text-[var(--color-muted-foreground)]">
        Every <span className="font-medium text-[var(--color-foreground)]">Save to repo</span> creates an instance here.
        Click a row to open the job detail, or use the actions to edit, run, duplicate, or delete.
      </div>


      {!listQ.isLoading && filteredRows.length === 0 && (
        <Card>
          <CardContent className="p-8 text-center text-sm text-[var(--color-muted-foreground)] flex flex-col items-center gap-2">
            <PackageOpen className="h-6 w-6" />
            {filterText.trim()
              ? "No instances match your search."
              : "No saved template instances yet. Pick a template from Catalog, fill it in, and click Save to repo."}
          </CardContent>
        </Card>
      )}

      {filteredRows.length > 0 && (
        <Card>
          <CardContent className="p-0 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wider text-[var(--color-muted-foreground)] border-b border-[color-mix(in_oklab,var(--color-border)_40%,transparent)]">
                {table.getHeaderGroups().map(hg => (
                  <tr key={hg.id}>
                    {hg.headers.map(h => (
                      <th key={h.id} className="text-left px-4 py-2.5 font-medium">
                        {h.isPlaceholder ? null : h.column.getCanSort() ? (
                          <button
                            type="button"
                            onClick={h.column.getToggleSortingHandler()}
                            className="inline-flex items-center gap-1 hover:text-[var(--color-foreground)]"
                          >
                            {flexRender(h.column.columnDef.header, h.getContext())}
                            <ArrowUpDown className="h-3 w-3 opacity-50" />
                          </button>
                        ) : (
                          flexRender(h.column.columnDef.header, h.getContext())
                        )}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map(row => (
                  <tr
                    key={row.id}
                    className="border-b border-[color-mix(in_oklab,var(--color-border)_40%,transparent)] last:border-0 hover:bg-[var(--color-muted)]/40 cursor-pointer transition-colors"
                    onClick={() => navigate({ to: "/infrastructure/job", search: (row.original.id ? { id: row.original.id } : { path: row.original.path }) as any })}
                  >
                    {row.getVisibleCells().map(cell => (
                      <td key={cell.id} className="px-4 py-2.5 align-middle">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
          {totalRows > 0 && (
            <div className="flex items-center justify-between gap-3 px-4 py-2.5 border-t border-[color-mix(in_oklab,var(--color-border)_40%,transparent)] text-xs text-[var(--color-muted-foreground)]">
              <div>
                Showing <span className="font-medium text-[var(--color-foreground)]">{rangeStart}</span>–
                <span className="font-medium text-[var(--color-foreground)]">{rangeEnd}</span> of{" "}
                <span className="font-medium text-[var(--color-foreground)]">{totalRows}</span>
              </div>
              <div className="flex items-center gap-2">
                <span>Page {currentPage + 1} of {pageCount}</span>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => table.previousPage()}
                  disabled={!table.getCanPreviousPage()}
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                  Previous
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => table.nextPage()}
                  disabled={!table.getCanNextPage()}
                >
                  Next
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          )}
        </Card>
      )}


      {open && (
        <TemplateDialog
          templateId={open.templateId}
          instancePath={open.instancePath}
          initialValues={open.initialValues}
          initialTargets={open.initialTargets}
          initialEnvironment={open.initialEnvironment}
          initialFilename={open.initialFilename}
          initialYaml={open.initialYaml}

          onClose={() => { setOpen(null); qc.invalidateQueries({ queryKey: ["template-instances"] }); }}
          onQueued={(id) => onRunLog?.(id)}
        />
      )}

      <ConfirmDialog
        open={!!confirmDel}
        variant="destructive"
        title="Delete template job?"
        description={
          confirmDel ? (
            <div className="space-y-2">
              <div>This permanently deletes the template instance and its generated playbook. This action cannot be undone.</div>
              <div className="font-mono text-xs bg-[var(--color-muted)] px-2 py-1.5 rounded border border-[var(--color-border)] break-all">
                {confirmDel.path}
              </div>
            </div>
          ) : null
        }
        confirmLabel="Delete"
        busy={deleting}
        onConfirm={() => confirmDel && doDelete(confirmDel)}
        onCancel={() => !deleting && setConfirmDel(null)}
      />
    </div>
  );
}


