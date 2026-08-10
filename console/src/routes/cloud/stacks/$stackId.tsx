import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, CheckCircle2, Download, Search, Rocket, Bomb, RefreshCcw, RefreshCw, Clock,
  GitBranch, GitPullRequestArrow, Edit, Trash2, KeyRound, AlertTriangle, Boxes, Github, Radar, Settings, X,
  MoreHorizontal,
} from "lucide-react";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { toast } from "sonner";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge, statusToVariant } from "@/components/ui/badge";
import { VmInventoryDialog } from "@/components/cloud/VmInventoryDialog";
import { RunFlowGraph } from "@/components/cloud/RunFlowGraph";
import { PlanDiff } from "@/components/cloud/PlanDiff";
import { PolicyGateCard } from "@/components/cloud/PolicyGateCard";
import { StateManagementCard } from "@/components/cloud/StateManagementCard";
import { StateStatusStrip, useStateOverview, RestoreStateDialog, type StateVersion } from "@/components/cloud/StateStatusStrip";

import { ProvisioningLogDialog } from "@/routes/cloud/summary";
import { api } from "@/lib/api";
import { qk } from "@/lib/query";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";

const GITHUB_ISSUES_URL = "https://github.com/opensible/opensible/issues/new";


export const Route = createFileRoute("/cloud/stacks/$stackId")({ component: StackDetail });

type DriftInfo = {
  enabled: boolean;
  status: "unknown" | "in_sync" | "drifted" | "checking" | "error" | string;
  last_run_id?: string | null;
  last_checked_at?: number | null;
  returncode?: number | null;
  run_status?: string | null;
};

type StackData = {
  name: string;
  provider?: string;
  path?: string;
  has_secrets?: boolean;
  terraform_tfvars?: string;
  drift?: DriftInfo;
};

type RunResp = {
  run_id?: string;
  log?: string;
  status?: string;
  returncode?: number | null;
  action?: string;
  stack?: string;
};

const ACTIONS: Array<{ id: string; label: string; icon: any; variant: "outline" | "default" | "destructive"; confirm?: boolean }> = [
  { id: "validate", label: "Validate", icon: CheckCircle2, variant: "outline" },
  { id: "init", label: "Init", icon: Download, variant: "outline" },
  { id: "plan", label: "Plan", icon: Search, variant: "default" },
  { id: "apply", label: "Apply", icon: Rocket, variant: "default", confirm: true },
  { id: "refresh", label: "Refresh State", icon: RefreshCcw, variant: "outline" },
  { id: "destroy", label: "Destroy", icon: Bomb, variant: "destructive", confirm: true },
];

type StateInfo = {
  state_present: boolean;
  resource_count: number;
  resources: string[];
  message?: string;
};

function fmtDurSec(startSec?: number, endSec?: number): string {
  if (!startSec) return "—";
  const end = endSec ?? Math.floor(Date.now() / 1000);
  const d = Math.max(0, end - startSec);
  const h = Math.floor(d / 3600);
  const m = Math.floor((d % 3600) / 60);
  const s = d % 60;
  if (h) return `${h}h ${m}m ${s}s`;
  return m ? `${m}m ${s}s` : `${s}s`;
}
function fmtRelSec(startSec?: number): string {
  if (!startSec) return "—";
  const diff = Math.floor(Date.now() / 1000 - startSec);
  if (diff < 5) return "just now";
  if (diff < 60) return diff + "s ago";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  return Math.floor(diff / 86400) + "d ago";
}

