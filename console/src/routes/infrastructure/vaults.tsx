import { createFileRoute, redirect } from "@tanstack/react-router";

// Legacy route — Vaults + Secrets were merged into /infrastructure/vaults-secrets.
export const Route = createFileRoute("/infrastructure/vaults")({
  beforeLoad: () => {
    throw redirect({ to: "/infrastructure/vaults-secrets", search: { tab: "keys" as const } });
  },
});
