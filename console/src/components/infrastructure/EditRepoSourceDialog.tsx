import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X, GitBranch, Loader2, Plus, ChevronDown, ChevronUp } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

type SecretOption = { id: string; name: string; meta?: { username?: string; fingerprint?: string } };
type RepoSource = {
  mode?: string;
  syncDirection?: string;
  git?: { repo?: string; ref?: string; subdir?: string; authSecretId?: string };
};
type RepoLayout = { playbooks?: string; roles?: string; inventories?: string; vars?: string };

type Props = {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  projectId: string | null;
};

const ENTITY_FIELDS: Array<{ key: keyof RepoLayout; label: string; default: string }> = [
  { key: "playbooks", label: "Playbooks Path", default: "playbooks" },
  { key: "roles", label: "Roles Path", default: "roles" },
  { key: "inventories", label: "Inventories Path", default: "inventories" },
  { key: "vars", label: "Vars Path", default: "vars" },
];

export function EditRepoSourceDialog({ open, onOpenChange, projectId }: Props) {
  const qc = useQueryClient();

  const sourcesQ = useQuery({
    enabled: open && !!projectId,
    queryKey: ["project-sources", projectId],
    queryFn: () =>
      api<{ success: boolean; sources: Record<string, RepoSource>; repoLayout?: RepoLayout }>(
        "GET",
        `/api/projects/${encodeURIComponent(projectId!)}/sources`
      ),
  });

  const secretsQ = useQuery({
    enabled: open,
    queryKey: ["git-secrets-options"],
    queryFn: () =>
      api<{ success: boolean; options: SecretOption[] }>("GET", "/api/global/secrets/options?purpose=git"),
  });

  const [repo, setRepo] = useState("");
  const [ref, setRef] = useState("main");
  const [subdir, setSubdir] = useState("");
  const [authSecretId, setAuthSecretId] = useState("");
  const [syncDirection, setSyncDirection] = useState("none");
  const [layout, setLayout] = useState<RepoLayout>({});
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    if (!open || !sourcesQ.data) return;
    const s = sourcesQ.data.sources?.repo || {};
    setRepo(s.git?.repo || "");
    setRef(s.git?.ref || "main");
    setSubdir(s.git?.subdir || "");
    setAuthSecretId(s.git?.authSecretId || "");
    setSyncDirection(s.syncDirection || "none");
    setLayout(sourcesQ.data.repoLayout || {});
  }, [open, sourcesQ.data]);

  function validateForm() {
    if (!repo.trim()) { toast.error("Repository URL is required"); return false; }
    if (!ref.trim()) { toast.error("Branch/Tag/Commit is required"); return false; }
    const sv = subdir.trim();
    if (sv && (sv.includes("..") || sv.startsWith("/") || (sv.length >= 2 && sv[1] === ":"))) {
      toast.error("Subdirectory must be a safe relative path"); return false;
    }
    return true;
  }

  function buildSourceConfig() {
    return {
      mode: "git",
      localPath: "repo",
      git: {
        repo: repo.trim(),
        ref: ref.trim() || "main",
        subdir: subdir.trim() || "",
        authSecretId: authSecretId || null,
      },
      syncDirection,
    };
  }

  function buildRepoLayout(): RepoLayout {
    const out: RepoLayout = {};
    for (const f of ENTITY_FIELDS) {
      const v = (layout[f.key] || "").toString().trim();
      out[f.key] = v || f.default;
    }
    return out;
  }

  function normalizeSourcesForSave(sources: Record<string, any>) {
    const next: Record<string, any> = { ...sources };
    const defaults: Record<string, string> = { repo: "repo", stacks: "stacks" };
    for (const [key, defaultPath] of Object.entries(defaults)) {
      const s = next[key];
      if (!s) { next[key] = { mode: "local", localPath: defaultPath }; continue; }
      if ((s.mode || "local") === "local" && !String(s.localPath || "").trim()) {
        next[key] = { ...s, mode: "local", localPath: defaultPath };
      }
    }
    return next;
  }

  const save = useMutation({
    mutationFn: async () => {
      if (!projectId) throw new Error("No project selected");
      if (!validateForm()) throw new Error("Please fix validation errors before saving");
      const sourceConfig = buildSourceConfig();
      try {
        const analysis = await api<{ success: boolean; hasErrors?: boolean; breakingChange?: boolean; issues?: Array<{ type: string; message: string }> }>(
          "POST",
          `/api/projects/${encodeURIComponent(projectId)}/sources/analyze`,
          { sourceKey: "repo", config: sourceConfig }
        );
        if (analysis.success && analysis.breakingChange && analysis.issues?.length) {
          const message = analysis.issues.map((i) => `${i.type.toUpperCase()}: ${i.message}`).join("\n");
          if (!window.confirm(`${message}\n\nSave source configuration anyway?`)) {
            throw new Error("Save cancelled");
          }
        } else if (analysis.success && analysis.hasErrors) {
          const message = analysis.issues?.filter((i) => i.type === "error").map((i) => i.message).join("\n") || "Cannot save source configuration";
          throw new Error(message);
        }
      } catch (e: any) {
        if (e?.message === "Save cancelled") throw e;
        console.warn("[EditRepoSourceDialog] analyze failed; continuing", e);
      }
      const latest = await api<{ success: boolean; sources?: Record<string, RepoSource>; repoLayout?: RepoLayout }>(
        "GET",
        `/api/projects/${encodeURIComponent(projectId)}/sources`
      );
      const current = latest.sources || sourcesQ.data?.sources || {};
      const next = normalizeSourcesForSave(current);
      next.repo = { ...(current.repo || {}), ...sourceConfig };
      return api("PUT", `/api/projects/${encodeURIComponent(projectId)}/sources`, {
        sources: next,
        repoLayout: buildRepoLayout(),
        skipValidation: false,
      });
    },
    onSuccess: () => {
      toast.success("Repository source updated");
      qc.invalidateQueries({ queryKey: ["project-sources", projectId] });
      onOpenChange(false);
    },
    onError: (e: any) => toast.error(e?.message || "Failed to save"),
  });

  const test = useMutation({
    mutationFn: async () => {
      if (!projectId) throw new Error("No project selected");
      if (!validateForm()) throw new Error("Please fix validation errors before testing");
      return api("POST", `/api/projects/${encodeURIComponent(projectId)}/sources/test`, {
        sourceKey: "repo",
        config: buildSourceConfig(),
      });
    },
    onSuccess: () => toast.success("Connection OK"),
    onError: (e: any) => toast.error(e?.message || "Connection failed"),
  });

  if (!open) return null;
  const busy = save.isPending;
  const secrets = secretsQ.data?.options || [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40" onClick={() => !busy && onOpenChange(false)}>
      <div className="w-full max-w-xl rounded-2xl bg-[var(--color-card)] border border-[var(--color-border)] shadow-xl max-h-[90vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-border)]">
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-[var(--color-primary)]/10 flex items-center justify-center">
              <GitBranch className="h-4 w-4 text-[var(--color-primary)]" />
            </div>
            <div>
              <div className="text-sm font-semibold">Edit Source: Repository Workspace</div>
              <div className="text-xs text-[var(--color-muted-foreground)]">
                Git source for Ansible roles, playbooks, inventories, and ansible.cfg.
              </div>
            </div>
          </div>
          <button className="h-8 w-8 inline-flex items-center justify-center rounded-md hover:bg-[var(--color-muted)]" onClick={() => onOpenChange(false)} aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4 overflow-y-auto">
          {sourcesQ.isLoading ? (
            <div className="text-sm text-[var(--color-muted-foreground)] flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading…
            </div>
          ) : (
            <>
              <div>
                <label className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1.5">
                  Repository URL <span className="text-[var(--color-destructive)]">*</span>
                </label>
                <Input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="https://github.com/user/repo.git or git@github.com:user/repo.git" />
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1.5">
                  Branch/Tag/Commit <span className="text-[var(--color-destructive)]">*</span>
                </label>
                <Input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="main, master, v1.0.0, or commit hash" />
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1.5">Subdirectory (optional)</label>
                <Input value={subdir} onChange={(e) => setSubdir(e.target.value)} placeholder="e.g., inventories, roles (leave empty to sync entire repo/)" />
                <div className="text-[11px] text-[var(--color-muted-foreground)] mt-1">
                  If empty: syncs entire repo/ workspace (roles, playbooks, inventories, ansible.cfg, etc.). If specified: syncs only the specified subdirectory (e.g., "inventories" or "roles").
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1.5">Authentication Secret (optional)</label>
                <select className="w-full h-10 px-3 rounded-md border border-[var(--color-border)] bg-[var(--color-input,white)] text-sm" value={authSecretId} onChange={(e) => setAuthSecretId(e.target.value)}>
                  <option value="">None (anonymous)</option>
                  {secrets.map((s) => {
                    const u = s.meta?.username || "git";
                    const fp = s.meta?.fingerprint ? ` • ${s.meta.fingerprint.slice(0, 16)}…` : "";
                    return <option key={s.id} value={s.id}>{s.name} ({u}{fp})</option>;
                  })}
                </select>
                <div className="flex items-center gap-2 mt-2">
                  <div className="flex-1 text-[11px] text-[var(--color-muted-foreground)]">SSH key or token for private repositories</div>
                  <a
                    href="/system/secrets"
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-[var(--color-primary)] text-[var(--color-primary)] text-xs font-medium hover:bg-[var(--color-primary)]/10"
                  >
                    <Plus className="h-3 w-3" /> Add new SSH key…
                  </a>
                </div>
              </div>

              <div className="pt-4 border-t border-[var(--color-border)]">
                <label className="block text-sm font-semibold mb-1">Source Binding (Sync Direction)</label>
                <div className="text-[11px] text-[var(--color-muted-foreground)] mb-2">
                  Configure bidirectional synchronization between Project Storage and Git
                </div>
                <select className="w-full h-10 px-3 rounded-md border border-[var(--color-border)] bg-[var(--color-input,white)] text-sm" value={syncDirection} onChange={(e) => setSyncDirection(e.target.value)}>
                  <option value="none">None (no sync binding)</option>
                  <option value="push">Push only (Project Storage → Git)</option>
                  <option value="pull">Pull only (Git → Project Storage)</option>
                  <option value="both">Bidirectional (both directions)</option>
                </select>
                <div className="text-[11px] text-[var(--color-muted-foreground)] mt-2 leading-relaxed">
                  <div><strong>Push:</strong> Copy from Project Storage to Git</div>
                  <div><strong>Pull:</strong> Copy from Git to Project Storage</div>
                  <div><strong>Bidirectional:</strong> Sync both directions (push then pull)</div>
                </div>
              </div>

              <div className="pt-4 border-t border-[var(--color-border)]">
                <button
                  type="button"
                  className="w-full flex items-center justify-between"
                  onClick={() => setShowAdvanced((v) => !v)}
                >
                  <span className="text-sm font-semibold">
                    Ansible Entity Paths <span className="text-[var(--color-muted-foreground)] font-normal text-xs">(Advanced)</span>
                  </span>
                  {showAdvanced ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                </button>
                <div className="text-[11px] text-[var(--color-muted-foreground)] mt-1">
                  Configure custom paths for Ansible entities relative to repository root (or subdir if specified). These paths are used during playbook execution but do not affect Git sync.
                </div>
                {showAdvanced && (
                  <div className="mt-3 grid grid-cols-2 gap-3">
                    {ENTITY_FIELDS.map((f) => (
                      <div key={f.key}>
                        <label className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1.5">{f.label}</label>
                        <Input
                          value={layout[f.key] ?? ""}
                          onChange={(e) => setLayout((p) => ({ ...p, [f.key]: e.target.value }))}
                          placeholder={f.default}
                        />
                      </div>
                    ))}
                    <div className="col-span-2 text-[11px] text-[var(--color-muted-foreground)] leading-relaxed">
                      <strong>Note:</strong> Paths are relative to repository root. If subdirectory is specified, paths are relative to that subdirectory. Use forward slashes (/) for path separators. Empty paths default to entity name.
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-3 border-t border-[var(--color-border)] bg-[var(--color-muted)]/30">
          <Button variant="outline" onClick={() => test.mutate()} disabled={busy || test.isPending || !repo.trim()}>
            {test.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Test Connection
          </Button>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
          <Button onClick={() => save.mutate()} disabled={busy || !repo.trim()}>
            {busy && <Loader2 className="h-4 w-4 animate-spin" />} Save
          </Button>
        </div>
      </div>
    </div>
  );
}
