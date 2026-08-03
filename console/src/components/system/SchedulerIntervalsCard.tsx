import { useEffect, useState } from "react";
import { Timer } from "lucide-react";
import { toast } from "sonner";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

type Key =
  | "playbook_scheduler_interval"
  | "execution_recovery_interval";

type Settings = Record<Key, number>;
type Bounds = Record<Key, { default: number; min: number; max: number }>;

const FIELDS: { key: Key; label: string; help: string }[] = [
  {
    key: "playbook_scheduler_interval",
    label: "Playbook cron scheduler",
    help: "Tick for evaluating playbook cron schedules. Also sets cron firing granularity.",
  },
  {
    key: "execution_recovery_interval",
    label: "Execution recovery",
    help: "How often stalled or stuck executions are detected and recovered.",
  },
];

export function SchedulerIntervalsCard() {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Partial<Settings>>({});

  const q = useQuery({
    queryKey: ["scheduler-settings"],
    queryFn: () => api<{ success: boolean; settings: Settings; bounds: Bounds }>("GET", "/api/scheduler_settings"),
  });

  useEffect(() => {
    if (q.data?.settings) setDraft(q.data.settings);
  }, [q.data]);

  const save = useMutation({
    mutationFn: (body: Partial<Settings>) => api("PUT", "/api/scheduler_settings", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scheduler-settings"] });
      toast.success("Scheduler intervals saved — applied within one tick");
    },
    onError: (e: any) => toast.error(e?.message || "Save failed"),
  });

  const bounds = q.data?.bounds;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Timer className="h-4 w-4" /> Background scheduler intervals
        </CardTitle>
        <CardDescription>
          Tune how often background loops run (seconds). Higher values reduce idle CPU usage. Changes apply live,
          no restart needed. Git auto sync and host status auto-check are configured per project in Project
          Settings.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {FIELDS.map(f => {
          const b = bounds?.[f.key];
          return (
            <div key={f.key} className="border-b border-[var(--color-border)] pb-3 last:border-0 last:pb-0">
              <label className="block text-sm font-medium mb-1">{f.label}</label>
              <p className="text-xs text-[var(--color-muted-foreground)] mb-2">{f.help}</p>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  min={b?.min}
                  max={b?.max}
                  value={draft[f.key] ?? ""}
                  onChange={e => setDraft(d => ({ ...d, [f.key]: Number(e.target.value) }))}
                  className="w-32"
                />
                <span className="text-sm">seconds</span>
                {b && (
                  <span className="text-xs text-[var(--color-muted-foreground)]">
                    (min: {b.min}s, max: {b.max}s, default: {b.default}s)
                  </span>
                )}
              </div>
            </div>
          );
        })}

        <div className="flex justify-end">
          <Button onClick={() => save.mutate(draft)} disabled={q.isLoading || save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