function StackDetail() {
  const { stackId } = Route.useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [log, setLog] = useState("");
  const [running, setRunning] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<{ status?: string; runId?: string; returncode?: number | null }>({});
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showInventory, setShowInventory] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    if (!settingsOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [settingsOpen]);


  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [restoreVersion, setRestoreVersion] = useState<StateVersion | null>(null);
  const stateOverviewQ = useStateOverview(stackId);
  const versionByRun = new Map<string, StateVersion>();
  for (const v of stateOverviewQ.data?.versions || []) {
    const ids = [v.run_id, ...(v.run_ids || [])].filter(Boolean) as string[];
    for (const id of ids) {
      if (!versionByRun.has(String(id))) versionByRun.set(String(id), v);
    }
  }

  const [flowLog, setFlowLog] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  

  const { data: stack, error } = useQuery({
    queryKey: qk.stack(stackId),
    queryFn: () => api<StackData>("GET", `/api/cloud/stacks/${encodeURIComponent(stackId)}`),
  });

  const { data: stateInfo, refetch: refetchState } = useQuery({
    queryKey: ["cloud", "stack-state", stackId],
    queryFn: () => api<StateInfo>("GET", `/api/cloud/stacks/${encodeURIComponent(stackId)}/state`),
    refetchInterval: 15000,
  });

  const driftQ = useQuery({
    queryKey: ["cloud", "stack-drift", stackId],
    queryFn: () => api<DriftInfo>("GET", `/api/cloud/stacks/${encodeURIComponent(stackId)}/drift`),
    refetchInterval: 20000,
  });
  const drift = driftQ.data;
  const driftEnabled = !!drift?.enabled;

  const driftToggleMut = useMutation({
    mutationFn: (enabled: boolean) =>
      api<DriftInfo>("PUT", `/api/cloud/stacks/${encodeURIComponent(stackId)}/drift`, { enabled }),
    onSuccess: (_d, enabled) => {
      toast.success(enabled ? "Drift detection enabled" : "Drift detection disabled");
      driftQ.refetch();
      queryClient.invalidateQueries({ queryKey: qk.stacks });
      queryClient.invalidateQueries({ queryKey: qk.stack(stackId) });
    },
    onError: (e: any) => toast.error(e?.message || "Failed to update drift detection"),
  });

  const runsQ = useQuery({
    queryKey: ["cloud", "stack-runs", stackId],
    queryFn: () => api<{ runs: Array<any> }>("GET", `/api/cloud/runs`),
    refetchInterval: 4000,
  });
  const stackRuns = (runsQ.data?.runs || []).filter((r) => r.stack === stackId);

  const hydratedRef = useRef(false);
  const latestRun = stackRuns[0];
  useEffect(() => {
    if (hydratedRef.current) return;
    if (running || !latestRun?.run_id) return;
    hydratedRef.current = true;
    (async () => {
      try {
        const run = await api<RunResp>(
          "GET",
          `/api/cloud/stacks/${encodeURIComponent(stackId)}/runs/${encodeURIComponent(latestRun.run_id)}`
        );
        setFlowLog(run.log ?? "");
        setLastAction(latestRun.action || "plan");
        setRunStatus({ status: run.status ?? latestRun.status, runId: latestRun.run_id, returncode: run.returncode });
      } catch {
        hydratedRef.current = false;
      }
    })();
  }, [latestRun?.run_id, running, stackId]);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  function stopPoll() { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } }

  async function runAction(actionId: string, confirmNeeded: boolean) {
    if (confirmNeeded) { setPendingAction(actionId); return; }
    await doRunAction(actionId);
  }

  async function doRunAction(actionId: string) {
    stopPoll();
    setRunning(actionId);
    setLastAction(actionId);
    setFlowLog(`→ tofu ${actionId} starting…\n`);
    setRunStatus({ status: "running" });
    try {
      const r = await api<{ run_id: string }>("POST", `/api/cloud/stacks/${encodeURIComponent(stackId)}/actions`, { action: actionId });
      const runId = r.run_id;
      setRunStatus({ status: "running", runId });
      setSelectedRunId(runId);
      const tick = async () => {
        try {
          const run = await api<RunResp>("GET", `/api/cloud/stacks/${encodeURIComponent(stackId)}/runs/${encodeURIComponent(runId)}`);
          setFlowLog(run.log ?? "");
          setRunStatus({ status: run.status, runId, returncode: run.returncode });
          if (run.status && run.status !== "running" && run.status !== "queued") {
            stopPoll();
            setRunning(null);
            if (run.status === "succeeded") toast.success(`${actionId} succeeded`);
            else toast.error(`${actionId} ${run.status}`);
            queryClient.invalidateQueries({ queryKey: qk.stack(stackId) });
            queryClient.invalidateQueries({ queryKey: qk.runs });
            refetchState();
            driftQ.refetch();
          }
        } catch { /* keep polling */ }
      };
      tick();
      pollRef.current = setInterval(tick, 1500);
    } catch (e: any) {
      toast.error(e?.message || "Action failed");
      setRunning(null);
      setRunStatus({});
    }
  }

  const [gitBusy, setGitBusy] = useState<"pull" | "push" | null>(null);
  const [gitStatus, setGitStatusMsg] = useState<{ msg: string; tone: "muted" | "warn" | "ok" | "err" }>({ msg: "", tone: "muted" });

  // Initial: show last sync time
  useEffect(() => {
    let cancelled = false;
    api<{ state?: any }>("GET", `/api/projects/_current/sources/stacks/sync/state`)
      .then(r => {
        if (cancelled) return;
        const s = r?.state || {};
        const t = Math.max(s.lastPullAt || 0, s.lastPushAt || 0);
        if (t) setGitStatusMsg({ msg: `Last Git sync: ${new Date(t * 1000).toLocaleString()}`, tone: "muted" });
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  function pollGit(direction: "pull" | "push"): Promise<void> {
    const statusKey = direction === "pull" ? "lastPullStatus" : "lastPushStatus";
    const errKey = direction === "pull" ? "lastPullError" : "lastPushError";
    const started = Date.now();
    return new Promise((resolve, reject) => {
      const tick = async () => {
        try {
          const r = await api<{ state?: any }>("GET", `/api/projects/_current/sources/stacks/sync/state`);
          const st = r?.state || {};
          if (st[statusKey] === "ok") return resolve();
          if (st[statusKey] === "failed") return reject(new Error(st[errKey] || "sync failed"));
          if (Date.now() - started > 120000) return reject(new Error("timeout waiting for sync"));
          setTimeout(tick, 1500);
        } catch { setTimeout(tick, 2000); }
      };
      setTimeout(tick, 1000);
    });
  }

  async function gitSync(direction: "pull" | "push") {
    if (gitBusy) return;
    setGitBusy(direction);
    setGitStatusMsg({ msg: `${direction === "pull" ? "Pulling" : "Pushing"} stacks from Git…`, tone: "warn" });
    try {
      await api("POST", `/api/projects/_current/sources/stacks/sync`, { direction });
      await pollGit(direction);
      setGitStatusMsg({ msg: `${direction === "pull" ? "Pulled" : "Pushed"} at ${new Date().toLocaleTimeString()}`, tone: "ok" });
      toast.success(`Git ${direction} succeeded.`);
      if (direction === "pull") {
        queryClient.invalidateQueries({ queryKey: qk.stack(stackId) });
        queryClient.invalidateQueries({ queryKey: qk.stacks });
      }
    } catch (e: any) {
      const msg = e?.message || `Git ${direction} failed`;
      setGitStatusMsg({ msg: `${direction} failed: ${msg}`, tone: "err" });
      if (/SOURCE_NOT_CONFIGURED|not configured/i.test(msg)) {
        toast.error("Configure a Git remote in Stack Project Settings first.");
      } else toast.error(`Git ${direction} failed: ${msg}`);
    } finally {
      setGitBusy(null);
    }
  }

  const driftBadge = (() => {
    switch (drift?.status) {
      case "in_sync": return { label: "In sync", variant: "success" as const, color: "var(--color-success)" };
      case "drifted": return { label: "Drift detected", variant: "warning" as const, color: "var(--color-warning)" };
      case "checking": return { label: "Checking…", variant: "primary" as const, color: "var(--color-primary)" };
      case "error": return { label: "Check failed", variant: "destructive" as const, color: "var(--color-destructive)" };
      default: return { label: "Not checked", variant: "default" as const, color: "var(--color-muted-foreground)" };
    }
  })();

  const deleteMut = useMutation({
    mutationFn: () => api("DELETE", `/api/cloud/stacks/${encodeURIComponent(stackId)}?force=true`),
    onSuccess: () => {
      toast.success("Deleted");
      queryClient.invalidateQueries({ queryKey: qk.stacks });
      navigate({ to: "/cloud/stacks" });
    },
    onError: (e: any) => toast.error(e?.message || "Delete failed"),
  });

  function deleteStack() { setConfirmDelete(true); }

  if (error) {
    return (
      <div className="space-y-6">
        <Breadcrumbs items={[{ label: `Stack · ${stackId}` }]} />
        <Card><CardContent className="p-8 text-center text-[var(--color-destructive)]">{(error as any)?.message || "Failed to load stack"}</CardContent></Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: `Stack · ${stackId}` }]} />

      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold">{stack?.name || stackId}</h1>
          {stack && (
            <p className="text-xs font-mono text-[var(--color-muted-foreground)] mt-1 flex items-center gap-2 flex-wrap break-all">
              <span>{stack.path}</span>
              {stack.has_secrets ? (
                <span className="inline-flex items-center gap-1 text-[var(--color-success)]"><KeyRound className="h-3 w-3" /> credentials stored</span>
              ) : (
                <span className="inline-flex items-center gap-1 text-[var(--color-warning)]"><AlertTriangle className="h-3 w-3" /> no credentials yet</span>
              )}
            </p>
          )}
        </div>
        <div className="flex flex-wrap justify-end gap-2 shrink-0">
          <Button variant="outline" size="sm" asChild><Link to="/cloud/stacks"><ArrowLeft className="h-4 w-4" /> Back</Link></Button>
          <Button variant="outline" size="sm" onClick={() => gitSync("pull")} disabled={!!gitBusy}>
            <GitBranch className={`h-4 w-4 ${gitBusy === "pull" ? "animate-spin" : ""}`} />
            {gitBusy === "pull" ? "Syncing…" : "Sync from Git"}
          </Button>
          <Button variant="outline" size="sm" onClick={() => gitSync("push")} disabled={!!gitBusy}>
            <GitPullRequestArrow className={`h-4 w-4 ${gitBusy === "push" ? "animate-spin" : ""}`} />
            {gitBusy === "push" ? "Syncing…" : "Sync to Git"}
          </Button>
          <Button variant="outline" size="sm" asChild>
            <Link
              to={
               stack?.provider === "hetzner" ? "/cloud/stacks/new/hetzner"
               : stack?.provider === "cloudflare" ? "/cloud/stacks/new/cloudflare"
               : stack?.provider === "aws" ? "/cloud/stacks/new/aws"
               : stack?.provider === "eks" ? "/cloud/stacks/new/eks"
               : stack?.provider === "gcp" ? "/cloud/stacks/new/gcp"
               : stack?.provider === "gke" ? "/cloud/stacks/new/gke"
               : stack?.provider === "kubernetes" || stack?.provider === "k8s" ? "/cloud/stacks/new/kubernetes"
               : "/cloud/stacks/new/bytedc"
              }
              search={{ edit: stackId } as any}
            ><Edit className="h-4 w-4" /> Edit</Link>
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" aria-label="More actions">
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => setSettingsOpen(true)}>
                <Settings className="h-4 w-4 mr-2" /> Stack settings
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="text-[var(--color-destructive)] focus:text-[var(--color-destructive)] focus:bg-[var(--color-destructive)]/10"
                onSelect={() => deleteStack()}
                disabled={deleteMut.isPending}
              >
                <Trash2 className="h-4 w-4 mr-2" /> Delete stack
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {gitStatus.msg && (
        <div
          className="text-xs"
          style={{
            color:
              gitStatus.tone === "ok" ? "var(--color-success)" :
              gitStatus.tone === "warn" ? "var(--color-warning)" :
              gitStatus.tone === "err" ? "var(--color-destructive)" :
              "var(--color-muted-foreground)",
          }}
        >
          {gitStatus.msg}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        
        {ACTIONS.map(a => (
          <Button key={a.id} variant={a.variant} disabled={!!running} onClick={() => runAction(a.id, !!a.confirm)}>
            <a.icon className="h-4 w-4" /> {a.label}
            {running === a.id && <span className="ml-1 animate-pulse">…</span>}
          </Button>
        ))}
        {driftEnabled && (
          <Button variant="outline" disabled={!!running} onClick={() => runAction("drift", false)}>
            <Radar className="h-4 w-4" /> Check Drift
            {running === "drift" && <span className="ml-1 animate-pulse">…</span>}
          </Button>
        )}
        <Button variant="outline" onClick={() => setShowInventory(true)}>
          <Boxes className="h-4 w-4" /> Inventory Resources
        </Button>

        {runStatus.status && (
          <div className="ml-auto text-xs flex items-center gap-2">
            <Badge variant={statusToVariant(runStatus.status)}>{runStatus.status}</Badge>
            {runStatus.runId && <span className="font-mono text-[var(--color-muted-foreground)]">{runStatus.runId.slice(0, 8)}</span>}
            {runStatus.returncode != null && <span className="text-[var(--color-muted-foreground)]">exit {runStatus.returncode}</span>}
          </div>
        )}
      </div>

      <StateStatusStrip stackId={stackId} onManage={() => setSettingsOpen(true)} />



      <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-muted)]/40 px-3 py-2 flex items-center justify-between gap-3 flex-wrap">
        <div className="text-xs text-[var(--color-muted-foreground)]">
          Missing something or want to improve this provider? OpenSible is open source — your feedback shapes the roadmap.
        </div>
        <Button variant="outline" size="sm" asChild>
          <a href={GITHUB_ISSUES_URL} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5">
            <Github className="h-3.5 w-3.5" />
            Suggest an issue
          </a>
        </Button>
      </div>

      {stateInfo && (
        <div
          className="text-xs rounded-md border px-3 py-2 flex items-center justify-between gap-3"
          style={{
            borderColor: stateInfo.resource_count === 0
              ? "var(--color-warning)" : "var(--color-border)",
            background: stateInfo.resource_count === 0
              ? "color-mix(in srgb, var(--color-warning) 12%, transparent)"
              : "var(--color-muted)",
          }}
        >
          <div className="flex items-center gap-2">
            {stateInfo.resource_count === 0
              ? <AlertTriangle className="h-3.5 w-3.5" style={{ color: "var(--color-warning)" }} />
              : <CheckCircle2 className="h-3.5 w-3.5" style={{ color: "var(--color-success)" }} />}
            <span className="font-medium">
              tfstate: {stateInfo.state_present ? `${stateInfo.resource_count} resource(s) tracked` : "missing"}
            </span>
            {stateInfo.resource_count === 0 && (
              <span className="text-[var(--color-muted-foreground)]">
                — Destroy will report "0 destroyed" because Tofu has nothing in state. Run <b>Refresh State</b> first, or re-import existing resources.
              </span>
            )}
          </div>
        </div>
      )}

      {settingsOpen && createPortal(
      <div className="fixed inset-0 z-[100] flex justify-end bg-black/50" onClick={() => setSettingsOpen(false)}>
      <div
        className="flex h-screen w-full max-w-2xl flex-col bg-[var(--color-card)] border-l border-[var(--color-border)] shadow-2xl animate-in slide-in-from-right duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-[var(--color-border)] bg-[var(--color-card)] shrink-0">
          <div>
            <h3 className="text-base font-semibold flex items-center gap-2">
              <Settings className="h-4 w-4" /> Settings
            </h3>
            <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5 font-mono">{stackId}</p>
          </div>
          <button onClick={() => setSettingsOpen(false)} className="p-1 rounded hover:bg-[var(--color-muted)]" aria-label="Close settings">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain p-4 space-y-4">

      <Card>

        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle className="text-base flex items-center gap-2">
              <Radar className="h-4 w-4" /> Drift detection
              {driftEnabled ? (
                <Badge variant={driftBadge.variant}>{driftBadge.label}</Badge>
              ) : (
                <Badge variant="default">Disabled</Badge>
              )}
            </CardTitle>
            <p className="text-xs text-[var(--color-muted-foreground)] mt-1 max-w-xl">
              Compares the live infrastructure against this stack's recorded state using a read-only
              <code className="mx-1 font-mono">tofu plan -refresh-only</code>
              check. It never modifies infrastructure or writes state. Disabled by default.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={driftEnabled}
            aria-label="Toggle drift detection"
            disabled={driftToggleMut.isPending}
            onClick={() => driftToggleMut.mutate(!driftEnabled)}
            className="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50"
            style={{ background: driftEnabled ? "var(--color-primary)" : "var(--color-muted)" }}
          >
            <span
              className="inline-block h-5 w-5 rounded-full bg-[var(--color-background)] shadow transition-transform"
              style={{ transform: driftEnabled ? "translateX(22px)" : "translateX(2px)" }}
            />
          </button>
        </CardHeader>
        <CardContent>
          {!driftEnabled ? (
            <div className="text-sm text-[var(--color-muted-foreground)]">
              Drift detection is turned off for this stack. Enable it to run drift checks and track whether
              the live infrastructure still matches your code.
            </div>
          ) : (
            <div className="text-sm space-y-2">
              <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
                <span>
                  Status: <b style={{ color: driftBadge.color }}>{driftBadge.label}</b>
                </span>
                <span className="text-[var(--color-muted-foreground)]">
                  Last checked: {drift?.last_checked_at ? fmtRelSec(drift.last_checked_at) : "never"}
                </span>
                {drift?.returncode != null && (
                  <span className="text-[var(--color-muted-foreground)]">exit {drift.returncode}</span>
                )}
              </div>
              <div className="text-xs text-[var(--color-muted-foreground)]">
                {drift?.status === "drifted"
                  ? "The live infrastructure no longer matches state. Run Refresh State to reconcile state, or Apply to push your code back onto the infrastructure."
                  : drift?.status === "in_sync"
                  ? "No drift detected at the last check."
                  : drift?.status === "checking"
                  ? "A drift check is currently running…"
                  : drift?.status === "error"
                  ? "The last drift check failed — open the run log for details."
                  : "No drift check has completed yet. Click Check Drift to run one."}
              </div>
              <Button variant="outline" size="sm" disabled={!!running} onClick={() => runAction("drift", false)}>
                <Radar className="h-4 w-4 mr-1.5" /> Check Drift
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      <PolicyGateCard stackId={stackId} />

      <StateManagementCard stackId={stackId} />

        </div>
      </div>
      </div>,
      document.body
      )}






      {(lastAction || running) && (
        <>
          <RunFlowGraph
            action={running || lastAction || "plan"}
            log={flowLog}
            status={runStatus.status}
          />
          <PlanDiff log={flowLog} action={running || lastAction || undefined} />
        </>
      )}



      <Card>

        <CardHeader><CardTitle className="text-base">terraform.tfvars</CardTitle></CardHeader>
        <CardContent>
          <pre className="rounded-md bg-[var(--color-muted)] font-mono text-xs p-4 overflow-auto max-h-[400px] whitespace-pre">
            {stack?.terraform_tfvars || "(empty)"}
          </pre>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2"><Clock className="h-4 w-4" /> Provisioning history</CardTitle>
          <Button variant="outline" size="sm" onClick={() => runsQ.refetch()}>
            <RefreshCw className="h-4 w-4 mr-1.5" /> Refresh
          </Button>
        </CardHeader>
        <CardContent>
          {runsQ.isLoading ? (
            <div className="text-sm text-[var(--color-muted-foreground)] py-3">Loading…</div>
          ) : stackRuns.length === 0 ? (
            <div className="text-sm text-[var(--color-muted-foreground)] py-6 text-center">
              No runs yet — click an action above to launch <b>tofu</b>.
            </div>
          ) : (
            <div className="border border-[var(--color-border)] rounded-md overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-[var(--color-accent)]/40">
                  <tr className="border-b border-[var(--color-border)]">
                    <th className="text-left px-3 py-2 font-medium">Execution</th>
                    <th className="text-left px-3 py-2 font-medium">Action</th>
                    <th className="text-left px-3 py-2 font-medium">Status</th>
                    <th className="text-left px-3 py-2 font-medium">Triggered by</th>
                    <th className="text-left px-3 py-2 font-medium">Started</th>
                    <th className="text-left px-3 py-2 font-medium">Finished</th>
                    <th className="text-left px-3 py-2 font-medium">Duration</th>
                    <th className="text-left px-3 py-2 font-medium">Age</th>
                    <th className="text-left px-3 py-2 font-medium">Exit</th>
                    <th className="text-left px-3 py-2 font-medium">State</th>
                  </tr>
                </thead>
                <tbody>
                  {stackRuns.map((r) => {
                    const startedSec = r.started_at ? Number(r.started_at) : undefined;
                    const finishedSec = r.finished_at ? Number(r.finished_at) : undefined;
                    const snapshot = versionByRun.get(String(r.run_id));
                    return (
                    <tr
                      key={r.run_id}
                      className="border-b border-[var(--color-border)] last:border-b-0 hover:bg-[var(--color-accent)]/40 cursor-pointer"
                      onClick={() => setSelectedRunId(r.run_id)}
                    >
                      <td className="px-3 py-2 font-mono text-[var(--color-primary)]">{String(r.run_id).slice(0, 8)}</td>
                      <td className="px-3 py-2"><Badge variant="default" className="text-[10px] uppercase">{r.action}</Badge></td>
                      <td className="px-3 py-2"><Badge variant={statusToVariant(r.status || "")} className="text-[10px]">{r.status || "—"}</Badge></td>
                      <td className="px-3 py-2 text-[var(--color-muted-foreground)]">{r.triggered_by || "—"}</td>
                      <td className="px-3 py-2 text-[var(--color-muted-foreground)]">{startedSec ? new Date(startedSec * 1000).toLocaleString() : "—"}</td>
                      <td className="px-3 py-2 text-[var(--color-muted-foreground)]">{finishedSec ? new Date(finishedSec * 1000).toLocaleString() : "—"}</td>
                      <td className="px-3 py-2 whitespace-nowrap text-[var(--color-muted-foreground)]">{fmtDurSec(startedSec, finishedSec)}</td>
                      <td className="px-3 py-2 whitespace-nowrap text-[var(--color-muted-foreground)]">{fmtRelSec(startedSec)}</td>
                      <td className="px-3 py-2 font-mono text-[var(--color-muted-foreground)]">{r.returncode ?? "—"}</td>
                      <td className="px-3 py-2 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                        {snapshot ? (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-6 px-2 text-[10px]"
                            title={`Roll back to the state captured before this ${r.action} run`}
                            onClick={() => setRestoreVersion(snapshot)}
                          >
                            <RefreshCcw className="h-3 w-3 mr-1" /> Rollback to here
                          </Button>
                        ) : (
                          <span
                            className="text-[var(--color-muted-foreground)]"
                            title="No state snapshot for this run — snapshots are captured for apply, destroy and refresh runs only."
                          >
                            —
                          </span>
                        )}
                      </td>

                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {selectedRunId && (
        <ProvisioningLogDialog
          stack={stackId}
          runId={selectedRunId}
          onClose={() => setSelectedRunId(null)}
        />
      )}


      <ConfirmDialog
        open={!!pendingAction}
        title={`Run '${pendingAction}' on '${stackId}'?`}
        description={
          <>
            This will execute <code className="font-mono">tofu {pendingAction}</code> against real infrastructure and may create, modify, or delete cloud resources.
          </>
        }
        confirmLabel={pendingAction === "destroy" ? "Destroy" : "Run"}
        variant={pendingAction === "destroy" ? "destructive" : "default"}
        onCancel={() => setPendingAction(null)}
        onConfirm={() => {
          const a = pendingAction!;
          setPendingAction(null);
          doRunAction(a);
        }}
      />

      <ConfirmDialog
        open={confirmDelete}
        title={`Delete stack '${stackId}'?`}
        description="This removes the env folder but cannot remove already-provisioned cloud resources. You should run Destroy first if you want to tear down infrastructure."
        confirmLabel="Delete stack"
        variant="destructive"
        busy={deleteMut.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => { setConfirmDelete(false); deleteMut.mutate(); }}
      />

      {restoreVersion && (
        <RestoreStateDialog stackId={stackId} version={restoreVersion} onClose={() => setRestoreVersion(null)} />
      )}

      {showInventory && <VmInventoryDialog stackId={stackId} onClose={() => setShowInventory(false)} />}
    </div>
  );
}
