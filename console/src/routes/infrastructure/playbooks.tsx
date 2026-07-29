import { createFileRoute } from "@tanstack/react-router";
import { BookOpen } from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { PlaybooksPanel } from "@/components/infrastructure/PlaybooksPanel";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/infrastructure/playbooks")({ component: PlaybooksPage });

function PlaybooksPage() {
  const t = useT();
  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "Infrastructure" }, { label: "Playbooks", icon: <BookOpen className="h-3.5 w-3.5" /> }]} />
      <div>
        <h1 className="text-2xl font-semibold">{t("page.playbooks.title")}</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">{t("page.playbooks.subtitle")}</p>
      </div>
      <PlaybooksPanel />
    </div>
  );
}
