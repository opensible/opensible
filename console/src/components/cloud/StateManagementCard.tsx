/**
 * State management panel for a single Cloud stack.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Database, Lock, LockOpen, History, RotateCcw, Download, Save, Camera, AlertTriangle, Server,
} from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, getToken } from "@/lib/api";

type Lock = {
  id: string;
  who: string;
  operation: string;
  run_id?: string | null;
  note?: string;
  created_at: string;
  held_seconds?: number;
};

type Version = {
  id: string;
  created_at: string;
  actor: string;
  reason: string;
  run_id?: string | null;
  size_bytes: number;
  serial?: number | null;
  lineage?: string | null;
  resource_count: number;
  tofu_version?: string | null;
};

type BackendCfg = {
  backend_type: string;
  configured: boolean;
  placeholder: boolean;
  values: Record<string, string>;
};

type Overview = {
  state_present: boolean;
  state_source?: string | null;
  serial?: number | null;
  lineage?: string | null;
  resource_count: number;
  tofu_version?: string | null;
  lock: Lock | null;
  versions: Version[];
  version_count: number;
  backend: BackendCfg;
};

type AuditEntry = {
  at: string;
  event: string;
  actor: string;
  [k: string]: unknown;
};

const BACKEND_FIELDS: { key: string; label: string; placeholder: string }[] = [
  { key: "bucket", label: "Bucket", placeholder: "my-tfstate-bucket" },
  { key: "key", label: "Key / path", placeholder: "cloud-provisioning/prod.tfstate" },
  { key: "region", label: "Region", placeholder: "eu-central-1" },
  { key: "endpoint", label: "Endpoint (S3-compatible)", placeholder: "https://obs.example.com" },
  { key: "profile", label: "Profile", placeholder: "default" },
  { key: "prefix", label: "Prefix (GCS)", placeholder: "cloud-provisioning/prod" },
];

function humanDuration(seconds?: number) {
  if (!seconds || seconds < 0) return "just now";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function bytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function StateManagementCard({ stackId }: { stackId: string }) {
  const qc = useQueryClient();
  const base = `/api/cloud/stacks/${encodeURIComponent(stackId)}/state`;
  const key = ["cloud", "state", stackId];

  const [rollbackTarget, setRollbackTarget] = useState<Version | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [backendDraft, setBackendDraft] = useState<Record<string, string> | null>(null);
  const [showAudit, setShowAudit] = useState(false);

  const overview = useQuery({
    queryKey: key,
    queryFn: () => api<Overview>("GET", `${base}/overview`),
    refetchInterval: 15_000,
  });

  const audit = useQuery({
    queryKey: [...key, "audit"],
    queryFn: () => api<{ entries: AuditEntry[] }>("GET", `${base}/audit?limit=50`),
    enabled: showAudit,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: key });
  };

  const snapshot = useMutation({
    mutationFn: () => api("POST", `${base}/versions`),
    onSuccess: () => { toast.success("State snapshot created"); invalidate(); },
    onError: (e: Error) => toast.error(e.message),
  });

  const unlock = useMutation({
    mutationFn: () => api("DELETE", `${base}/lock?force=true&reason=manual+unlock`),
    onSuccess: () => { toast.success("State lock released"); invalidate(); },
    onError: (e: Error) => toast.error(e.message),
  });

  const lock = useMutation({
    mutationFn: () => api("POST", `${base}/lock`, { operation: "manual", note: "Locked from the console" }),
    onSuccess: () => { toast.success("State locked"); invalidate(); },
    onError: (e: Error) => toast.error(e.message),
  });

  const rollback = useMutation({
    mutationFn: (versionId: string) =>
      api<{ warning?: string }>("POST", `${base}/versions/${versionId}/rollback`, { confirm: stackId }),
    onSuccess: (res) => {
      toast.success(res?.warning || "State restored");
      setRollbackTarget(null);
      setConfirmText("");
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const saveBackend = useMutation({
    mutationFn: (values: Record<string, string>) =>
      api<{ message?: string }>("PUT", `${base}/backend`, { values }),
    onSuccess: (res) => {
      toast.success(res?.message || "backend.hcl updated");
      setBackendDraft(null);
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const download = async (versionId: string) => {
    const token = getToken();
    const res = await fetch(`${base}/versions/${versionId}?download=1`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) { toast.error("Download failed"); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${stackId}-${versionId}.tfstate.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const data = overview.data;
  const activeLock = data?.lock || null;
  const backend = data?.backend;
  const draft = backendDraft ?? (backend?.values || {});

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Database className="h-4 w-4" /> State management
        </CardTitle>
        <div className="flex items-center gap-2">
          {activeLock ? (
            <Badge variant="warning" className="gap-1">
              <Lock className="h-3 w-3" /> Locked
            </Badge>
          ) : (
            <Badge variant="default" className="gap-1">
              <LockOpen className="h-3 w-3" /> Unlocked
            </Badge>
          )}
          <Button size="sm" variant="outline" disabled={snapshot.isPending} onClick={() => snapshot.mutate()}>
            <Camera className="h-3.5 w-3.5 mr-1.5" /> Snapshot
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-5 text-xs">
        {overview.isLoading && (
          <div className="text-[var(--color-muted-foreground)]">Loading state…</div>
        )}

        {/* Summary ------------------------------------------------------- */}
        {data && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {[
              ["Resources", String(data.resource_count)],
              ["Serial", data.serial != null ? String(data.serial) : "—"],
              ["Versions", `${data.version_count}`],
              ["Source", data.state_source || "none"],
            ].map(([label, value]) => (
              <div key={label} className="rounded-md border border-[var(--color-border)] px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-[var(--color-muted-foreground)]">{label}</div>
                <div className="font-medium mt-0.5 truncate">{value}</div>
              </div>
            ))}
          </div>
        )}

        {/* Lock ---------------------------------------------------------- */}
        <div className="rounded-md border border-[var(--color-border)] p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="font-medium flex items-center gap-1.5">
              <Lock className="h-3.5 w-3.5" /> Lock status
            </div>
            {activeLock ? (
              <Button size="sm" variant="destructive" disabled={unlock.isPending} onClick={() => unlock.mutate()}>
                <LockOpen className="h-3.5 w-3.5 mr-1.5" /> Force unlock
              </Button>
            ) : (
              <Button size="sm" variant="outline" disabled={lock.isPending} onClick={() => lock.mutate()}>
                <Lock className="h-3.5 w-3.5 mr-1.5" /> Lock stack
              </Button>
            )}
          </div>
          {activeLock ? (
            <div className="text-[var(--color-muted-foreground)] space-y-0.5">
              <div>
                Held by <b className="text-[var(--color-foreground)]">{activeLock.who}</b> for{" "}
                <b className="text-[var(--color-foreground)]">{humanDuration(activeLock.held_seconds)}</b> —
                operation <span className="font-mono">{activeLock.operation}</span>
              </div>
              <div>
                Since {new Date(activeLock.created_at).toLocaleString()}
                {activeLock.run_id ? <> · run <span className="font-mono">{activeLock.run_id}</span></> : null}
              </div>
              <div className="flex items-start gap-1.5 pt-1" style={{ color: "var(--color-warning)" }}>
                <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                <span>Force-unlocking while a run is in flight can corrupt state. Only do it for a stalled run.</span>
              </div>
            </div>
          ) : (
            <div className="text-[var(--color-muted-foreground)]">
              No active lock. Apply, destroy and refresh runs take the lock automatically and release it when the run ends.
            </div>
          )}
        </div>

        {/* Versions ------------------------------------------------------ */}
        <div className="rounded-md border border-[var(--color-border)]">
          <div className="px-3 py-2 border-b border-[var(--color-border)] font-medium flex items-center gap-1.5">
            <History className="h-3.5 w-3.5" /> Version history
            <span className="text-[var(--color-muted-foreground)] font-normal">
              — snapshotted automatically before every apply, destroy and refresh
            </span>
          </div>
          {(!data || data.versions.length === 0) ? (
            <div className="px-3 py-3 text-[var(--color-muted-foreground)]">
              No versions yet. Snapshot now, or run an apply to capture the first one.
            </div>
          ) : (
            <ul className="divide-y divide-[var(--color-border)] max-h-64 overflow-auto">
              {data.versions.map((v) => (
                <li key={v.id} className="px-3 py-2 flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-medium truncate">
                      serial {v.serial ?? "—"} · {v.resource_count} resource(s)
                      <span className="ml-2 font-normal text-[var(--color-muted-foreground)]">{v.reason}</span>
                    </div>
                    <div className="text-[11px] text-[var(--color-muted-foreground)] mt-0.5">
                      {new Date(v.created_at).toLocaleString()} · {v.actor} · {bytes(v.size_bytes)}
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <Button size="sm" variant="outline" onClick={() => download(v.id)}>
                      <Download className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => { setRollbackTarget(v); setConfirmText(""); }}>
                      <RotateCcw className="h-3.5 w-3.5 mr-1.5" /> Rollback
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Rollback confirmation ---------------------------------------- */}
        {rollbackTarget && (
          <div
            className="rounded-md border p-3 space-y-2"
            style={{
              borderColor: "var(--color-destructive)",
              background: "color-mix(in srgb, var(--color-destructive) 8%, transparent)",
            }}
          >
            <div className="font-medium flex items-center gap-1.5">
              <AlertTriangle className="h-3.5 w-3.5" /> Roll back to serial {rollbackTarget.serial ?? "—"}?
            </div>
            <div className="text-[var(--color-muted-foreground)]">
              This replaces the current state file only — <b>no cloud resources are changed</b>. Anything created
              after this version becomes untracked until you run a plan and reconcile. The current state is
              snapshotted first, so this is reversible.
            </div>
            <div className="flex items-center gap-2">
              <Input
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder={`Type "${stackId}" to confirm`}
                className="h-8 text-xs"
              />
              <Button
                size="sm"
                variant="destructive"
                disabled={confirmText !== stackId || rollback.isPending}
                onClick={() => rollback.mutate(rollbackTarget.id)}
              >
                Roll back
              </Button>
              <Button size="sm" variant="outline" onClick={() => setRollbackTarget(null)}>Cancel</Button>
            </div>
          </div>
        )}

        {/* Remote backend ------------------------------------------------ */}
        <div className="rounded-md border border-[var(--color-border)] p-3 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="font-medium flex items-center gap-1.5">
              <Server className="h-3.5 w-3.5" /> Remote backend
            </div>
            <Badge variant={backend?.configured ? "success" : "default"}>
              {backend?.backend_type || "local"}{backend?.configured ? " · configured" : " · not configured"}
            </Badge>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {BACKEND_FIELDS.map((f) => (
              <label key={f.key} className="space-y-1">
                <span className="text-[11px] text-[var(--color-muted-foreground)]">{f.label}</span>
                <Input
                  className="h-8 text-xs"
                  value={draft[f.key] ?? ""}
                  placeholder={f.placeholder}
                  onChange={(e) => setBackendDraft({ ...draft, [f.key]: e.target.value })}
                />
              </label>
            ))}
          </div>
          <div className="flex items-center justify-between gap-2">
            <div className="text-[var(--color-muted-foreground)]">
              Writes <span className="font-mono">backend.hcl</span>. Run <b>init</b> afterwards so OpenTofu migrates the state.
            </div>
            <Button
              size="sm"
              disabled={!backendDraft || saveBackend.isPending}
              onClick={() => backendDraft && saveBackend.mutate(backendDraft)}
            >
              <Save className="h-3.5 w-3.5 mr-1.5" /> Save backend
            </Button>
          </div>
        </div>

        {/* Audit --------------------------------------------------------- */}
        <div className="rounded-md border border-[var(--color-border)]">
          <button
            className="w-full px-3 py-2 flex items-center justify-between hover:bg-[var(--color-accent)]"
            onClick={() => setShowAudit((s) => !s)}
          >
            <span className="font-medium flex items-center gap-1.5">
              <History className="h-3.5 w-3.5" /> Audit trail
            </span>
            <span className="text-[var(--color-muted-foreground)]">{showAudit ? "Hide" : "Show"}</span>
          </button>
          {showAudit && (
            <ul className="divide-y divide-[var(--color-border)] max-h-56 overflow-auto border-t border-[var(--color-border)]">
              {(audit.data?.entries || []).map((e, i) => (
                <li key={i} className="px-3 py-2 flex items-center justify-between gap-3">
                  <span className="font-mono">{e.event}</span>
                  <span className="text-[var(--color-muted-foreground)] truncate">
                    {e.actor} · {new Date(e.at).toLocaleString()}
                  </span>
                </li>
              ))}
              {!audit.isLoading && (audit.data?.entries || []).length === 0 && (
                <li className="px-3 py-2 text-[var(--color-muted-foreground)]">No state activity recorded yet.</li>
              )}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
