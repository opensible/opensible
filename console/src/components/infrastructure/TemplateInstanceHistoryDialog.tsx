import { useQuery } from "@tanstack/react-query";
import { X, History, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

type Commit = { sha: string; author: string; date: string; message: string };

export function TemplateInstanceHistoryDialog({
  path, onClose, onRestore,
}: {
  path: string;
  onClose: () => void;
  onRestore: (values: unknown, targets: unknown, sha: string) => void;
}) {
  const q = useQuery({
    queryKey: ["template-instance-history", path],
    queryFn: () => api<{ commits: Commit[] }>(
      "GET", `/api/templates/instances/history?path=${encodeURIComponent(path)}`
    ),
  });

  const restore = async (sha: string) => {
    try {
      const res = await api<{ config: { values: unknown; targets: unknown } }>(
        "GET", `/api/templates/instances/version?path=${encodeURIComponent(path)}&sha=${sha}`
      );
      onRestore(res.config.values, res.config.targets, sha);
      toast.success(`Restored version ${sha.slice(0, 7)} into the wizard`);
      onClose();
    } catch (e) { toast.error((e as Error).message); }
  };

  const commits = q.data?.commits || [];
  return (
    <div className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-[var(--color-background)] border border-[var(--color-border)] rounded-lg shadow-xl w-full max-w-2xl max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4" />
            <div className="font-medium text-sm">Version history</div>
            <Badge variant="default" className="text-[10px] font-mono">{path}</Badge>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-[var(--color-accent)] rounded"><X className="h-4 w-4" /></button>
        </div>
        <div className="flex-1 overflow-auto">
          {q.isLoading && <div className="p-4 text-xs text-[var(--color-muted-foreground)]">Loading git history…</div>}
          {!q.isLoading && commits.length === 0 && (
            <div className="p-4 text-xs text-[var(--color-muted-foreground)]">
              No git history yet. Push your repo (Project Settings → Git) so commits get recorded, then re-open.
            </div>
          )}
          <ul className="divide-y divide-[var(--color-border)]">
            {commits.map((c) => (
              <li key={c.sha} className="px-4 py-3 flex items-center justify-between gap-3 text-xs">
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{c.message}</div>
                  <div className="text-[11px] text-[var(--color-muted-foreground)] mt-0.5">
                    <span className="font-mono">{c.sha.slice(0, 8)}</span> · {c.author} · {new Date(c.date).toLocaleString()}
                  </div>
                </div>
                <Button size="sm" variant="outline" onClick={() => restore(c.sha)}>
                  <RotateCcw className="h-3.5 w-3.5 mr-1.5" /> Restore
                </Button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
