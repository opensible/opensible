import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch, ArrowDown, ArrowUp, Pencil, Globe } from "lucide-react";
import { toast } from "sonner";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { runGitSync } from "@/lib/git-sync";
import { formatSyncTime } from "@/lib/format-time";
import { useProjects } from "@/lib/project";
import { EditRepoSourceDialog } from "@/components/infrastructure/EditRepoSourceDialog";
import { HostStatusCacheCard } from "@/components/infrastructure/HostStatusCacheCard";
import { GitAutosyncCard } from "@/components/project/GitAutosyncCard";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/settings")({ component: InfraSettingsPage });

type RepoSource = {
  mode?: string;
  git?: { repo?: string; ref?: string; subdir?: string; authSecretId?: string };
  syncDirection?: string;
  syncState?: {
    lastPushAt?: number | string | null;
    lastPullAt?: number | string | null;
    lastPushStatus?: string;
    lastPullStatus?: string;
    lastPushError?: string | null;
    lastPullError?: string | null;
  };
};

function InfraSettingsPage() {
  const t = useT();
  const { currentId } = useProjects();
  const qc = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);

  const sourcesQ = useQuery({
    enabled: !!currentId,
    queryKey: ["project-sources", currentId],
    queryFn: () =>
      api<{ success: boolean; sources: Record<string, RepoSource> }>(
        "GET",
        `/api/projects/${encodeURIComponent(currentId!)}/sources`,
      ),
  });

  const repo: RepoSource = sourcesQ.data?.sources?.repo || {};
  const isGit = (repo.mode || "local") === "git";
  const repoUrl = repo.git?.repo || "—";
  const branch = repo.git?.ref || "main";

  async function sync(direction: "pull" | "push") {
    if (!currentId) return;
    setBusy(direction);
    const tid = toast.loading(`Git ${direction} in progress…`);
    try {
      const res = await runGitSync(currentId, "repo", direction);
      if (res.ok) {
        toast.success(`Git ${direction} completed`, { id: tid });
      } else {
        toast.error(`Git ${direction} failed: ${res.error}`, { id: tid });
      }
    } catch (e: any) {
      toast.error(e?.message || `Git ${direction} failed`, { id: tid });
    } finally {
      setBusy(null);
      qc.invalidateQueries({ queryKey: ["project-sources", currentId] });
    }
  }

  const push = repo.syncState?.lastPushStatus || "idle";
  const pull = repo.syncState?.lastPullStatus || "idle";

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: "Project Settings" }]} />
      <div>
        <h1 className="text-2xl font-bold">{t("page.settings.title")}</h1>
        <p className="text-sm text-[var(--color-muted-foreground)] mt-1">{t("page.settings.subtitle")}</p>
      </div>

      <Card>
        <CardHeader className="!flex-row items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <CardTitle className="flex items-center gap-2">
              <GitBranch className="h-5 w-5" /> Repository Workspace (Ansible)
            </CardTitle>
            <p className="text-sm mt-2 font-mono truncate">{repoUrl} @ {branch}</p>
            {repo.git?.subdir && (
              <p className="text-xs text-[var(--color-muted-foreground)] mt-1 font-mono">subdir: {repo.git.subdir}</p>
            )}
            <p className="text-xs text-[var(--color-muted-foreground)] mt-1">
              Git-synced workspace containing roles, playbooks, inventories, ansible.cfg, etc.
            </p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <Badge variant={isGit ? "primary" : "default"}>{isGit ? "Git" : (repo.mode || "local")}</Badge>
            <Button variant="outline" size="sm" onClick={() => setEditOpen(true)} disabled={!currentId}>
              <Pencil className="h-4 w-4" /> Edit
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 border-l-2 border-[var(--color-primary)] pl-4">
            <div>
              <div className="text-xs uppercase tracking-wider text-[var(--color-muted-foreground)] mb-1">Push:</div>
              <Badge variant={push === "success" || push === "ok" ? "success" : "default"}>● {push}</Badge>
              {repo.syncState?.lastPushAt != null && <div className="text-xs mt-1">{formatSyncTime(repo.syncState.lastPushAt)}</div>}
            </div>
            <div>
              <div className="text-xs uppercase tracking-wider text-[var(--color-muted-foreground)] mb-1">Pull:</div>
              <Badge variant={pull === "success" || pull === "ok" ? "success" : "default"}>● {pull}</Badge>
              {repo.syncState?.lastPullAt != null && <div className="text-xs mt-1">{formatSyncTime(repo.syncState.lastPullAt)}</div>}
            </div>
          </div>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="outline" size="sm" onClick={() => sync("push")} disabled={busy === "push" || !currentId}>
              <ArrowUp className="h-4 w-4" /> Push
            </Button>
            <Button variant="outline" size="sm" onClick={() => sync("pull")} disabled={busy === "pull" || !currentId}>
              <ArrowDown className="h-4 w-4" /> Pull
            </Button>
          </div>
        </CardContent>
      </Card>

      <GitAutosyncCard projectId={currentId} sourceKey="repo" sourceLabel="repository" />

      <HostStatusCacheCard projectId={currentId} />


      <EditRepoSourceDialog open={editOpen} onOpenChange={setEditOpen} projectId={currentId} />
    </div>
  );
}
