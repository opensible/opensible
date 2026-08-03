import { Link, useLocation } from "@tanstack/react-router";
import {
  Cloud, PieChart, Layers, Plus, Settings2, Server, Shield, KeyRound, BookOpen, ScrollText, FileCode, ChevronsLeft, ChevronsRight, Home, ShieldCheck, Plug, Cpu, Users, Calculator, Rocket, LayoutTemplate, Library, Network, GitBranch, History,
} from "lucide-react";
import logoSvg from "@/assets/opensible-logo.png";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useSidebarCollapsed } from "@/lib/sidebar-state";

type Item = { to: string; label: string; icon: typeof Cloud; disabled?: boolean };

const REMOVED_SIDEBAR_ROUTES = new Set([
  "/infrastructure/playbook-builder",
  "/infrastructure/playbooks/builder",
]);
const REMOVED_SIDEBAR_LABELS = new Set(["Playbook Builder"]);

export function AppSidebar() {
  const t = useT();
  const { pathname } = useLocation();
  const [collapsed, toggle] = useSidebarCollapsed();

  const overview: Item[] = [
    { to: "/dashboard", label: t("nav.homeDashboard"), icon: Home },
  ];
  const infra: Item[] = [
    { to: "/infrastructure/deployment", label: t("nav.deployment"), icon: Rocket },
    { to: "/infrastructure/templates", label: t("nav.jobsTemplates"), icon: Library },
    { to: "/infrastructure/hosts", label: t("nav.hosts"), icon: Network },
    { to: "/infrastructure/playbooks-roles", label: t("nav.playbooksRoles"), icon: BookOpen },
    { to: "/infrastructure/vaults-secrets", label: t("nav.vaultsSecrets"), icon: ShieldCheck },
    { to: "/settings", label: t("nav.projectSettings"), icon: Settings2 },
  ];
  const cloud: Item[] = [
    { to: "/cloud/summary", label: t("nav.summary"), icon: PieChart },
    { to: "/cloud/stacks", label: t("nav.stacks"), icon: Layers },
    { to: "/cloud/stacks/new", label: t("nav.newStack"), icon: Plus },
    { to: "/cloud/cost", label: t("nav.costAnalysis"), icon: Calculator },
    { to: "/cloud/settings", label: t("nav.projectSettings"), icon: Settings2 },
  ];
  const system: Item[] = [
    { to: "/system/settings", label: t("nav.settings"), icon: Settings2 },
    { to: "/system/users", label: t("nav.usersManagement"), icon: Users },
    { to: "/system/workers", label: t("nav.workers"), icon: Cpu },
    { to: "/system/secrets", label: t("nav.secretsManagement"), icon: ShieldCheck },
    { to: "/system/api", label: t("nav.api"), icon: Plug },
  ];

  return (
    <aside
      className={cn(
        "hidden md:flex shrink-0 flex-col text-[var(--color-sidebar-foreground)] bg-[var(--color-sidebar)] transition-[width] duration-200",
        collapsed ? "w-16" : "w-64"
      )}
    >
      {/* Brand */}
      <div className={cn("flex items-center gap-3 h-14 border-b border-[var(--color-sidebar-border)]", collapsed ? "px-3 justify-center" : "px-4")}>
        <div className="h-9 w-9 rounded-full flex items-center justify-center shrink-0">
          <img src={logoSvg} className="h-9 w-9" alt="OpenSible" />
        </div>
        {!collapsed && (
          <div className="min-w-0">
            <div className="font-semibold text-base truncate">{t("app.name")}</div>
          </div>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-4 text-sm custom-scrollbar">
        <Section title={t("nav.overview")} items={overview} pathname={pathname} collapsed={collapsed} />
        <Section title={t("nav.cloud")} items={cloud} pathname={pathname} collapsed={collapsed} />
        <Section title={t("nav.infrastructure")} items={infra} pathname={pathname} collapsed={collapsed} />
        <Section title={t("nav.system")} items={system} pathname={pathname} collapsed={collapsed} />
      </nav>

      {/* Collapse toggle */}
      <button
        onClick={toggle}
        className={cn(
          "flex items-center gap-2 h-11 border-t border-[var(--color-sidebar-border)] text-xs text-[var(--color-sidebar-muted)] hover:text-white hover:bg-[var(--color-sidebar-active-bg)] transition-colors",
          collapsed ? "justify-center px-2" : "px-4"
        )}
        title={collapsed ? t("common.expand") : t("common.collapse")}
      >
        {collapsed ? <ChevronsRight className="h-4 w-4" /> : <ChevronsLeft className="h-4 w-4" />}
        {!collapsed && <span>{t("common.collapse")}</span>}
      </button>
    </aside>
  );
}

function Section({ title, items, pathname, collapsed }: { title: string; items: Item[]; pathname: string; collapsed: boolean }) {
  const visibleItems = items.filter(
    (it) => !REMOVED_SIDEBAR_ROUTES.has(it.to) && !REMOVED_SIDEBAR_LABELS.has(it.label),
  );

  return (
    <div className="mb-5">
      {!collapsed && (
        <div className="px-3 mb-2 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-sidebar-muted)]">{title}</div>
      )}
      <ul className="space-y-0.5">
        {visibleItems.map(it => {
          const matches = pathname === it.to || pathname.startsWith(it.to + "/");
          const hasMoreSpecific = visibleItems.some(
            (other) => other.to !== it.to && other.to.startsWith(it.to + "/") && (pathname === other.to || pathname.startsWith(other.to + "/"))
          );
          const active = matches && !hasMoreSpecific;
          const base = cn(
            "relative flex items-center gap-3 rounded-lg transition-colors",
            collapsed ? "justify-center px-2 py-2.5" : "px-3 py-2.5"
          );
          if (it.disabled) {
            return (
              <li key={it.to}>
                <span className={cn(base, "text-[var(--color-sidebar-muted)] opacity-50 cursor-not-allowed")} title={collapsed ? it.label : undefined}>
                  <it.icon className="h-[18px] w-[18px] shrink-0" />
                  {!collapsed && <span className="truncate">{it.label}</span>}
                </span>
              </li>
            );
          }
          return (
            <li key={it.to}>
              <Link
                to={it.to}
                title={collapsed ? it.label : undefined}
                className={cn(
                  base,
                  "text-[var(--color-sidebar-foreground)] hover:text-white hover:bg-[var(--color-sidebar-active-bg)]",
                  active && "bg-[var(--color-sidebar-active-bg)] text-[var(--color-sidebar-accent)] font-medium"
                )}
              >
                {active && <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r bg-[var(--color-sidebar-accent)]" />}
                <it.icon className="h-[18px] w-[18px] shrink-0" />
                {!collapsed && <span className="truncate">{it.label}</span>}
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
