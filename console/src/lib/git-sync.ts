import { api } from "@/lib/api";

export type SyncDirection = "pull" | "push" | "both";

export type SyncState = {
  lastPushAt?: number | string | null;
  lastPullAt?: number | string | null;
  lastPushStatus?: string;
  lastPullStatus?: string;
  lastPushError?: string | null;
  lastPullError?: string | null;
};

/**
 * Kick off a git sync and poll the state endpoint until the chosen direction
 * leaves the `running` state. Matches the old-web-frontend behaviour.
 */
export async function runGitSync(
  projectId: string,
  sourceKey: string,
  direction: SyncDirection,
  opts: { timeoutMs?: number; intervalMs?: number } = {},
): Promise<{ ok: boolean; state: SyncState; error?: string }> {
  const timeoutMs = opts.timeoutMs ?? 120_000;
  const intervalMs = opts.intervalMs ?? 1500;

  await api("POST", `/api/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceKey)}/sync`, {
    direction,
  });

  const dir: "pull" | "push" = direction === "push" ? "push" : "pull";
  const statusKey = dir === "pull" ? "lastPullStatus" : "lastPushStatus";
  const errKey = dir === "pull" ? "lastPullError" : "lastPushError";
  const startedAt = Date.now();

  // Give the server a brief moment to mark `running`
  await new Promise((r) => setTimeout(r, 400));

  while (true) {
    const resp = await api<{ success: boolean; state: SyncState }>(
      "GET",
      `/api/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceKey)}/sync/state`,
    );
    const state = (resp?.state || {}) as SyncState;
    const status = String(state[statusKey] || "").toLowerCase();
    if (status === "ok" || status === "success") {
      return { ok: true, state };
    }
    if (status === "failed" || status === "error") {
      return { ok: false, state, error: (state[errKey] as string) || "sync failed" };
    }
    if (Date.now() - startedAt > timeoutMs) {
      return { ok: false, state, error: "timeout waiting for sync" };
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
