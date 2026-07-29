import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Plug, Plus, RefreshCw, Trash2, RotateCcw, Ban, BookOpen, Copy, Check } from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/system/api")({ component: ApiPage });

type Token = {
  id: string;
  name: string;
  scope: string;
  project_id?: string | null;
  created_at?: string;
  expires_at?: string | null;
  last_used_at?: string | null;
  revoked?: boolean;
};

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "tokens", label: "Tokens" },
] as const;
type TabId = (typeof TABS)[number]["id"];

function ApiPage() {
  const t = useT();
  const [tab, setTab] = useState<TabId>("overview");
  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "System" }, { label: "API" }]} />
      <div>
        <h1 className="text-2xl font-semibold">{t("page.api.title")}</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">{t("page.api.subtitle")}</p>
      </div>

      <div className="flex gap-1 border-b border-[var(--color-border)]">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "px-4 py-2 text-sm border-b-2 -mb-px transition-colors",
              tab === t.id ? "border-[var(--color-primary)] text-[var(--color-primary)] font-medium"
                : "border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
            )}
          >{t.label}</button>
        ))}
      </div>

      {tab === "overview" && <OverviewTab />}
      {tab === "tokens" && <TokensTab />}
    </div>
  );
}

function OverviewTab() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Base URL</CardTitle>
          <CardDescription>All endpoints are served from this origin.</CardDescription>
        </CardHeader>
        <CardContent>
          <code className="block px-3 py-2 bg-[var(--color-muted)] rounded text-sm font-mono">
            {typeof window !== "undefined" ? window.location.origin : ""}/api
          </code>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Authentication</CardTitle>
          <CardDescription>Send an API token as a Bearer header.</CardDescription>
        </CardHeader>
        <CardContent>
          <code className="block px-3 py-2 bg-[var(--color-muted)] rounded text-sm font-mono whitespace-pre">
{`curl -H "Authorization: Bearer <token>" \\
  ${typeof window !== "undefined" ? window.location.origin : ""}/api/projects`}
          </code>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <BookOpen className="h-4 w-4" /> OpenAPI v2
            <Badge variant="success">Recommended</Badge>
          </CardTitle>
          <CardDescription>
            Modern flask-smorest spec with rich schemas. Mirrors every endpoint at <code>/api/v2/*</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <a href="/api/v2/docs" target="_blank" rel="noreferrer" className="block">
            <Button variant="outline" size="sm">Open Swagger UI (v2)</Button>
          </a>
          <a href="/api/v2/openapi.json" target="_blank" rel="noreferrer" className="block">
            <Button variant="outline" size="sm">View openapi.json (v2)</Button>
          </a>
        </CardContent>
      </Card>
    </div>
  );
}


function TokensTab() {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [newToken, setNewToken] = useState<{ token: string; name: string } | null>(null);

  const q = useQuery({
    queryKey: ["api-tokens"],
    queryFn: () => api<{ success: boolean; tokens: Token[] }>("GET", "/api/api-tokens"),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => api("POST", `/api/api-tokens/${encodeURIComponent(id)}/revoke`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-tokens"] }),
  });
  const del = useMutation({
    mutationFn: (id: string) => api("DELETE", `/api/api-tokens/${encodeURIComponent(id)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-tokens"] }),
  });
  const rotate = useMutation({
    mutationFn: (id: string) => api<{ success: boolean; token: string }>("POST", `/api/api-tokens/${encodeURIComponent(id)}/rotate`),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["api-tokens"] });
      if (data?.token) setNewToken({ token: data.token, name: "Rotated token" });
    },
  });

  const tokens = q.data?.tokens ?? [];

  return (
    <Card>
      <CardHeader className="!flex-row items-center justify-between pb-3">
        <div>
          <CardTitle className="text-base">API Tokens</CardTitle>
          <CardDescription>Tokens scoped to your user account.</CardDescription>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => q.refetch()}>
            <RefreshCw className={`h-4 w-4 ${q.isFetching ? "animate-spin" : ""}`} /> Refresh
          </Button>
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" /> New Token
          </Button>
        </div>
      </CardHeader>
      <CardContent className="px-0">
        {q.isLoading ? (
          <div className="py-12 text-center text-sm text-[var(--color-muted-foreground)]">Loading…</div>
        ) : q.isError ? (
          <div className="px-6 py-6 text-sm text-[var(--color-destructive)]">{(q.error as Error).message}</div>
        ) : tokens.length === 0 ? (
          <div className="py-12 text-center text-sm text-[var(--color-muted-foreground)]">No tokens yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-wider text-[var(--color-muted-foreground)] border-b">
              <tr>
                <th className="text-left font-medium px-6 py-2.5">Name</th>
                <th className="text-left font-medium px-3 py-2.5">Scope</th>
                <th className="text-left font-medium px-3 py-2.5">Created</th>
                <th className="text-left font-medium px-3 py-2.5">Last used</th>
                <th className="text-left font-medium px-3 py-2.5">Status</th>
                <th className="px-6 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {tokens.map(t => (
                <tr key={t.id} className="border-b">
                  <td className="px-6 py-3 font-medium">{t.name}</td>
                  <td className="px-3 py-3"><Badge variant="primary">{t.scope}</Badge></td>
                  <td className="px-3 py-3 text-xs text-[var(--color-muted-foreground)]">{t.created_at ? new Date(t.created_at).toLocaleString() : "—"}</td>
                  <td className="px-3 py-3 text-xs text-[var(--color-muted-foreground)]">{t.last_used_at ? new Date(t.last_used_at).toLocaleString() : "—"}</td>
                  <td className="px-3 py-3">
                    <Badge variant={t.revoked ? "destructive" : "success"}>{t.revoked ? "Revoked" : "Active"}</Badge>
                  </td>
                  <td className="px-6 py-3 text-right">
                    <div className="flex justify-end gap-1">
                      {!t.revoked && (
                        <>
                          <Button variant="ghost" size="icon" title="Rotate" onClick={() => rotate.mutate(t.id)}>
                            <RotateCcw className="h-4 w-4" />
                          </Button>
                          <Button variant="ghost" size="icon" title="Revoke" onClick={() => revoke.mutate(t.id)}>
                            <Ban className="h-4 w-4" />
                          </Button>
                        </>
                      )}
                      <Button variant="ghost" size="icon" title="Delete" onClick={() => del.mutate(t.id)}>
                        <Trash2 className="h-4 w-4 text-[var(--color-destructive)]" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>

      {creating && (
        <CreateTokenDialog
          onClose={() => setCreating(false)}
          onCreated={(token, name) => {
            setCreating(false);
            setNewToken({ token, name });
            qc.invalidateQueries({ queryKey: ["api-tokens"] });
          }}
        />
      )}

      {newToken && (
        <ShowTokenDialog token={newToken.token} name={newToken.name} onClose={() => setNewToken(null)} />
      )}
    </Card>
  );
}

function CreateTokenDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (token: string, name: string) => void }) {
  const [name, setName] = useState("");
  const [scope, setScope] = useState("global");
  const [expiresDays, setExpiresDays] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);

  const m = useMutation({
    mutationFn: async () => {
      setErr(null);
      return api<{ success: boolean; token: string }>("POST", "/api/api-tokens", {
        name, scope, expiresDays: expiresDays ? Number(expiresDays) : null,
      });
    },
    onSuccess: (d) => onCreated(d.token, name),
    onError: (e: Error) => setErr(e.message),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="bg-[var(--color-card)] rounded-lg shadow-xl w-full max-w-md border border-[var(--color-border)]" onClick={e => e.stopPropagation()}>
        <div className="p-5 border-b border-[var(--color-border)]">
          <h2 className="text-lg font-semibold">New API Token</h2>
        </div>
        <div className="p-5 space-y-3">
          <div>
            <div className="text-xs font-medium text-[var(--color-muted-foreground)] mb-1">Name</div>
            <Input value={name} onChange={e => setName(e.target.value)} placeholder="My laptop" />
          </div>
          <div>
            <div className="text-xs font-medium text-[var(--color-muted-foreground)] mb-1">Scope</div>
            <Select
              value={scope}
              onChange={setScope}
              options={[{ value: "global", label: "Global" }, { value: "project", label: "Project" }]}
            />
          </div>
          <div>
            <div className="text-xs font-medium text-[var(--color-muted-foreground)] mb-1">Expires (days)</div>
            <Input type="number" value={expiresDays} onChange={e => setExpiresDays(e.target.value)} placeholder="Never" />
          </div>
          {err && <div className="text-sm text-[var(--color-destructive)]">{err}</div>}
        </div>
        <div className="p-4 border-t border-[var(--color-border)] flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={() => m.mutate()} disabled={!name || m.isPending}>{m.isPending ? "Creating…" : "Create"}</Button>
        </div>
      </div>
    </div>
  );
}

function ShowTokenDialog({ token, name, onClose }: { token: string; name: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(token);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-[var(--color-card)] rounded-lg shadow-xl w-full max-w-lg border border-[var(--color-border)]">
        <div className="p-5 border-b border-[var(--color-border)]">
          <h2 className="text-lg font-semibold">{name}</h2>
          <p className="text-sm text-[var(--color-destructive)] mt-1">
            Copy this token now. It will not be shown again.
          </p>
        </div>
        <div className="p-5">
          <div className="flex gap-2">
            <code className="flex-1 px-3 py-2 bg-[var(--color-muted)] rounded text-xs font-mono break-all">{token}</code>
            <Button variant="outline" onClick={copy}>
              {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
        </div>
        <div className="p-4 border-t border-[var(--color-border)] flex justify-end">
          <Button onClick={onClose}>I've saved it</Button>
        </div>
      </div>
    </div>
  );
}
