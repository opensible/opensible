import { createFileRoute, redirect } from "@tanstack/react-router";

// Legacy route — Secrets was merged into /infrastructure/vaults-secrets.
export const Route = createFileRoute("/infrastructure/secrets")({
  beforeLoad: () => {
    throw redirect({ to: "/infrastructure/vaults-secrets", search: { tab: "secrets" as const } });
  },
});
