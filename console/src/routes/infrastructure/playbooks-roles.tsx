import { createFileRoute, useSearch } from "@tanstack/react-router";
import { BookOpen, Shield } from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { PlaybooksPanel } from "@/components/infrastructure/PlaybooksPanel";
import { RolesPanel } from "@/components/infrastructure/RolesPanel";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/infrastructure/playbooks-roles")({
  component: PlaybooksRolesPage,
  validateSearch: (s: Record<string, unknown>): { tab?: Tab } => {
    const t = s.tab;
    return t === "playbooks" || t === "roles" ? { tab: t } : {};
  },
});

type Tab = "playbooks" | "roles";

function PlaybooksRolesPage() {
  const t = useT();
  const search = useSearch({ from: "/infrastructure/playbooks-roles" });
  const navigate = Route.useNavigate();
  const tab: Tab = search.tab ?? "playbooks";
  const setTab = (t: Tab) => navigate({ search: { tab: t } });

  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "Infrastructure" }, { label: t("page.playbooksRoles.title"), icon: <BookOpen className="h-3.5 w-3.5" /> }]} />
      <div>
        <h1 className="text-2xl font-semibold">{t("page.playbooksRoles.title")}</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">{t("page.playbooksRoles.subtitle")}</p>
      </div>

      <div className="flex items-center gap-1 border-b border-[var(--color-border)]">
        {([
          ["playbooks", "Playbooks", BookOpen],
          ["roles", "Roles", Shield],
        ] as const).map(([id, label, Icon]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-2 px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
              tab === id
                ? "border-[var(--color-primary)] text-[var(--color-foreground)] font-medium"
                : "border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
            }`}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
      </div>

      {tab === "playbooks" && <PlaybooksPanel />}
      {tab === "roles" && <RolesPanel />}
    </div>
  );
}
