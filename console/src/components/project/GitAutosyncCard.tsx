import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { formatSyncTime } from "@/lib/format-time";

const MIN_INTERVAL = 60;
const MAX_INTERVAL = 24 * 60 * 60;

type Autosync = {
  enabled: boolean;
  direction: "pull" | "push" | "both";
  sourceKeys: string[];
  intervalSeconds: number;
  lastRunAt?: number | null;
  nextRunAt?: number | null;
  lastStatus?: string | null;
  lastError?: string | null;
};

type Props = {
  projectId: string | null;
  /** Source key this project's git workspace uses: "repo" (infra) or "stacks" (cloud). */
  sourceKey: string;
  sourceLabel: string;
};

export function GitAutosyncCard({ projectId, sourceKey, sourceLabel }: Props) {
  const qc = useQueryClient();
  const [enabled, setEnabled] = useState(false);
  const [direction, setDirection] = useState<"pull" | "push" | "both">("pull");
  const [interval, setInterval] = useState<number>(3600);

  const q = useQuery({
    enabled: !!projectId,
    queryKey: ["project-autosync", projectId],
    queryFn: () =>
      api<{ success: boolean; autosync: Autosync }>(
        "GET",
        `/api/projects/${encodeURIComponent(projectId!)}/autosync`,
      ),
  });

  useEffect(() => {
    const a = q.data?.autosync;
    if (!a) return;
    setEnabled(!!a.enabled);
    setDirection((a.direction as any) || "pull");
    setInterval(Number(a.intervalSeconds) || 3600);
  }, [q.data]);

  const save = useMutation({
    mutationFn: () => {
      const v = Math.max(MIN_INTERVAL, Math.min(MAX_INTERVAL, Math.floor(Number(interval) || 0)));
      if (v !== interval) setInterval(v);
      const existing = q.data?.autosync?.sourceKeys || [];
      const sourceKeys = enabled
        ? Array.from(new Set([...existing, sourceKey]))
        : existing;
      return api("PUT", `/api/projects/${encodeURIComponent(projectId!)}/autosync`, {
        enabled,
        direction,
        sourceKeys,
        intervalSeconds: v,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project-autosync", projectId] });
      toast.success(enabled ? "Auto sync enabled" : "Auto sync disabled");
    },
    onError: (e: any) => toast.error(e?.message || "Save failed"),
  });

  const a = q.data?.autosync;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <RefreshCw className="h-5 w-5" /> Git auto sync
        </CardTitle>
        <p className="text-sm text-[var(--color-muted-foreground)] mt-2">
          Periodically syncs the {sourceLabel} workspace with Git. Disabled by default — sync stays manual until
          you enable it.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <label className="flex items-start gap-2 cursor-pointer select-none">
          <input type="checkbox" className="mt-1" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          <span>
            <div className="font-medium">Enable auto sync</div>
            <div className="text-sm text-[var(--color-muted-foreground)]">
              When off, no background git scan runs for this project.
            </div>
          </span>
        </label>

        <div className={enabled ? "space-y-4" : "space-y-4 opacity-50 pointer-events-none"}>
          <div>
            <label className="block text-sm font-medium mb-1">Direction</label>
            <select
              className="h-10 px-3 rounded-md border border-[var(--color-border)] bg-[var(--color-input,transparent)] text-sm"
              value={direction}
              onChange={(e) => setDirection(e.target.value as any)}
            >
              <option value="pull">Pull (Git → Project Storage)</option>
              <option value="push">Push (Project Storage → Git)</option>
              <option value="both">Bidirectional</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">Scan interval</label>
            <div className="flex items-center gap-3">
              <Input
                type="number"
                min={MIN_INTERVAL}
                max={MAX_INTERVAL}
                value={interval}
                onChange={(e) => setInterval(Number(e.target.value))}
                className="w-32"
              />
              <span className="text-sm">
                seconds{" "}
                <span className="text-[var(--color-muted-foreground)]">(min: 60s, max: 24h, default: 3600s)</span>
              </span>
            </div>
          </div>
        </div>

        {a?.lastRunAt != null && (
          <div className="text-xs text-[var(--color-muted-foreground)]">
            Last run: {formatSyncTime(a.lastRunAt)} · status: {a.lastStatus || "—"}
            {a.lastError ? ` · ${a.lastError}` : ""}
          </div>
        )}

        <div className="flex justify-end">
          <Button onClick={() => save.mutate()} disabled={!projectId || q.isLoading || save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
