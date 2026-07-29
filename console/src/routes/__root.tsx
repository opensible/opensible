import { createRootRoute, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AppSidebar } from "@/components/app-shell/Sidebar";
import { AppHeader } from "@/components/app-shell/Header";
import { getToken } from "@/lib/api";

export const Route = createRootRoute({
  component: RootLayout,
  errorComponent: ({ error }) => (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold mb-2">Something went wrong</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">{error.message}</p>
      </div>
    </div>
  ),
});

function RootLayout() {
  const navigate = useNavigate();
  const { location } = useRouterState();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const token = getToken();
    if (!token && !location.pathname.startsWith("/login")) {
      navigate({ to: "/login", replace: true });
    } else {
      setReady(true);
    }
  }, [location.pathname, navigate]);

  if (location.pathname.startsWith("/login")) {
    return <Outlet />;
  }

  if (!ready) return null;

  return (
    <div className="flex h-screen w-full">
      <AppSidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <AppHeader />
        <main className="flex-1 overflow-auto p-6 bg-[var(--color-background)]">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
