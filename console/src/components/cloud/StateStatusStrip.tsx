/**
 * Compact, always-visible state summary for a Cloud stack.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Database, Lock, LockOpen, RotateCcw, AlertTriangle, Settings } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

export type StateVersion = {
  id: string;
  created_at: string;
  actor: string;
  reason: string;
  run_id?: string | null;
  run_ids?: string[] | null;
  size_bytes: number;
  serial?: number | null;
  resource_count: number;
};

export type StateOverview = {
  state_present: boolean;
  serial?: number | null;
  resource_count: number;
  version_count: number;
  versions: StateVersion[];
  lock: { who: string; operation: string; held_seconds?: number; created_at: string } | null;
  backend: { backend_type: string; configured: boolean };
};

export function stateQueryKey(stackId: string) {
  return ["cloud", "state", stackId];
}

export function useStateOverview(stackId: string) {
  return useQuery({
    queryKey: stateQueryKey(stackId),
    queryFn: () =>
      api<StateOverview>("GET", `/api/cloud/stacks/${encodeURIComponent(stackId)}/state/overview`),
    refetchInterval: 15_000,
  });
}

function relTime(iso?: string) {
  if (!iso) return "—";
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

/** Confirmation dialog for restoring a specific state version. */
export function RestoreStateDialog({
  stackId, version, onClose,
}: { stackId: string; version: StateVersion; onClose: () => void }) {
  const qc = useQueryClient();
  const [confirmText, setConfirmText] = useState("");

  const rollback = useMutation({
    mutationFn: () =>
      api<{ warning?: string }>(
        "POST",
        `/api/cloud/stacks/${encodeURIComponent(stackId)}/state/versions/${version.id}/rollback`,
        { confirm: stackId },
      ),
    onSuccess: (res) => {
      toast.success(res?.warning || "State restored — run a plan to reconcile");
      qc.invalidateQueries({ queryKey: stateQueryKey(stackId) });
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <div className="fixed inset-0 z-[70] bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-[var(--color-background)] border rounded-lg shadow-xl w-full max-w-lg p-4 space-y-3 text-xs"
        style={{ borderColor: "var(--color-destructive)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="font-medium text-sm flex items-center gap-2">
          <AlertTriangle className="h-4 w-4" /> Roll back to serial {version.serial ?? "—"}?
        </div>
        <div className="text-[var(--color-muted-foreground)]">
          Captured {new Date(version.created_at).toLocaleString()} by {version.actor} · {version.resource_count} resource(s) · {version.reason}
        </div>
        <div className="text-[var(--color-muted-foreground)]">
          This replaces the current state file only — <b>no cloud resources are changed</b>. Anything created after
          this version becomes untracked until you run a plan and reconcile. The current state is snapshotted first,
          so this is reversible.
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
            onClick={() => rollback.mutate()}
          >
            Roll back
          </Button>
          <Button size="sm" variant="outline" onClick={onClose}>Cancel</Button>
        </div>
      </div>
    </div>
  );
}

export function StateStatusStrip({
  stackId, onManage,
}: { stackId: string; onManage: () => void }) {
  const q = useStateOverview(stackId);
  const [target, setTarget] = useState<StateVersion | null>(null);
  const data = q.data;
  const latest = data?.versions?.[0];
  const lock = data?.lock || null;

  return (
    <>
      {lock && (
        <div
          className="rounded-lg border px-4 py-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs"
          style={{
            borderColor: "var(--color-warning)",
            background: "color-mix(in srgb, var(--color-warning) 12%, transparent)",
          }}
        >
          <Lock className="h-4 w-4" style={{ color: "var(--color-warning)" }} />
          <span className="font-medium text-sm">State is locked</span>
          <span className="text-[var(--color-muted-foreground)]">
            Held by <b className="text-[var(--color-foreground)]">{lock.who}</b> for{" "}
            <b className="text-[var(--color-foreground)]">{lock.operation}</b> · since{" "}
            {relTime(lock.created_at)}. Apply, destroy and refresh are blocked until it is released.
          </span>
          <Button size="sm" variant="outline" className="ml-auto" onClick={onManage}>
            Force-unlock…
          </Button>
        </div>
      )}

      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
        <div className="flex items-center gap-2 font-medium text-sm">
          <Database className="h-4 w-4" /> State
        </div>

        {lock ? (
          <Badge variant="warning" className="gap-1">
            <Lock className="h-3 w-3" /> Locked by {lock.who} · {lock.operation}
          </Badge>
        ) : (
          <Badge variant="default" className="gap-1">
            <LockOpen className="h-3 w-3" /> Unlocked
          </Badge>
        )}


        <span className="text-[var(--color-muted-foreground)]">
          Serial <b className="text-[var(--color-foreground)]">{data?.serial ?? "—"}</b>
        </span>
        <span className="text-[var(--color-muted-foreground)]">
          Resources <b className="text-[var(--color-foreground)]">{data?.resource_count ?? 0}</b>
        </span>
        <span className="text-[var(--color-muted-foreground)]">
          Versions <b className="text-[var(--color-foreground)]">{data?.version_count ?? 0}</b>
          {latest ? <> · last {relTime(latest.created_at)}</> : null}
        </span>
        <span className="text-[var(--color-muted-foreground)]">
          Backend <b className="text-[var(--color-foreground)]">{data?.backend?.backend_type || "local"}</b>
        </span>

        <div className="ml-auto flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={!latest}
            title={latest ? "Restore the most recent state snapshot" : "No snapshots yet"}
            onClick={() => latest && setTarget(latest)}
          >
            <RotateCcw className="h-3.5 w-3.5 mr-1.5" /> Rollback
          </Button>
          <Button size="sm" variant="outline" onClick={onManage}>
            <Settings className="h-3.5 w-3.5 mr-1.5" /> Manage state
          </Button>
        </div>
      </div>

      {target && <RestoreStateDialog stackId={stackId} version={target} onClose={() => setTarget(null)} />}
    </>
  );
}
