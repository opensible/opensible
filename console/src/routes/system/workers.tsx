import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, useEffect, ReactNode } from "react";
import {
  Server, RefreshCw, Plus, Pencil, KeyRound, Ban, Check, Trash2,
  Info, Copy, AlertTriangle, X, Circle, PlugZap,
} from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/system/workers")({ component: WorkersPage });

type Worker = {
  id: string;
  name?: string;
  description?: string;
  tags?: string[];
  tagColors?: Record<string, string>;
  capabilities?: Record<string, unknown>;
  enabled?: boolean;
  lastSeenAt?: number;
  status?: string;
};

type WorkersResp = { success: boolean; workers: Worker[] };
type CreateResp = { success: boolean; worker?: Worker; workerToken?: string };
type RotateResp = { success: boolean; workerToken?: string };

const HEARTBEAT_TTL = 60;

function WorkersPage() {
  const t = useT();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["admin-workers"],
    queryFn: () => api<WorkersResp>("GET", "/api/admin/workers"),
    refetchInterval: 5000,
  });
  const workers = q.data?.workers ?? [];

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Worker | null>(null);
  const [tokenShow, setTokenShow] = useState<{ token: string; title: string } | null>(null);
  const [connectOpen, setConnectOpen] = useState(false);
  const [confirm, setConfirm] = useState<{
    title: string; description: ReactNode; variant?: "default" | "destructive"; onConfirm: () => void;
  } | null>(null);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin-workers"] });

  const enableM = useMutation({
    mutationFn: (id: string) => api("POST", `/api/admin/workers/${id}/enable`),
    onSuccess: invalidate,
  });
  const disableM = useMutation({
    mutationFn: (id: string) => api("POST", `/api/admin/workers/${id}/disable`),
    onSuccess: invalidate,
  });
  const deleteM = useMutation({
    mutationFn: (id: string) => api("DELETE", `/api/admin/workers/${id}`),
    onSuccess: invalidate,
  });
  const rotateM = useMutation({
    mutationFn: (id: string) => api<RotateResp>("POST", `/api/admin/workers/${id}/rotate-token`),
    onSuccess: (data) => {
      invalidate();
      if (data?.workerToken) setTokenShow({ token: data.workerToken, title: "New Worker Token" });
    },
  });

  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "System" }, { label: "Workers" }]} />
      <div className="flex items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold">{t("page.workers.title")}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">{t("page.workers.subtitle")}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => q.refetch()}>
            <RefreshCw className={`h-4 w-4 ${q.isFetching ? "animate-spin" : ""}`} /> {t("common.refresh")}
          </Button>
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" /> {t("workers.addWorker")}
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2"><Server className="h-4 w-4" /> {t("workers.agents")}</CardTitle>
          <CardDescription>{t("workers.agentsDesc")}</CardDescription>
        </CardHeader>
        <CardContent className="px-0">
          {q.isLoading ? (
            <div className="py-12 text-center text-sm text-[var(--color-muted-foreground)]">{t("workers.loading")}</div>
          ) : q.isError ? (
            <div className="px-6 py-6 text-sm text-[var(--color-destructive)]">{(q.error as Error).message}</div>
          ) : workers.length === 0 ? (
            <div className="py-12 text-center text-sm text-[var(--color-muted-foreground)]">
              <Server className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <div>{t("workers.empty")}</div>
              <div className="text-xs mt-1">{t("workers.emptyHint")}</div>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wider text-[var(--color-muted-foreground)] border-b">
                <tr>
                  <th className="text-left font-medium px-6 py-2.5">{t("common.name")}</th>
                  <th className="text-left font-medium px-3 py-2.5">{t("common.status")}</th>
                  <th className="text-left font-medium px-3 py-2.5">{t("common.lastSeen")}</th>
                  <th className="text-left font-medium px-3 py-2.5">{t("common.tags")}</th>
                  <th className="text-right font-medium px-6 py-2.5">{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {workers.map((w) => {
                  const online = w.lastSeenAt != null && now - w.lastSeenAt <= HEARTBEAT_TTL;
                  const status = !w.enabled ? "Disabled" : online ? "Online" : "Offline";
                  const dotColor = !w.enabled
                    ? "text-[var(--color-muted-foreground)]"
                    : online
                    ? "text-[var(--color-success,#10B981)]"
                    : "text-[var(--color-destructive)]";
                  return (
                    <tr key={w.id} className="border-b hover:bg-[var(--color-muted)]/30">
                      <td className="px-6 py-3">
                        <div className="font-medium">{w.name || "Unnamed"}</div>
                        <div className="text-[11px] font-mono text-[var(--color-muted-foreground)] mt-0.5">{w.id.substring(0, 8)}…</div>
                      </td>
                      <td className="px-3 py-3">
                        <span className={cn("inline-flex items-center gap-1.5 text-xs font-medium", dotColor)}>
                          <Circle className="h-2 w-2 fill-current" /> {status}
                        </span>
                      </td>
                      <td className="px-3 py-3 text-[var(--color-muted-foreground)] text-xs">{relTime(w.lastSeenAt, now)}</td>
                      <td className="px-3 py-3">
                        <div className="flex flex-wrap gap-1.5">
                          {(w.tags ?? []).map((t) => (
                            <Badge key={t} variant="default" className="text-[11px]">{t}</Badge>
                          ))}
                          {w.capabilities && Object.keys(w.capabilities).length > 0 && (
                            <Badge variant="default" className="text-[11px]">
                              {Object.keys(w.capabilities).length} caps
                            </Badge>
                          )}
                        </div>
                      </td>
                      <td className="px-6 py-3">
                        <div className="flex justify-end gap-1">
                          <IconBtn title="Connect worker agent" onClick={() => setConnectOpen(true)}><PlugZap className="h-3.5 w-3.5" /></IconBtn>
                          <IconBtn title="Edit" onClick={() => setEditing(w)}><Pencil className="h-3.5 w-3.5" /></IconBtn>
                          <IconBtn
                            title="Rotate token"
                            onClick={() =>
                              setConfirm({
                                title: "Rotate worker token?",
                                description: "The old token will stop working immediately.",
                                onConfirm: () => { rotateM.mutate(w.id); setConfirm(null); },
                              })
                            }
                          >
                            <KeyRound className="h-3.5 w-3.5" />
                          </IconBtn>
                          {w.enabled ? (
                            <IconBtn title="Disable" onClick={() => disableM.mutate(w.id)} tone="warning">
                              <Ban className="h-3.5 w-3.5" />
                            </IconBtn>
                          ) : (
                            <IconBtn title="Enable" onClick={() => enableM.mutate(w.id)} tone="success">
                              <Check className="h-3.5 w-3.5" />
                            </IconBtn>
                          )}
                          <IconBtn
                            title="Delete"
                            tone="destructive"
                            onClick={() =>
                              setConfirm({
                                title: `Delete worker "${w.name || "Unnamed"}"?`,
                                description: "This action cannot be undone.",
                                variant: "destructive",
                                onConfirm: () => { deleteM.mutate(w.id); setConfirm(null); },
                              })
                            }
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </IconBtn>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      {connectOpen && <ConnectWorkerPanel onClose={() => setConnectOpen(false)} />}



      {createOpen && (
        <CreateWorkerDialog
          onClose={() => setCreateOpen(false)}
          onCreated={(token) => { invalidate(); setTokenShow({ token, title: "Worker Created" }); }}
        />
      )}
      {editing && (
        <EditWorkerDialog worker={editing} onClose={() => setEditing(null)} onSaved={() => { invalidate(); setEditing(null); }} />
      )}
      {tokenShow && <TokenDialog token={tokenShow.token} title={tokenShow.title} onClose={() => setTokenShow(null)} />}
      <ConfirmDialog
        open={!!confirm}
        title={confirm?.title ?? ""}
        description={confirm?.description}
        variant={confirm?.variant}
        onConfirm={() => confirm?.onConfirm()}
        onCancel={() => setConfirm(null)}
      />
    </div>
  );
}

function IconBtn({
  children, onClick, title, tone = "default",
}: {
  children: ReactNode; onClick: () => void; title: string;
  tone?: "default" | "success" | "warning" | "destructive";
}) {
  const toneCls = {
    default: "text-[var(--color-foreground)] hover:bg-[var(--color-muted)]",
    success: "text-[var(--color-success,#10B981)] hover:bg-[var(--color-success,#10B981)]/10",
    warning: "text-[var(--color-warning,#F59E0B)] hover:bg-[var(--color-warning,#F59E0B)]/10",
    destructive: "text-[var(--color-destructive)] hover:bg-[var(--color-destructive)]/10",
  }[tone];
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={cn("inline-flex items-center justify-center h-7 w-7 rounded-md border border-[var(--color-border)] transition-colors", toneCls)}
    >
      {children}
    </button>
  );
}

function relTime(ts: number | undefined, now: number) {
  if (!ts) return "Never";
  const s = Math.max(0, Math.floor(now - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function ModalShell({ title, icon, onClose, children }: { title: string; icon?: ReactNode; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className="bg-[var(--color-card)] rounded-lg shadow-2xl w-full max-w-lg border border-[var(--color-border)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-[var(--color-border)]">
          <h3 className="text-base font-semibold flex items-center gap-2">{icon}{title}</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-[var(--color-muted)]"><X className="h-4 w-4" /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

function CreateWorkerDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (token: string) => void }) {
  const [name, setName] = useState("");
  const [tags, setTags] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const m = useMutation({
    mutationFn: () =>
      api<CreateResp>("POST", "/api/admin/workers", {
        name: name.trim(),
        tags: tags.trim().split(/\s+/).filter(Boolean),
      }),
    onSuccess: (data) => {
      if (data.workerToken) { onCreated(data.workerToken); onClose(); }
    },
    onError: (e: Error) => setErr(e.message),
  });
  return (
    <ModalShell title="Create Worker" icon={<Plus className="h-4 w-4" />} onClose={onClose}>
      <div className="space-y-4">
        <div>
          <label className="text-sm font-medium block mb-1.5">Name <span className="text-[var(--color-destructive)]">*</span></label>
          <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="worker-1" />
        </div>
        <div>
          <label className="text-sm font-medium block mb-1.5">Tags (space-separated)</label>
          <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="docker linux production" />
          <p className="text-xs text-[var(--color-muted-foreground)] mt-1">Tags help filter which tasks this worker can execute.</p>
        </div>
        {err && <div className="text-sm text-[var(--color-destructive)]">{err}</div>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onClose} disabled={m.isPending}>Cancel</Button>
          <Button onClick={() => { setErr(null); if (!name.trim()) { setErr("Name is required"); return; } m.mutate(); }} disabled={m.isPending}>
            {m.isPending ? "Creating…" : "Create"}
          </Button>
        </div>
      </div>
    </ModalShell>
  );
}

function EditWorkerDialog({ worker, onClose, onSaved }: { worker: Worker; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(worker.name ?? "");
  const [description, setDescription] = useState(worker.description ?? "");
  const [tags, setTags] = useState((worker.tags ?? []).join(", "));
  const [err, setErr] = useState<string | null>(null);
  const m = useMutation({
    mutationFn: () =>
      api("PATCH", `/api/admin/workers/${encodeURIComponent(worker.id)}`, {
        name: name.trim(),
        description: description.trim(),
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      }),
    onSuccess: onSaved,
    onError: (e: Error) => setErr(e.message),
  });
  return (
    <ModalShell title="Edit Worker" icon={<Pencil className="h-4 w-4" />} onClose={onClose}>
      <div className="space-y-4">
        <div>
          <label className="text-sm font-medium block mb-1.5">Name <span className="text-[var(--color-destructive)]">*</span></label>
          <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="text-sm font-medium block mb-1.5">Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-background)] resize-y"
            placeholder="Worker description…"
          />
        </div>
        <div>
          <label className="text-sm font-medium block mb-1.5">Tags (comma-separated)</label>
          <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="docker, linux, production" />
        </div>
        {err && <div className="text-sm text-[var(--color-destructive)]">{err}</div>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onClose} disabled={m.isPending}>Cancel</Button>
          <Button onClick={() => { setErr(null); if (!name.trim()) { setErr("Name is required"); return; } m.mutate(); }} disabled={m.isPending}>
            {m.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>
    </ModalShell>
  );
}

function TokenDialog({ token, title, onClose }: { token: string; title: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(token); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {}
  };
  return (
    <ModalShell title={title} icon={<KeyRound className="h-4 w-4" />} onClose={onClose}>
      <div className="space-y-3">
        <div className="flex items-start gap-2 text-sm bg-[var(--color-warning,#F59E0B)]/10 border border-[var(--color-warning,#F59E0B)]/30 rounded-md p-3">
          <AlertTriangle className="h-4 w-4 text-[var(--color-warning,#F59E0B)] shrink-0 mt-0.5" />
          <span>Save this token now — it will not be shown again.</span>
        </div>
        <div className="flex gap-2">
          <Input readOnly value={token} className="font-mono text-xs" />
          <Button onClick={copy} variant="outline"><Copy className="h-4 w-4" /> {copied ? "Copied" : "Copy"}</Button>
        </div>
        <div className="flex justify-end pt-2">
          <Button onClick={onClose}>Close</Button>
        </div>
      </div>
    </ModalShell>
  );
}

function ConnectWorkerPanel({ onClose }: { onClose: () => void }) {
  const origin = typeof window !== "undefined" ? window.location.origin : "https://platform";
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onClick={onClose}>
      <div
        className="h-full w-full max-w-md bg-[var(--color-card)] border-l border-[var(--color-border)] shadow-2xl overflow-y-auto animate-in slide-in-from-right duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-[var(--color-border)]">
          <h3 className="text-base font-semibold flex items-center gap-2">
            <Info className="h-4 w-4" /> How to connect a worker agent
          </h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-[var(--color-muted)]"><X className="h-4 w-4" /></button>
        </div>
        <div className="p-5 text-sm text-[var(--color-muted-foreground)] space-y-2">
          <p>1. Create a worker above and copy the token.</p>
          <p>2. Set environment variables:</p>
          <pre className="bg-[var(--color-muted)] rounded-md p-3 text-xs text-[var(--color-foreground)] overflow-x-auto">
{`export WORKER_SERVER_URL=${origin}
export WORKER_TOKEN=your-token-here`}
          </pre>
          <p>3. Run the worker:</p>
          <pre className="bg-[var(--color-muted)] rounded-md p-3 text-xs text-[var(--color-foreground)] overflow-x-auto">{`cd web
python3 worker.py`}</pre>
          <p className="text-xs">Or use a token file: create <code className="font-mono">web/worker/.token</code> with the worker token.</p>
        </div>
      </div>
    </div>
  );
}
