import { useEffect, useState } from "react";
import { Timer } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const MIN = 30;
const MAX = 24 * 60 * 60;

type Settings = { ttl: number; autoCheck: boolean };

function storageKey(projectId: string | null) {
  return `ansible_host_status_cache:${projectId || "_"}`;
}

function load(projectId: string | null): Settings {
  try {
    const raw = localStorage.getItem(storageKey(projectId));
    if (raw) {
      const v = JSON.parse(raw);
      return { ttl: Number(v.ttl) || 300, autoCheck: !!v.autoCheck };
    }
  } catch { /* ignore */ }
  return { ttl: 300, autoCheck: false };
}

export function HostStatusCacheCard({ projectId }: { projectId: string | null }) {
  const [ttl, setTtl] = useState<number>(300);
  const [autoCheck, setAutoCheck] = useState<boolean>(false);

  useEffect(() => {
    const s = load(projectId);
    setTtl(s.ttl);
    setAutoCheck(s.autoCheck);
  }, [projectId]);

  function save() {
    const v = Math.max(MIN, Math.min(MAX, Math.floor(Number(ttl) || 0)));
    if (v !== ttl) setTtl(v);
    try {
      localStorage.setItem(storageKey(projectId), JSON.stringify({ ttl: v, autoCheck }));
      toast.success("Host status cache settings saved");
    } catch (e: any) {
      toast.error(e?.message || "Save failed");
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
            <div className="font-medium">Auto-check all hosts on Hosts &amp; Groups open</div>
            <div className="text-sm text-[var(--color-muted-foreground)]">
              When enabled, the project will automatically run "Check all hosts" once when you open Hosts &amp; Groups.
            </div>
          </span>
        </label>

        <div className="flex justify-end">
          <Button onClick={save} disabled={!projectId}>Save</Button>
        </div>
      </CardContent>
    </Card>
  );
}
