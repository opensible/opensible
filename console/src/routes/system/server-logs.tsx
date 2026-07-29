import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { RefreshCw, Pause, Play, Download, Search } from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { LogViewer } from "@/components/cloud/LogViewer";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/system/server-logs")({ component: ServerLogsPage });

type LogEntry = {
  service: string;
  raw: string;
  timestamp?: string | null;
  level?: string | null;
  message?: string | null;
};

function ServerLogsPage() {
  const t = useT();
  const [service, setService] = useState("all");
  const [level, setLevel] = useState("all");
  const [search, setSearch] = useState("");
  const [lines, setLines] = useState(100);
  const [paused, setPaused] = useState(false);

  const params = new URLSearchParams({ service, level, search, lines: String(lines) });

  const q = useQuery({
    queryKey: ["server-logs", service, level, search, lines],
    queryFn: () => api<{ success: boolean; logs: LogEntry[] }>("GET", `/api/server_logs?${params.toString()}`),
    refetchInterval: paused ? false : 10000,
    refetchIntervalInBackground: false,
  });

  const entries = q.data?.logs ?? [];

  const text = useMemo(
    () =>
      entries
        .map((e) => {
          const lvl = e.level ? `${e.level.toUpperCase()} ` : "";
          return `[${e.service}] ${lvl}${e.raw}`;
        })
        .join("\n"),
    [entries]
  );

  const download = () => {
    const blob = new Blob([entries.map((e) => e.raw).join("\n")], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `server-logs-${Date.now()}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "System" }, { label: "Server Logs" }]} />
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{t("page.serverLogs.title")}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">{t("page.serverLogs.subtitle")}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => setPaused((p) => !p)}>
            {paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
            {paused ? "Resume" : "Pause"}
          </Button>
          <Button variant="outline" size="sm" onClick={() => q.refetch()}>
            <RefreshCw className={`h-4 w-4 ${q.isFetching ? "animate-spin" : ""}`} /> Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={download}>
            <Download className="h-4 w-4" /> Download
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <Select
              value={service}
              onChange={setService}
              options={[
                { value: "all", label: "All services" },
                { value: "backend", label: "Backend" },
                { value: "worker", label: "Worker" },
                { value: "frontend", label: "Frontend" },
              ]}
            />
            <Select
              value={level}
              onChange={setLevel}
              options={[
                { value: "all", label: "All levels" },
                { value: "error", label: "Error" },
                { value: "warning", label: "Warning" },
                { value: "info", label: "Info" },
                { value: "debug", label: "Debug" },
              ]}
            />
            <Select
              value={String(lines)}
              onChange={(v) => setLines(Number(v))}
              options={[
                { value: "100", label: "Last 100" },
                { value: "200", label: "Last 200" },
                { value: "500", label: "Last 500" },
                { value: "1000", label: "Last 1,000" },
                { value: "5000", label: "Last 5,000" },
              ]}

            />
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--color-muted-foreground)]" />
              <Input className="pl-8" placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {q.isLoading ? (
            <div className="py-12 text-center text-sm text-[var(--color-muted-foreground)]">Loading logs…</div>
          ) : q.isError ? (
            <div className="py-6 text-sm text-[var(--color-destructive)]">{(q.error as Error).message}</div>
          ) : entries.length === 0 ? (
            <div className="rounded-md border border-[var(--color-border)] bg-black/90 text-[var(--color-muted-foreground)] font-mono text-xs p-3 h-[60vh]">
              No log entries.
            </div>
          ) : (
            <LogViewer text={text} className="h-[60vh] max-h-[60vh]" />
          )}
          <div className="mt-2 text-xs text-[var(--color-muted-foreground)] flex items-center gap-2">
            <Badge variant="default">{entries.length} entries</Badge>
            {!paused && <span>Auto-refreshing every 10s</span>}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

