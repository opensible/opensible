import { useEffect, useState } from "react";
import { Timer } from "lucide-react";
import { toast } from "sonner";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

const MIN = 30;
const MAX = 24 * 60 * 60;

type HostSettings = { ttl_seconds: number; auto_check_all_hosts: boolean };

export function HostStatusCacheCard({ projectId }: { projectId: string | null }) {
  const qc = useQueryClient();
  const [ttl, setTtl] = useState<number>(300);
  const [autoCheck, setAutoCheck] = useState<boolean>(false);
  const [saving, setSaving] = useState(false);

  const settingsQ = useQuery({
    enabled: !!projectId,
    queryKey: ["project-host-settings", projectId],
    queryFn: () =>
      api<{ success: boolean; settings: HostSettings }>(
        "GET",
        `/api/projects/${encodeURIComponent(projectId!)}/host_settings`,
      ),
  });

  useEffect(() => {
    const s = settingsQ.data?.settings;
    if (s) {
      setTtl(Number(s.ttl_seconds) || 300);
      setAutoCheck(!!s.auto_check_all_hosts);
    }
  }, [settingsQ.data]);

  async function save() {
    if (!projectId) return;
    const v = Math.max(MIN, Math.min(MAX, Math.floor(Number(ttl) || 0)));
    if (v !== ttl) setTtl(v);
    setSaving(true);
    try {
      await api("PUT", `/api/projects/${encodeURIComponent(projectId)}/host_settings`, {
        ttl_seconds: v,
        auto_check_all_hosts: autoCheck,
      });
      qc.invalidateQueries({ queryKey: ["project-host-settings", projectId] });
      toast.success("Host status cache settings saved");
    } catch (e: any) {
      toast.error(e?.message || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Timer className="h-5 w-5" /> Host status cache TTL
        </CardTitle>
        <p className="text-sm text-[var(--color-muted-foreground)] mt-2">
          Controls how long host availability check results are cached. When TTL expires, status becomes Unknown
          and a new check must be started manually.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-3">
          <Input
            type="number"
            min={MIN}
            max={MAX}
            value={ttl}
            onChange={(e) => setTtl(Number(e.target.value))}
            className="w-32"
          />
          <span className="text-sm">
            seconds <span className="text-[var(--color-muted-foreground)]">(min: 30s, max: 24h)</span>
          </span>
        </div>

        <label className="flex items-start gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            className="mt-1"
            checked={autoCheck}
            onChange={(e) => setAutoCheck(e.target.checked)}
          />
          <span>
            <div className="font-medium">Auto-check all hosts (background)</div>
            <div className="text-sm text-[var(--color-muted-foreground)]">
              When enabled, the server periodically re-checks hosts whose cached status has expired. Disabled by
              default — checks stay manual.
            </div>
          </span>
        </label>

        <div className="flex justify-end">
          <Button onClick={save} disabled={!projectId || saving || settingsQ.isLoading}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
