import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { FolderKanban, Cloud, BookOpen, Plus, ArrowRight, Activity, Layers } from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge, statusToVariant } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { useProjects } from "@/lib/project";
import { NewProjectDialog } from "@/components/project/NewProjectDialog";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/dashboard")({ component: Dashboard });

type StackRow = { name: string; provider?: string; last_status?: string; updated_at?: number };
type Run = { run_id: string; stack: string; action: string; status: string; mtime?: number };
type Playbook = { id: string; name?: string };

function Dashboard() {
  const t = useT();
  const { projects, current, currentId, loading: projectsLoading } = useProjects();
  const [createOpen, setCreateOpen] = useState(false);

  const stacksQ = useQuery({
    queryKey: ["cloud", "stacks", currentId],
    queryFn: () => api<{ stacks: StackRow[] }>("GET", "/api/cloud/stacks"),
    enabled: !!currentId,
  });
  const runsQ = useQuery({
    queryKey: ["cloud", "runs", currentId],
    queryFn: () => api<{ runs: Run[] }>("GET", "/api/cloud/runs"),
    enabled: !!currentId,
    refetchInterval: 8000,
  });
  const playbooksQ = useQuery({
    queryKey: ["ansible", "playbooks", currentId],
    queryFn: () => api<{ playbooks?: Playbook[] } | Playbook[]>("GET", "/api/projects/_current/playbooks"),
    enabled: !!currentId,
  });

  const stacks = stacksQ.data?.stacks ?? [];
  const runs = runsQ.data?.runs ?? [];
  const playbooks = Array.isArray(playbooksQ.data)
    ? playbooksQ.data
    : (playbooksQ.data?.playbooks ?? []);

  const activeProjects = projects.filter(p => !p.archived && !p.isArchived).length;
  const runningCount = runs.filter(r => r.status === "running" || r.status === "queued").length;

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: t("nav.homeDashboard") }]} />

      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">{t("page.home.title")}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)] mt-1">{t("page.home.subtitle")}</p>
        </div>
        <Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> {t("common.newProject")}</Button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          icon={<FolderKanban className="h-5 w-5" />}
          label={t("page.home.totalProjects")}
          value={projectsLoading ? "…" : activeProjects}
          hint={current ? t("page.home.activeProject", { name: current.name }) : t("page.home.noActiveProject")}
          tint="primary"
        />
        <StatCard
          icon={<Cloud className="h-5 w-5" />}
          label={t("page.home.cloudProvisioning")}
          value={stacksQ.isLoading ? "…" : stacks.length}
          hint={t("page.home.opentofuStacks")}
          tint="success"
          to="/cloud/stacks"
        />
        <StatCard
          icon={<BookOpen className="h-5 w-5" />}
          label={t("page.home.infrastructure")}
          value={playbooksQ.isLoading ? "…" : playbooks.length}
          hint={t("page.home.ansiblePlaybooks")}
          tint="accent"
        />
        <StatCard
          icon={<Activity className="h-5 w-5" />}
          label={t("page.home.activeRuns")}
          value={runsQ.isLoading ? "…" : runningCount}
          hint={t("page.home.totalInHistory", { count: runs.length })}
          tint="warning"
          to="/cloud/summary"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2"><Layers className="h-4 w-4" /> {t("page.home.recentStacks")}</CardTitle>
            <Link to="/cloud/stacks" className="text-xs text-[var(--color-primary)] hover:underline inline-flex items-center gap-1">
              {t("page.home.viewAll")} <ArrowRight className="h-3 w-3" />
            </Link>
          </CardHeader>
          <CardContent className="p-0">
            {stacks.length === 0 ? (
              <EmptyRow label={t("page.home.noStacks")} cta={<Link to="/cloud/stacks/new" className="text-[var(--color-primary)] hover:underline">{t("page.home.createStack")}</Link>} />
            ) : (
              <ul className="divide-y divide-[var(--color-border)]">
                {stacks.slice(0, 6).map(s => (
                  <li key={s.name} className="flex items-center gap-3 px-4 py-2.5">
                    <div className="h-8 w-8 rounded-md bg-[var(--color-primary)]/10 text-[var(--color-primary)] flex items-center justify-center shrink-0">
                      <Cloud className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium truncate">{s.name}</div>
                      <div className="text-xs text-[var(--color-muted-foreground)] truncate">{s.provider ?? "—"}</div>
                    </div>
                    {s.last_status && <Badge variant={statusToVariant(s.last_status)} className="shrink-0">{s.last_status}</Badge>}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2"><FolderKanban className="h-4 w-4" /> {t("page.home.yourProjects")}</CardTitle>
            <button onClick={() => setCreateOpen(true)} className="text-xs text-[var(--color-primary)] hover:underline inline-flex items-center gap-1">
              <Plus className="h-3 w-3" /> {t("page.home.new")}
            </button>
          </CardHeader>
          <CardContent className="p-0">
            {projects.length === 0 ? (
              <EmptyRow label={t("page.home.noProjects")} cta={<button onClick={() => setCreateOpen(true)} className="text-[var(--color-primary)] hover:underline">{t("page.home.createFirstProject")}</button>} />
            ) : (
              <ul className="divide-y divide-[var(--color-border)]">
                {projects.slice(0, 6).map(p => (
                  <li key={p.id} className="flex items-center gap-3 px-4 py-2.5">
                    <div className="h-8 w-8 rounded-md bg-[var(--color-muted)] flex items-center justify-center shrink-0 text-xs font-semibold">
                      {p.name.slice(0, 2).toUpperCase()}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium truncate">{p.name}</div>
                      {p.description && <div className="text-xs text-[var(--color-muted-foreground)] truncate">{p.description}</div>}
                    </div>
                    {p.id === currentId && <Badge variant="success" className="shrink-0">{t("common.active")}</Badge>}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <NewProjectDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  );
}

function EmptyRow({ label, cta }: { label: string; cta: React.ReactNode }) {
  return (
    <div className="px-4 py-10 text-center text-sm text-[var(--color-muted-foreground)]">
      <div className="mb-2">{label}</div>
      <div className="text-xs">{cta}</div>
    </div>
  );
}

function StatCard({
  icon, label, value, hint, tint, to,
}: {
  icon: React.ReactNode; label: string; value: number | string; hint?: string;
  tint: "primary" | "success" | "accent" | "warning"; to?: string;
}) {
  const tints: Record<string, string> = {
    primary: "bg-[var(--color-muted)] text-[var(--color-foreground)]",
    success: "bg-[var(--color-muted)] text-[var(--color-foreground)]",
    accent: "bg-[var(--color-muted)] text-[var(--color-foreground)]",
    warning: "bg-[var(--color-muted)] text-[var(--color-foreground)]",
  };
  const inner = (
    <Card className="h-full hover:border-[var(--color-primary)]/40 transition-colors">
      <CardContent className="p-5">
        <div className="flex items-start justify-between gap-2">
          <div className={`h-10 w-10 rounded-xl flex items-center justify-center ${tints[tint]}`}>{icon}</div>
          {to && <ArrowRight className="h-4 w-4 text-[var(--color-muted-foreground)]" />}
        </div>
        <div className="mt-4 text-3xl font-bold tracking-tight">{value}</div>
        <div className="text-sm font-medium mt-0.5">{label}</div>
        {hint && <div className="text-xs text-[var(--color-muted-foreground)] mt-1 truncate">{hint}</div>}
      </CardContent>
    </Card>
  );
  return to ? <Link to={to} className="block">{inner}</Link> : inner;
}
