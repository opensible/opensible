import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen, RefreshCw, Plus, Play, Search, Upload, Download, Copy,
  Edit3, Trash2, CheckCircle2, AlertCircle, FileCode, Eye,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { YamlEditor } from "@/components/ui/yaml-editor";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";


type Playbook = {
  id: string;
  name: string;
  description?: string;
  tag?: string;
  tags?: string[];
  updated_at?: string | number;
  created_at?: string | number;
  validation_status?: boolean;
  validation_errors_count?: number;
  disabled?: boolean;
  metadata?: Record<string, unknown>;
  plays?: unknown[];
};

type RunRecord = {
  run_id?: string;
  id?: string;
  status?: string;
  started_at?: string;
  finished_at?: string;
  exit_code?: number;
};

function Modal({ open, onClose, title, children, footer, wide }: {
  open: boolean; onClose: () => void; title: string;
  children: React.ReactNode; footer?: React.ReactNode; wide?: boolean;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className={`bg-[var(--color-card)] rounded-lg shadow-2xl w-full ${wide ? "max-w-4xl" : "max-w-lg"} border border-[var(--color-border)] flex flex-col max-h-[90vh]`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-3 border-b border-[var(--color-border)] flex items-center justify-between">
          <h2 className="text-base font-semibold">{title}</h2>
          <button onClick={onClose} className="text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] text-xl leading-none">×</button>
        </div>
        <div className="p-5 overflow-y-auto flex-1">{children}</div>
        {footer && <div className="px-5 py-3 border-t border-[var(--color-border)] flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

export function PlaybooksPanel() {
  const t = useT();
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 15;
  const [showCreate, setShowCreate] = useState(false);
  const [editTarget, setEditTarget] = useState<Playbook | null>(null);
  const [previewTarget, setPreviewTarget] = useState<Playbook | null>(null);
  const [runTarget, setRunTarget] = useState<Playbook | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Playbook | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const query = useQuery({
    queryKey: ["playbooks"],
    queryFn: () =>
      api<{ success: boolean; playbooks: Playbook[] }>("GET", "/api/projects/_current/playbooks"),
    refetchInterval: 30_000,
    staleTime: 15_000,
    placeholderData: (prev) => prev,
  });
  const playbooks: Playbook[] = useMemo(() => {
    const d = query.data as any;
    return Array.isArray(d?.playbooks) ? d.playbooks : Array.isArray(d) ? d : [];
  }, [query.data]);
  const filtered = useMemo(
    () => playbooks.filter(
      (p) => !q || `${p.name} ${p.description ?? ""} ${p.tag ?? ""}`.toLowerCase().includes(q.toLowerCase()),
    ),
    [playbooks, q],
  );
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  useEffect(() => { setPage(1); }, [q]);
  const paged = useMemo(
    () => filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE),
    [filtered, currentPage],
  );
  const showInitialLoading = query.isLoading && playbooks.length === 0;

  const deleteMut = useMutation({
    mutationFn: (id: string) => api("DELETE", `/api/projects/_current/playbooks/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["playbooks"] });
      setDeleteTarget(null);
    },
  });

  const cloneMut = useMutation({
    mutationFn: (id: string) => api("POST", `/api/projects/_current/playbooks/${id}/clone`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["playbooks"] }),
  });

  const onUpload = async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const projectId = window.localStorage.getItem("current_project_id");
    const token = window.localStorage.getItem("auth_token");
    const res = await fetch(`/api/projects/${projectId}/playbooks/upload`, {
      method: "POST",
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(projectId ? { "X-Project-Id": projectId } : {}),
      },
      body: fd,
    });
    if (!res.ok) {
      const t = await res.text();
      alert(`Upload failed: ${t}`);
      return;
    }
    qc.invalidateQueries({ queryKey: ["playbooks"] });
  };

  const downloadPlaybook = async (pb: Playbook) => {
    const projectId = window.localStorage.getItem("current_project_id");
    const token = window.localStorage.getItem("auth_token");
    const res = await fetch(`/api/projects/${projectId}/playbooks/${pb.id}/download`, {
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(projectId ? { "X-Project-Id": projectId } : {}),
      },
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${pb.name}.yml`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="text-sm text-[var(--color-muted-foreground)]">
          {t("page.playbooks.subtitleCount", { count: playbooks.length })}
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => query.refetch()}>
            <RefreshCw className={`h-4 w-4 mr-1.5 ${query.isFetching ? "animate-spin" : ""}`} /> Refresh
          </Button>
          <input
            ref={fileInputRef} type="file" accept=".yml,.yaml" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) onUpload(f); e.target.value = ""; }}
          />
          <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
            <Upload className="h-4 w-4 mr-1.5" /> Upload YAML
          </Button>
          <Button size="sm" onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4 mr-1.5" /> New Playbook
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader className="py-3 flex flex-row items-center justify-between">
          <CardTitle className="text-sm">Playbook Library</CardTitle>
          <div className="relative w-72">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name, tag, description…" className="h-8 pl-7 text-sm" />
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Description</th>
                <th className="px-4 py-2 font-medium">Tag</th>
                <th className="px-4 py-2 font-medium">Updated</th>
                <th className="px-4 py-2 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {showInitialLoading && (
                <tr><td colSpan={6} className="px-4 py-6 text-center text-[var(--color-muted-foreground)]">Loading…</td></tr>
              )}
              {!showInitialLoading && filtered.length === 0 && (
                <tr><td colSpan={6} className="px-4 py-10 text-center text-[var(--color-muted-foreground)]">
                  <FileCode className="h-8 w-8 mx-auto mb-2 opacity-40" />
                  No playbooks found. Click <span className="text-[var(--color-foreground)]">New Playbook</span> or upload a YAML file.
                </td></tr>
              )}
              {paged.map((p) => (
                <tr key={p.id} className="border-b hover:bg-[var(--color-accent)]/40">
                  <td className="px-4 py-2">
                    {p.validation_status ? (
                      <span title="Ready to run" className="inline-flex items-center gap-1 text-[var(--color-success)] text-xs">
                        <CheckCircle2 className="h-3.5 w-3.5" /> OK
                      </span>
                    ) : (
                      <span title={`${p.validation_errors_count ?? 0} validation issue(s)`} className="inline-flex items-center gap-1 text-[var(--color-warning)] text-xs">
                        <AlertCircle className="h-3.5 w-3.5" /> {p.validation_errors_count ?? "—"}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 font-medium">{p.name}{p.disabled && <Badge className="ml-2 text-[10px]">disabled</Badge>}</td>
                  <td className="px-4 py-2 text-[var(--color-muted-foreground)] max-w-xs truncate">{p.description || "—"}</td>
                  <td className="px-4 py-2">{p.tag ? <Badge variant="default" className="text-[10px]">{p.tag}</Badge> : "—"}</td>
                  <td className="px-4 py-2 text-xs text-[var(--color-muted-foreground)]">{fmtDate(p.updated_at)}</td>
                  <td className="px-4 py-2 text-right whitespace-nowrap">
                    <Button size="sm" variant="ghost" title="Run" onClick={() => setRunTarget(p)}>
                      <Play className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="sm" variant="ghost" title="Preview YAML" onClick={() => setPreviewTarget(p)}>
                      <Eye className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="sm" variant="ghost" title="Edit" onClick={() => setEditTarget(p)}>
                      <Edit3 className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="sm" variant="ghost" title="Clone" onClick={() => cloneMut.mutate(p.id)}>
                      <Copy className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="sm" variant="ghost" title="Download" onClick={() => downloadPlaybook(p)}>
                      <Download className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="sm" variant="ghost" title="Delete" onClick={() => setDeleteTarget(p)}>
                      <Trash2 className="h-3.5 w-3.5 text-[var(--color-destructive)]" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length > 0 && (
            <div className="flex items-center justify-between px-4 py-2.5 border-t border-[var(--color-border)] text-xs text-[var(--color-muted-foreground)]">
              <div>
                Showing {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, filtered.length)} of {filtered.length}
              </div>
              <div className="flex items-center gap-2">
                <Button size="sm" variant="outline" disabled={currentPage <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>Previous</Button>
                <span>Page {currentPage} of {totalPages}</span>
                <Button size="sm" variant="outline" disabled={currentPage >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>Next</Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {showCreate && (
        <CreateOrEditDialog
          mode="create"
          onClose={() => setShowCreate(false)}
          onSaved={() => { setShowCreate(false); qc.invalidateQueries({ queryKey: ["playbooks"] }); }}
        />
      )}
      {editTarget && (
        <CreateOrEditDialog
          mode="edit"
          playbook={editTarget}
          onClose={() => setEditTarget(null)}
          onSaved={() => { setEditTarget(null); qc.invalidateQueries({ queryKey: ["playbooks"] }); }}
        />
      )}
      {previewTarget && (
        <PreviewDialog playbook={previewTarget} onClose={() => setPreviewTarget(null)} />
      )}
      {runTarget && (
        <RunDialog playbook={runTarget} onClose={() => setRunTarget(null)} />
      )}
      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete playbook"
        description={<>This will permanently delete <span className="font-semibold">{deleteTarget?.name}</span>. This action cannot be undone.</>}
        variant="destructive"
        confirmLabel="Delete"
        busy={deleteMut.isPending}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
      />
    </div>
  );
}

function fmtDate(v: unknown): string {
  if (!v) return "—";
  const d = new Date(typeof v === "number" ? v * (String(v).length > 10 ? 1 : 1000) : String(v));
  if (isNaN(d.getTime())) return String(v);
  return d.toLocaleString();
}

function CreateOrEditDialog({
  mode, playbook, onClose, onSaved,
}: { mode: "create" | "edit"; playbook?: Playbook; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(playbook?.name ?? "");
  const [description, setDescription] = useState(playbook?.description ?? "");
  const [tag, setTag] = useState(playbook?.tag ?? "");
  const [disabled, setDisabled] = useState(!!playbook?.disabled);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<"details" | "yaml">("details");
  const [yamlText, setYamlText] = useState<string>("");
  const [yamlLoaded, setYamlLoaded] = useState(false);
  const [yamlLoading, setYamlLoading] = useState(false);

  const isEdit = mode === "edit" && !!playbook;

  useEffect(() => {
    if (!isEdit || tab !== "yaml" || yamlLoaded || yamlLoading) return;
    setYamlLoading(true);
    (async () => {
      try {
        const full = await api<{ success: boolean; playbook: Playbook }>(
          "GET", `/api/projects/_current/playbooks/${playbook!.id}`,
        );
        const res = await api<{ success: boolean; yaml: string }>(
          "POST", `/api/projects/_current/playbooks/${playbook!.id}/preview`,
          { playbook: (full as any).playbook ?? full },
        );
        setYamlText(res.yaml || "---\n# (empty playbook)\n");
      } catch (e) {
        setYamlText(`# Error loading YAML: ${(e as Error).message}`);
      } finally {
        setYamlLoaded(true); setYamlLoading(false);
      }
    })();
  }, [tab, isEdit, playbook, yamlLoaded, yamlLoading]);

  const save = async () => {
    setBusy(true); setErr(null);
    try {
      if (mode === "create") {
        await api("POST", "/api/projects/_current/playbooks", { name, description, tag });
      } else if (playbook) {
        await api("PUT", `/api/projects/_current/playbooks/${playbook.id}`, {
          name, description, tag, disabled,
        });
        if (yamlLoaded) {
          await api("PUT", `/api/projects/_current/playbooks/${playbook.id}/yaml`, { yaml: yamlText });
        }
      }
      onSaved();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      wide={isEdit}
      title={mode === "create" ? "New Playbook" : `Edit Playbook — ${playbook?.name}`}
      footer={<>
        <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button onClick={save} disabled={busy || !name.trim()}>{busy ? "Saving…" : "Save"}</Button>
      </>}
    >
      {isEdit && (
        <div className="flex gap-1 border-b border-[var(--color-border)] mb-3 -mt-1">
          {(["details", "yaml"] as const).map(t => (
            <button
              key={t} onClick={() => setTab(t)}
              className={`px-3 py-1.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
                tab === t
                  ? "border-[var(--color-primary)] text-[var(--color-foreground)]"
                  : "border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
              }`}
            >
              {t === "details" ? "Details" : "YAML"}
            </button>
          ))}
        </div>
      )}

      {(!isEdit || tab === "details") && (
        <div className="space-y-3">
          <Field label="Name *">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="my-playbook" />
          </Field>
          <Field label="Description">
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Optional description" />
          </Field>
          <Field label="Tag">
            <Input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="e.g. base, web, db" />
          </Field>
          {mode === "edit" && (
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={disabled} onChange={(e) => setDisabled(e.target.checked)} />
              Disabled (skip during bulk runs)
            </label>
          )}
          {err && <div className="text-sm text-[var(--color-destructive)]">{err}</div>}
        </div>
      )}

      {isEdit && tab === "yaml" && (
        <div className="space-y-2">
          <div className="text-xs text-[var(--color-muted-foreground)]">
            Edit the playbook as raw YAML. Saving will re-parse the YAML into the playbook model.
          </div>
          <div className="border border-[var(--color-border)] rounded overflow-hidden">
            {yamlLoading
              ? <div className="p-4 text-sm text-[var(--color-muted-foreground)]">Loading YAML…</div>
              : <YamlEditor value={yamlText} onChange={setYamlText} height={460} />}
          </div>
          {err && <div className="text-sm text-[var(--color-destructive)]">{err}</div>}
        </div>
      )}
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1">{label}</label>
      {children}
    </div>
  );
}

function PreviewDialog({ playbook, onClose }: { playbook: Playbook; onClose: () => void }) {
  const [yaml, setYaml] = useState<string>("# Loading…");
  const [validation, setValidation] = useState<any>(null);

  useEffect(() => {
    (async () => {
      try {
        const full = await api<{ success: boolean; playbook: Playbook }>(
          "GET", `/api/projects/_current/playbooks/${playbook.id}`,
        );
        const res = await api<{ success: boolean; yaml: string }>(
          "POST", `/api/projects/_current/playbooks/${playbook.id}/preview`,
          { playbook: (full as any).playbook ?? full },
        );
        setYaml(res.yaml || "# (empty)");
      } catch (e) {
        setYaml(`# Error: ${(e as Error).message}`);
      }
      try {
        const v = await api("POST", `/api/projects/_current/playbooks/${playbook.id}/validate`);
        setValidation(v);
      } catch { /* ignore */ }
    })();
  }, [playbook.id]);

  return (
    <Modal open onClose={onClose} wide title={`Preview YAML — ${playbook.name}`}
      footer={<Button onClick={onClose}>Close</Button>}
    >
      {validation && (
        <div className="mb-3 text-xs flex items-center gap-2">
          {(validation as any)?.summary?.playbook_can_run ? (
            <span className="inline-flex items-center gap-1 text-[var(--color-success)]">
              <CheckCircle2 className="h-3.5 w-3.5" /> Validation passed
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-[var(--color-warning)]">
              <AlertCircle className="h-3.5 w-3.5" /> {(validation as any)?.summary?.total_errors ?? 0} validation issue(s)
            </span>
          )}
        </div>
      )}
      <YamlEditor value={yaml} readOnly height={460} />
    </Modal>
  );
}

function RunDialog({ playbook, onClose }: { playbook: Playbook; onClose: () => void }) {
  const [verbosity, setVerbosity] = useState("");
  const [checkMode, setCheckMode] = useState(false);
  const [forks, setForks] = useState<string>("");
  const [forceHandlers, setForceHandlers] = useState(false);
  const [connectionTimeout, setConnectionTimeout] = useState<string>("");
  const [inventoryFiles, setInventoryFiles] = useState<string>("");
  const [ansibleConfig, setAnsibleConfig] = useState<string>("ansible.cfg");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  const runsQuery = useQuery({
    queryKey: ["playbook-runs", playbook.id],
    queryFn: () => api<{ runs?: RunRecord[] } | RunRecord[]>(
      "GET", `/api/projects/_current/playbooks/${playbook.id}/runs`,
    ),
  });
  const runs: RunRecord[] = useMemo(() => {
    const d = runsQuery.data as any;
    return Array.isArray(d?.runs) ? d.runs : Array.isArray(d) ? d : [];
  }, [runsQuery.data]);

  const run = async () => {
    setBusy(true); setErr(null); setResult(null);
    try {
      const body: Record<string, unknown> = {
        ansible_config: ansibleConfig || undefined,
        check_mode: checkMode,
        verbosity: verbosity || undefined,
        force_handlers: forceHandlers,
      };
      if (inventoryFiles.trim()) {
        body.inventory_files = inventoryFiles.split(",").map((s) => s.trim()).filter(Boolean);
      }
      if (forks) body.forks = Number(forks);
      if (connectionTimeout) body.connection_timeout = Number(connectionTimeout);
      const res = await api("POST", `/api/projects/_current/playbooks/${playbook.id}/run`, body);
      setResult(res);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open onClose={onClose} wide title={`Run Playbook — ${playbook.name}`}
      footer={<>
        <Button variant="outline" onClick={onClose} disabled={busy}>Close</Button>
        <Button onClick={run} disabled={busy}>
          <Play className="h-4 w-4 mr-1.5" /> {busy ? "Queueing…" : "Run Playbook"}
        </Button>
      </>}
    >
      <div className="grid grid-cols-2 gap-4">
        <Field label="Inventory files (comma-separated, optional)">
          <Input value={inventoryFiles} onChange={(e) => setInventoryFiles(e.target.value)} placeholder="inventory.yml, inventories/prod.yml" />
        </Field>
        <Field label="Ansible config">
          <Input value={ansibleConfig} onChange={(e) => setAnsibleConfig(e.target.value)} placeholder="ansible.cfg" />
        </Field>
        <Field label="Verbosity">
          <select
            className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-2 text-sm"
            value={verbosity} onChange={(e) => setVerbosity(e.target.value)}
          >
            <option value="">Normal</option>
            <option value="-v">-v</option>
            <option value="-vv">-vv</option>
            <option value="-vvv">-vvv</option>
            <option value="-vvvv">-vvvv (debug)</option>
          </select>
        </Field>
        <Field label="Forks">
          <Input type="number" value={forks} onChange={(e) => setForks(e.target.value)} placeholder="5" />
        </Field>
        <Field label="Connection timeout (s)">
          <Input type="number" value={connectionTimeout} onChange={(e) => setConnectionTimeout(e.target.value)} placeholder="30" />
        </Field>
        <div className="space-y-2 pt-5">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={checkMode} onChange={(e) => setCheckMode(e.target.checked)} />
            Check mode (dry run)
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={forceHandlers} onChange={(e) => setForceHandlers(e.target.checked)} />
            Force handlers
          </label>
        </div>
      </div>

      {err && <div className="mt-3 text-sm text-[var(--color-destructive)]">{err}</div>}
      {result && (
        <div className="mt-3 p-3 rounded-md bg-[var(--color-accent)]/30 text-xs font-mono whitespace-pre-wrap break-all">
          {JSON.stringify(result, null, 2)}
        </div>
      )}

      <div className="mt-5">
        <div className="text-xs font-semibold uppercase tracking-wider text-[var(--color-muted-foreground)] mb-2">
          Recent Runs ({runs.length})
        </div>
        <div className="max-h-48 overflow-y-auto border border-[var(--color-border)] rounded-md">
          {runs.length === 0 && (
            <div className="px-3 py-4 text-center text-xs text-[var(--color-muted-foreground)]">No runs yet.</div>
          )}
          {runs.slice(0, 20).map((r, i) => (
            <div key={r.run_id || r.id || i} className="flex justify-between px-3 py-2 border-b border-[var(--color-border)] last:border-b-0 text-xs">
              <span className="font-mono truncate">{r.run_id || r.id}</span>
              <span className={
                r.status === "success" ? "text-[var(--color-success)]" :
                r.status === "failed" ? "text-[var(--color-destructive)]" :
                "text-[var(--color-muted-foreground)]"
              }>{r.status || "—"}</span>
              <span className="text-[var(--color-muted-foreground)]">{fmtDate(r.started_at)}</span>
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}
