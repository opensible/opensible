import { Link } from "@tanstack/react-router";
import { Home, Globe, ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import { useT } from "@/lib/i18n";

export type Crumb = { label: string; to?: string; icon?: ReactNode };

export function Breadcrumbs({ items }: { items: Crumb[] }) {
  const t = useT();
  return (
    <nav className="flex items-center gap-1.5 text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
      <Link to="/" className="flex items-center gap-1 hover:text-[var(--color-foreground)]">
        <Home className="h-3.5 w-3.5" /> {t("common.home")}
      </Link>
      <ChevronRight className="h-3.5 w-3.5" />
      <span className="flex items-center gap-1"><Globe className="h-3.5 w-3.5" /> {t("common.global")}</span>
      {items.map((c, i) => (
        <span key={i} className="flex items-center gap-1.5">
          <ChevronRight className="h-3.5 w-3.5" />
          {c.to ? (
            <Link to={c.to} className="flex items-center gap-1 hover:text-[var(--color-foreground)]">
              {c.icon}{c.label}
            </Link>
          ) : (
            <span className="flex items-center gap-1">{c.icon}{c.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}

