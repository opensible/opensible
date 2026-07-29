import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X, Cloud, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

type SecretOption = { id: string; name: string; meta?: { username?: string; fingerprint?: string } };
type EntityPaths = { envs?: string; modules?: string; scripts?: string };
type StackSource = {
  mode?: string;
  syncDirection?: string;
  entityPaths?: EntityPaths;
  git?: {
    repo?: string;
    ref?: string;
    subdir?: string;
    authSecretId?: string;
  };
};

type Props = {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  projectId: string | null;
};

export function EditStackSourceDialog({ open, onOpenChange, projectId }: Props) {
  const qc = useQueryClient();

  const sourcesQ = useQuery({
    enabled: open && !!projectId,
    queryKey: ["project-sources", projectId],
    queryFn: () =>
      api<{ success: boolean; sources: Record<string, StackSource> }>(
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

  const stacks = sourcesQ.data?.sources?.stacks || {};
  const [repo, setRepo] = useState("");
  const [ref, setRef] = useState("main");
  const [subdir, setSubdir] = useState("");
  const [authSecretId, setAuthSecretId] = useState("");
  const [syncDirection, setSyncDirection] = useState("none");
  const [envs, setEnvs] = useState("envs");
  const [modules, setModules] = useState("modules");
  const [scripts, setScripts] = useState("scripts");

  function validateForm() {
    if (!repo.trim()) {
      toast.error("Repository URL is required");
      return false;
    }
    if (!ref.trim()) {
      toast.error("Branch/Tag/Commit is required");
      return false;
    }
    const subdirValue = subdir.trim();
    if (subdirValue && (subdirValue.includes("..") || subdirValue.startsWith("/") || (subdirValue.length >= 2 && subdirValue[1] === ":"))) {
      toast.error("Subdirectory must be a safe relative path");
      return false;
    }
    const pathValues = [
      ["Environments path", envs],
      ["Modules path", modules],
      ["Scripts path", scripts],
    ] as const;
    for (const [label, value] of pathValues) {
      const v = value.trim();
      if (!v) continue;
      if (v.includes("..") || v.startsWith("/") || v.endsWith("/") || (v.length >= 2 && v[1] === ":")) {
        toast.error(`${label} must be a safe relative path`);
        return false;
      }
    }
    return true;
  }

  function buildSourceConfig() {
    return {
      mode: "git",
      localPath: "stacks",
      git: {
        repo: repo.trim(),
        ref: ref.trim() || "main",
        subdir: subdir.trim() || "",
        authSecretId: authSecretId || null,
      },
      syncDirection,
      entityPaths: {
        envs: envs.trim() || "envs",
        modules: modules.trim() || "modules",
        scripts: scripts.trim() || "scripts",
      },
    };
  }

  function normalizeSourcesForSave(sources: Record<string, any>) {
    const next: Record<string, any> = { ...sources };
    const defaultLocalPaths: Record<string, string> = { repo: "repo", stacks: "stacks" };

    for (const [key, defaultPath] of Object.entries(defaultLocalPaths)) {
      const source = next[key];
      if (!source) {
        next[key] = { mode: "local", localPath: defaultPath };
        continue;
      }
      if ((source.mode || "local") === "local" && !String(source.localPath || "").trim()) {
        next[key] = { ...source, mode: "local", localPath: defaultPath };
      }
    }

    return next;
  }

  useEffect(() => {
    if (!open || !sourcesQ.data) return;
    const s = sourcesQ.data.sources?.stacks || {};
    setRepo(s.git?.repo || "");
    setRef(s.git?.ref || "main");
    setSubdir(s.git?.subdir || "");
    setAuthSecretId(s.git?.authSecretId || "");
    setSyncDirection(s.syncDirection || "none");
    setEnvs(s.entityPaths?.envs || "envs");
    setModules(s.entityPaths?.modules || "modules");
    setScripts(s.entityPaths?.scripts || "scripts");
  }, [open, sourcesQ.data]);

  const save = useMutation({
    mutationFn: async () => {
      if (!projectId) throw new Error("No project selected");
      if (!validateForm()) throw new Error("Please fix validation errors before saving");
      const sourceConfig = buildSourceConfig();

      try {
        const analysis = await api<{ success: boolean; hasErrors?: boolean; breakingChange?: boolean; issues?: Array<{ type: string; message: string }> }>(
          "POST",
          `/api/projects/${encodeURIComponent(projectId)}/sources/analyze`,
          { sourceKey: "stacks", config: sourceConfig }
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
        console.warn("[EditStackSourceDialog] Source impact analysis failed; continuing with save", e);
      }

      const latest = await api<{ success: boolean; sources?: Record<string, StackSource> }>(
        "GET",
        `/api/projects/${encodeURIComponent(projectId)}/sources`
      );
      const current = latest.sources || sourcesQ.data?.sources || {};
      const next = normalizeSourcesForSave(current);
      next.stacks = {
        ...(current.stacks || {}),
        ...sourceConfig,
      };
      return api("PUT", `/api/projects/${encodeURIComponent(projectId)}/sources`, { sources: next, skipValidation: false });
    },
    onSuccess: () => {
      toast.success("Stack source updated");
      qc.invalidateQueries({ queryKey: ["project-sources", projectId] });
      qc.invalidateQueries({ queryKey: ["stacks-sync-state", projectId] });
      onOpenChange(false);
    },
    onError: (e: any) => toast.error(e?.message || "Failed to save"),
  });

  const test = useMutation({
    mutationFn: async () => {
      if (!projectId) throw new Error("No project selected");
      if (!validateForm()) throw new Error("Please fix validation errors before testing");
      return api("POST", `/api/projects/${encodeURIComponent(projectId)}/sources/test`, {
        sourceKey: "stacks",
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
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40"
      onClick={() => !busy && onOpenChange(false)}
    >
      <div
        className="w-full max-w-xl rounded-2xl bg-[var(--color-card)] border border-[var(--color-border)] shadow-xl max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-border)]">
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-[var(--color-primary)]/10 flex items-center justify-center">
              <Cloud className="h-4 w-4 text-[var(--color-primary)]" />
            </div>
            <div>
              <div className="text-sm font-semibold">Edit Source: Stacks Workspace</div>
              <div className="text-xs text-[var(--color-muted-foreground)]">
                Git source configuration for Cloud Provisioning.
              </div>
            </div>
          </div>
          <button
            className="h-8 w-8 inline-flex items-center justify-center rounded-md hover:bg-[var(--color-muted)]"
            onClick={() => onOpenChange(false)}
            aria-label="Close"
          >
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
                <Input
                  value={repo}
                  onChange={(e) => setRepo(e.target.value)}
                  placeholder="https://github.com/user/repo.git or git@github.com:user/repo.git"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1.5">
                  Branch/Tag/Commit <span className="text-[var(--color-destructive)]">*</span>
                </label>
                <Input
                  value={ref}
                  onChange={(e) => setRef(e.target.value)}
                  placeholder="main, master, v1.0.0, or commit hash"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1.5">
                  Subdirectory (optional)
                </label>
                <Input
                  value={subdir}
                  onChange={(e) => setSubdir(e.target.value)}
                  placeholder="e.g. IaC/opentofu-bytedc"
                />
                <div className="text-[11px] text-[var(--color-muted-foreground)] mt-1">
                  If empty: syncs the entire repository. If specified: only that subdirectory is synced.
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1.5">
                  Authentication Secret (optional)
                </label>
                <select
                  className="w-full h-10 px-3 rounded-md border border-[var(--color-border)] bg-[var(--color-input,white)] text-sm"
                  value={authSecretId}
                  onChange={(e) => setAuthSecretId(e.target.value)}
                >
                  <option value="">None (anonymous)</option>
                  {secrets.map((s) => {
                    const u = s.meta?.username || "git";
                    const fp = s.meta?.fingerprint ? ` • ${s.meta.fingerprint.slice(0, 16)}…` : "";
                    return (
                      <option key={s.id} value={s.id}>
                        {s.name} ({u}{fp})
                      </option>
                    );
                  })}
                </select>
                <div className="text-[11px] text-[var(--color-muted-foreground)] mt-1">
                  SSH key or token for private repositories.
                </div>
              </div>

              <div className="pt-4 border-t border-[var(--color-border)]">
                <label className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1.5">
                  Source Binding (Sync Direction)
                </label>
                <select
                  className="w-full h-10 px-3 rounded-md border border-[var(--color-border)] bg-[var(--color-input,white)] text-sm"
                  value={syncDirection}
                  onChange={(e) => setSyncDirection(e.target.value)}
                >
                  <option value="none">None (no sync binding)</option>
                  <option value="push">Push only (Project Storage → Git)</option>
                  <option value="pull">Pull only (Git → Project Storage)</option>
                  <option value="both">Bidirectional (both directions)</option>
                </select>
              </div>

              <div className="pt-4 border-t border-[var(--color-border)]">
                <div className="text-xs font-medium mb-2">
                  OpenTofu Entity Paths <span className="text-[var(--color-muted-foreground)]">(Advanced)</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div>
                    <label className="block text-[11px] text-[var(--color-muted-foreground)] mb-1">Environments</label>
                    <Input value={envs} onChange={(e) => setEnvs(e.target.value)} placeholder="envs" />
                  </div>
                  <div>
                    <label className="block text-[11px] text-[var(--color-muted-foreground)] mb-1">Modules</label>
                    <Input value={modules} onChange={(e) => setModules(e.target.value)} placeholder="modules" />
                  </div>
                  <div>
                    <label className="block text-[11px] text-[var(--color-muted-foreground)] mb-1">Scripts</label>
                    <Input value={scripts} onChange={(e) => setScripts(e.target.value)} placeholder="scripts" />
                  </div>
                </div>
                <div className="text-[11px] text-[var(--color-muted-foreground)] mt-2">
                  Paths are relative to repository root (or subdirectory if specified).
                </div>
              </div>
            </>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-3 border-t border-[var(--color-border)] bg-[var(--color-muted)]/30">
          <Button variant="outline" onClick={() => test.mutate()} disabled={busy || test.isPending || !repo.trim()}>
            {test.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Test Connection
          </Button>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={() => save.mutate()} disabled={busy || !repo.trim()}>
            {busy && <Loader2 className="h-4 w-4 animate-spin" />} Save
          </Button>
        </div>
      </div>
    </div>
  );
}
