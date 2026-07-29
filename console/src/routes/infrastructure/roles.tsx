import { createFileRoute } from "@tanstack/react-router";
import { Shield } from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { RolesPanel } from "@/components/infrastructure/RolesPanel";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/infrastructure/roles")({ component: RolesPage });

function RolesPage() {
  const t = useT();
  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "Infrastructure" }, { label: "Roles", icon: <Shield className="h-3.5 w-3.5" /> }]} />
      <div>
        <h1 className="text-2xl font-semibold">{t("page.roles.title")}</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">{t("page.roles.subtitle")}</p>
      </div>
      <RolesPanel />
    </div>
  );
}
