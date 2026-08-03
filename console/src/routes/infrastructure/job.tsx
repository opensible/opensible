import { createFileRoute, Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import {
  ArrowLeft, Play, Pencil, Copy as CopyIcon, Trash2, RefreshCw, Clock, FileCode,
  ChevronDown, ChevronUp, Maximize2, Minimize2,
} from "lucide-react";
import { toast } from "sonner";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge, statusToVariant } from "@/components/ui/badge";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { TemplateDialog } from "@/components/infrastructure/TemplateDialog";
import { RunLogDialog } from "@/components/infrastructure/RunLogDialog";
import { YamlEditor } from "@/components/ui/yaml-editor";
import { WorkerSelect } from "@/components/infrastructure/WorkerSelect";
import { PlaybookStageGraph } from "@/components/infrastructure/PlaybookStageGraph";

import { api } from "@/lib/api";

type Search = { id?: string; path?: string };

export const Route = createFileRoute("/infrastructure/job")({
  component: JobDetail,
  validateSearch: (s: Record<string, unknown>): Search => ({
    id: typeof s.id === "string" ? s.id : undefined,
    path: typeof s.path === "string" ? s.path : undefined,
  }),
});

type InstanceDetail = {
  template_id: string;
  environment: string;
  filename: string;
  values: Record<string, unknown>;
  targets: { groups?: string[]; hosts?: string[] };
  path?: string;
  id?: string;
  playbook_id?: string;
};

type Execution = {
  id?: string;
  execution_id?: string;
  status?: string;
  started_at?: string | number;
  finished_at?: string | number;
  startedAt?: string | number;
  finishedAt?: string | number;
  queuedAt?: string | number;
  createdAt?: string | number;
  exit_code?: number;
  exitCode?: number;
  returnCode?: number;
  play_name?: string;
  playName?: string;
  playbook_name?: string;
  stage?: string;
  runParams?: { stage?: string };
  triggeredBy?: string;
  triggered_by?: string;
  triggeredByUserId?: string;
};

function toMs(v: unknown): number {
  if (v == null || v === "") return 0;
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isNaN(n) && n > 0) return n < 1e12 ? n * 1000 : n;
  if (typeof v === "string") {
    const d = new Date(v).getTime();
    if (!Number.isNaN(d)) return d;
  }
  return 0;
}

function fmtTs(v: unknown): string {
  const ms = toMs(v);
  if (!ms) return "—";
  return new Date(ms).toLocaleString();
}

