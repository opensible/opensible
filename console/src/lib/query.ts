import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./api";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Don't retry auth/client errors
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
        return failureCount < 2;
      },
      retryDelay: (i) => Math.min(1000 * 2 ** i, 8000),
    },
    mutations: {
      retry: false,
    },
  },
});

// Centralized cache keys for cross-page invalidation.
export const qk = {
  stacks: ["cloud", "stacks"] as const,
  stack: (id: string) => ["cloud", "stack", id] as const,
  stackSchema: (provider: string) => ["cloud", "schema", provider] as const,
  providers: ["cloud", "providers"] as const,
  runs: ["cloud", "runs"] as const,
  run: (stack: string, runId: string) => ["cloud", "run", stack, runId] as const,
  syncState: ["cloud", "sync-state"] as const,
  projects: ["projects"] as const,
  pipelines: ["cicd", "pipelines"] as const,
  cicdRuns: ["cicd", "runs"] as const,
};