function fmtDuration(startV: unknown, endV: unknown): string {
  const start = toMs(startV);
  const end = toMs(endV);
  if (!start || !end || end < start) return "—";
  let s = Math.round((end - start) / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  s = s % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return mm ? `${h}h ${mm}m` : `${h}h`;
}

function fmtAge(v: unknown): string {
  const ms = toMs(v);
  if (!ms) return "—";
  const diff = Math.max(0, Date.now() - ms);
  const s = Math.round(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(ms).toLocaleDateString();
}


type Stage = "init" | "validate" | "plan" | "apply";


function stageFromExec(e: Execution): Stage | null {
  const direct = (e.stage || e.runParams?.stage || "").toLowerCase();
  if (direct === "init" || direct === "validate" || direct === "plan" || direct === "apply") return direct as Stage;
  const s = (e.play_name || e.playName || "").toLowerCase();
  const m = s.match(/^\s*\[(init|validate|plan|apply)\]/);
  return (m ? (m[1] as Stage) : null);
}


function playbookIdFromFilename(name?: string): string | undefined {
  if (!name) return undefined;
  return name.replace(/\.ya?ml$/i, "");
}

const SECRET_KEY_RE = /(pass(word)?|secret|token|api[_-]?key|priv(ate)?[_-]?key|credential|ssh[_-]?key|auth)/i;
function maskSecrets(v: any): any {
  if (v == null) return v;
  if (Array.isArray(v)) return v.map(maskSecrets);
  if (typeof v === "object") {
    const out: any = {};
    for (const [k, val] of Object.entries(v)) {
      if (SECRET_KEY_RE.test(k) && val != null && val !== "") {
        out[k] = "••••••••";
      } else {
        out[k] = maskSecrets(val);
      }
    }
    return out;
  }
  return v;
}

function JobDetail() {
  const { id, path } = useSearch({ from: Route.id }) as Search;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [openEdit, setOpenEdit] = useState<null | "edit" | "duplicate">(null);
  const [runLogId, setRunLogId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [workerId, setWorkerId] = useState<string>("");
  const [playbookCollapsed, setPlaybookCollapsed] = useState(false);
  const [playbookExpanded, setPlaybookExpanded] = useState(false);

  const detailQ = useQuery({
    queryKey: ["template-instance", id || path],
    queryFn: () => {
      const qs = id
        ? `id=${encodeURIComponent(id)}`
        : `path=${encodeURIComponent(path!)}`;
      return api<InstanceDetail>("GET", `/api/templates/instances/detail?${qs}`);
    },
    enabled: !!(id || path),
  });
  const inst = detailQ.data;
  const pbId = inst?.playbook_id || playbookIdFromFilename(inst?.filename);

  const yamlQ = useQuery({
    queryKey: ["playbook-yaml", pbId],
    queryFn: () => api<{ playbook?: { content?: string; raw?: string; yaml?: string } } | any>(
      "GET", `/api/projects/_current/playbooks/${encodeURIComponent(pbId!)}`
    ),
    enabled: !!pbId,
  });

  const runsQ = useQuery({
    queryKey: ["job-executions", pbId],
    queryFn: () => api<{ executions?: Execution[] }>(
      "GET", `/api/executions?playbook_id=${encodeURIComponent(pbId!)}&limit=50`
    ),
    enabled: !!pbId,
    refetchInterval: 5000,
  });
  const runs = runsQ.data?.executions || [];

  const deleteMut = useMutation({
    mutationFn: () => {
      const target = inst?.path || path;
      return api<{ ok?: boolean; removed?: string[] }>(
        "DELETE", `/api/templates/instances?path=${encodeURIComponent(target!)}`,
      );
    },
    onSuccess: async (res) => {
      const removed = res?.removed?.length ? ` (${res.removed.join(", ")})` : "";
      toast.success(`Deleted${removed}`);
      await qc.refetchQueries({ queryKey: ["template-instances"] });
      navigate({ to: "/infrastructure/templates" });
    },
    onError: (e: any) => toast.error(`Delete failed: ${e?.message || "unknown error"}`),
  });

  const [pendingStage, setPendingStage] = useState<Stage | null>(null);

  const runStage = async (stage: Stage) => {
    if (!pbId || !inst) return;
    setPendingStage(stage);
    try {
      const res = await api<{ executionId?: string; execution_id?: string }>(
        "POST", `/api/projects/_current/playbooks/${encodeURIComponent(pbId)}/run`,
        {
          inventory_files: ["inventory.yml"],
          ansible_config: "ansible.cfg",
          play_name: `[${stage}] ${inst.filename}`,
          become: true,
          strategy: "linear",
          stage,
          check_mode: stage === "plan",
          worker_id: workerId || undefined,
        },
      );
      const id = res.executionId || res.execution_id;
      if (id) { toast.success(`${stage.toUpperCase()} queued (${String(id).slice(0,8)})`); setRunLogId(String(id)); }
      else toast.success(`${stage} queued`);
      runsQ.refetch();
    } catch (e) { toast.error((e as Error).message); }
    finally { setPendingStage(null); }
  };


  const yamlText: string =
    yamlQ.data?.playbook?._yaml_raw ||
    yamlQ.data?.playbook?.content ||
    yamlQ.data?.playbook?.raw ||
    yamlQ.data?.playbook?.yaml ||
    (typeof yamlQ.data?.content === "string" ? yamlQ.data.content : "") ||
    "";

  if (!id && !path) {
    return (
      <div className="space-y-4">
        <Breadcrumbs items={[{ label: "Infrastructure" }, { label: "Job" }]} />
        <Card><CardContent className="p-8 text-center text-[var(--color-destructive)]">Missing job id</CardContent></Card>
      </div>
    );
  }

  if (detailQ.error) {
    return (
      <div className="space-y-4">
        <Breadcrumbs items={[{ label: "Infrastructure" }, { label: "Job" }]} />
        <Card><CardContent className="p-8 text-center text-[var(--color-destructive)]">{(detailQ.error as any)?.message || "Failed to load job"}</CardContent></Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[
        { label: "Infrastructure" },
        { label: "Stack Deployment & Templates", to: "/infrastructure/templates" },
        { label: inst?.filename || "Job" },
      ]} />

      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold truncate">{inst?.filename || "Job"}</h1>
          {inst && (
            <p className="text-xs text-[var(--color-muted-foreground)] mt-1 flex items-center gap-2 flex-wrap">
              {inst.filename && (
                <span className="font-mono break-all">playbooks/{inst.filename}</span>
              )}
              <Badge variant="default" className="text-[10px]">{inst.template_id}</Badge>
              <Badge variant={inst.environment && inst.environment !== "default" ? "success" : "default"} className="text-[10px]">{inst.environment || "default"}</Badge>
            </p>
          )}
        </div>
        <div className="flex flex-wrap justify-end items-center gap-2 shrink-0">
          <WorkerSelect value={workerId} onChange={setWorkerId} disabled={pendingStage !== null} />
          <Button variant="outline" size="sm" asChild>
            <Link to="/infrastructure/templates"><ArrowLeft className="h-4 w-4" /> Back</Link>
          </Button>
          <Button variant="outline" size="sm" onClick={() => setOpenEdit("edit")} disabled={!inst}>
            <Pencil className="h-4 w-4" /> Edit
          </Button>
          <Button variant="outline" size="sm" onClick={() => setOpenEdit("duplicate")} disabled={!inst}>
            <CopyIcon className="h-4 w-4" /> Duplicate
          </Button>
          <Button size="sm" onClick={() => runStage("apply")} disabled={!inst || pendingStage !== null}>
            <Play className="h-4 w-4" /> Run
          </Button>
          <Button variant="destructive" size="sm" onClick={() => setConfirmDelete(true)} disabled={deleteMut.isPending}>
            <Trash2 className="h-4 w-4" /> Delete
          </Button>
        </div>
      </div>


      {(() => {
        const latest = runs[0];
        const latestId = latest ? (latest.id || latest.execution_id) : undefined;
        return (
          <PlaybookStageGraph
            yamlText={yamlText}
            executionId={latestId ? String(latestId) : undefined}
            status={latest?.status}
          />
        );
      })()}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2"><FileCode className="h-4 w-4" /> Rendered playbook</CardTitle>
          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPlaybookExpanded((v) => !v)}
              disabled={playbookCollapsed}
              title={playbookExpanded ? "Shrink playbook" : "Expand playbook"}
            >
              {playbookExpanded ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPlaybookCollapsed((v) => !v)}
              title={playbookCollapsed ? "Show playbook" : "Hide playbook"}
            >
              {playbookCollapsed ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
            </Button>
          </div>
        </CardHeader>
        {!playbookCollapsed && (
          <CardContent>
            <YamlEditor value={yamlText || "# (no playbook found)"} readOnly height={playbookExpanded ? 800 : 400} />
          </CardContent>
        )}
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Values</CardTitle></CardHeader>
        <CardContent>
          <YamlEditor
            value={JSON.stringify({ values: maskSecrets(inst?.values), targets: inst?.targets }, null, 2)}
            readOnly
            height={300}
          />
        </CardContent>
      </Card>


      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2"><Clock className="h-4 w-4" /> Run history</CardTitle>
          <Button variant="outline" size="sm" onClick={() => runsQ.refetch()}>
            <RefreshCw className="h-4 w-4 mr-1.5" /> Refresh
          </Button>
        </CardHeader>
        <CardContent>
          {runsQ.isLoading ? (
            <div className="text-sm text-[var(--color-muted-foreground)] py-3">Loading…</div>
          ) : runs.length === 0 ? (
            <div className="text-sm text-[var(--color-muted-foreground)] py-6 text-center">
              No runs yet. Click <span className="font-medium text-[var(--color-foreground)]">Run</span> to launch this job.
            </div>
          ) : (
            <div className="border border-[var(--color-border)] rounded-md overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-[var(--color-accent)]/40">
                  <tr className="border-b border-[var(--color-border)]">
                    <th className="text-left px-3 py-2 font-medium">Execution</th>
                    <th className="text-left px-3 py-2 font-medium">Stage</th>
                    <th className="text-left px-3 py-2 font-medium">Status</th>
                    <th className="text-left px-3 py-2 font-medium">Triggered by</th>
                    <th className="text-left px-3 py-2 font-medium">Started</th>
                    <th className="text-left px-3 py-2 font-medium">Finished</th>
                    <th className="text-left px-3 py-2 font-medium">Duration</th>
                    <th className="text-left px-3 py-2 font-medium">Age</th>
                    <th className="text-left px-3 py-2 font-medium">Exit</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => {
                    const id = r.id || r.execution_id || "";
                    const st = stageFromExec(r);
                    const startV = r.started_at ?? r.startedAt ?? r.queuedAt ?? r.createdAt;
                    const endV = r.finished_at ?? r.finishedAt;
                    const who = r.triggeredBy || r.triggered_by || "";
                    return (
                      <tr
                        key={id}
                        className="border-b border-[var(--color-border)] last:border-b-0 hover:bg-[var(--color-accent)]/40 cursor-pointer"
                        onClick={() => id && setRunLogId(String(id))}
                      >
                        <td className="px-3 py-2 font-mono text-[var(--color-primary)]">{id.slice(0, 8)}</td>
                        <td className="px-3 py-2">{st ? <Badge variant="default" className="text-[10px] uppercase">{st}</Badge> : <span className="text-[var(--color-muted-foreground)]">—</span>}</td>
                        <td className="px-3 py-2"><Badge variant={statusToVariant(r.status || "")} className="text-[10px]">{r.status || "—"}</Badge></td>
                        <td className="px-3 py-2 text-[var(--color-foreground)]">{who ? <span className="font-medium">{who}</span> : <span className="text-[var(--color-muted-foreground)]">—</span>}</td>
                        <td className="px-3 py-2 text-[var(--color-muted-foreground)]">{fmtTs(startV)}</td>
                        <td className="px-3 py-2 text-[var(--color-muted-foreground)]">{fmtTs(endV)}</td>
                        <td className="px-3 py-2 text-[var(--color-muted-foreground)] font-mono">{fmtDuration(startV, endV)}</td>
                        <td className="px-3 py-2 text-[var(--color-muted-foreground)]" title={fmtTs(startV)}>{fmtAge(startV)}</td>
                        <td className="px-3 py-2 font-mono text-[var(--color-muted-foreground)]">{r.exit_code ?? r.exitCode ?? r.returnCode ?? "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>


      {openEdit && inst && (
        <TemplateDialog
          templateId={inst.template_id}
          instancePath={openEdit === "edit" ? (inst.path || path) : undefined}
          initialValues={inst.values}
          initialTargets={inst.targets}
          initialEnvironment={inst.environment}
          initialFilename={openEdit === "edit" ? inst.filename : undefined}
          onClose={() => { setOpenEdit(null); detailQ.refetch(); yamlQ.refetch(); }}
          onQueued={(id) => setRunLogId(id)}
        />
      )}

      {runLogId && (
        <RunLogDialog executionId={runLogId} live onClose={() => setRunLogId(null)} />
      )}

      <ConfirmDialog
        open={confirmDelete}
        title={`Delete job '${inst?.filename || ""}'?`}
        description="This removes the saved instance and its rendered playbook. Existing run history is preserved."
        confirmLabel="Delete job"
        variant="destructive"
        busy={deleteMut.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => { setConfirmDelete(false); deleteMut.mutate(); }}
      />
    </div>
  );
}
