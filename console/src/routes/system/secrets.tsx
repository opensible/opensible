import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ShieldCheck, Plus, Pencil, Trash2, KeyRound, Search, RefreshCw, Key, Fingerprint, Users, Package } from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/system/secrets")({ component: SecretsPage });

type Secret = {
  id: string;
  name: string;
  type: string;
  description?: string;
  metadata?: Record<string, any>;
  created_at?: string;
  updated_at?: string;
};

const SECRET_TYPES = [
  { value: "git_ssh_key", label: "SSH Key" },
  { value: "git_token", label: "Git Token" },
  { value: "basic_auth", label: "Basic Auth" },
  { value: "registry_token", label: "Registry Token" },
  { value: "vault_token", label: "Vault Token" },
];

const SECRET_TYPE_CARDS = [
  { value: "git_ssh_key", label: "SSH Key", icon: Key, disabled: false, comingSoon: false },
  { value: "git_token", label: "Token", icon: Fingerprint, disabled: true, comingSoon: true },
  { value: "basic_auth", label: "Basic Auth", icon: Users, disabled: true, comingSoon: true },
  { value: "registry_token", label: "Registry Token", icon: Package, disabled: true, comingSoon: true },
] as const;


function SecretsPage() {
  const t = useT();
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [editing, setEditing] = useState<Secret | "new" | null>(null);
  const [deleting, setDeleting] = useState<Secret | null>(null);

  const perms = useQuery({
    queryKey: ["global-secrets-perms"],
    queryFn: () => api<{ success: boolean; permissions: { read: boolean; write: boolean } }>("GET", "/api/global/secrets/permissions"),
  });

  const list = useQuery({
    queryKey: ["global-secrets", search, typeFilter],
    queryFn: () => {
      const p = new URLSearchParams();
      if (search) p.set("search", search);
      if (typeFilter) p.set("type", typeFilter);
      const qs = p.toString();
      return api<{ success: boolean; secrets: Secret[] }>("GET", `/api/global/secrets${qs ? `?${qs}` : ""}`);
    },
  });

  const keyInfo = useQuery({
    queryKey: ["global-secrets-key"],
    queryFn: () => api<{ success: boolean; key: { exists: boolean; source: string } }>("GET", "/api/global/secrets/encryption-key"),
  });

  const del = useMutation({
    mutationFn: (id: string) => api("DELETE", `/api/global/secrets/${encodeURIComponent(id)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["global-secrets"] });
      setDeleting(null);
    },
  });

  const canWrite = !!perms.data?.permissions?.write;
  const secrets = list.data?.secrets ?? [];

  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "System" }, { label: "Secrets Management" }]} />
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{t("page.systemSecrets.title")}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">{t("page.systemSecrets.subtitle")}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => list.refetch()}>
            <RefreshCw className={`h-4 w-4 ${list.isFetching ? "animate-spin" : ""}`} /> Refresh
          </Button>
          {canWrite && (
            <Button size="sm" onClick={() => setEditing("new")}>
              <Plus className="h-4 w-4" /> New Secret
            </Button>
          )}
        </div>
      </div>

      {/* Encryption key card */}
      <Card>
        <CardHeader className="pb-3 flex-row items-center justify-between !flex-row">
          <div>
            <CardTitle className="text-base flex items-center gap-2">
              <KeyRound className="h-4 w-4" /> Encryption Key
            </CardTitle>
            <CardDescription>
              Master key used to encrypt and decrypt stored secret material.
            </CardDescription>
          </div>
          <Badge variant={keyInfo.data?.key?.exists ? "success" : "destructive"}>
            {keyInfo.data?.key?.exists
              ? `Active · ${keyInfo.data.key.source}`
              : "Missing"}
          </Badge>
        </CardHeader>
      </Card>

      {/* Filters + table */}
      <Card>
        <CardHeader className="pb-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="relative md:col-span-2">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--color-muted-foreground)]" />
              <Input className="pl-8" placeholder="Search by name or description…" value={search} onChange={e => setSearch(e.target.value)} />
            </div>
            <Select
              value={typeFilter}
              onChange={setTypeFilter}
              placeholder="All types"
              options={[{ value: "", label: "All types" }, ...SECRET_TYPES]}
            />
          </div>
        </CardHeader>
        <CardContent className="px-0">
          {list.isLoading ? (
            <div className="py-12 text-center text-sm text-[var(--color-muted-foreground)]">Loading…</div>
          ) : list.isError ? (
            <div className="px-6 py-6 text-sm text-[var(--color-destructive)]">{(list.error as Error).message}</div>
          ) : secrets.length === 0 ? (
            <div className="py-12 text-center text-sm text-[var(--color-muted-foreground)]">
              No secrets yet. {canWrite && "Click \"New Secret\" to add one."}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs uppercase tracking-wider text-[var(--color-muted-foreground)] border-b">
                  <tr>
                    <th className="text-left font-medium px-6 py-2.5">Name</th>
                    <th className="text-left font-medium px-3 py-2.5">Type</th>
                    <th className="text-left font-medium px-3 py-2.5">Description</th>
                    <th className="text-left font-medium px-3 py-2.5">Updated</th>
                    <th className="px-6 py-2.5"></th>
                  </tr>
                </thead>
                <tbody>
                  {secrets.map(s => (
                    <tr key={s.id} className="border-b hover:bg-[var(--color-muted)]/40">
                      <td className="px-6 py-3 font-medium">{s.name}</td>
                      <td className="px-3 py-3"><Badge variant="primary">{s.type}</Badge></td>
                      <td className="px-3 py-3 text-[var(--color-muted-foreground)] max-w-xs truncate">{s.description || "—"}</td>
                      <td className="px-3 py-3 text-[var(--color-muted-foreground)] text-xs">{s.updated_at ? new Date(s.updated_at).toLocaleString() : "—"}</td>
                      <td className="px-6 py-3 text-right">
                        {canWrite && (
                          <div className="flex justify-end gap-1">
                            <Button variant="ghost" size="icon" onClick={() => setEditing(s)} title="Edit">
                              <Pencil className="h-4 w-4" />
                            </Button>
                            <Button variant="ghost" size="icon" onClick={() => setDeleting(s)} title="Delete">
                              <Trash2 className="h-4 w-4 text-[var(--color-destructive)]" />
                            </Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {editing && (
        <SecretDialog
          secret={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); qc.invalidateQueries({ queryKey: ["global-secrets"] }); }}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title={`Delete "${deleting.name}"?`}
          description="This will permanently delete this secret. Any stacks or projects referencing it will fail until updated."
          confirmLabel={del.isPending ? "Deleting…" : "Delete"}
          destructive
          onCancel={() => setDeleting(null)}
          onConfirm={() => del.mutate(deleting.id)}
        />
      )}
    </div>
  );
}

function SecretDialog({ secret, onClose, onSaved }: { secret: Secret | null; onClose: () => void; onSaved: () => void }) {
  const isEdit = !!secret;
  const [name, setName] = useState(secret?.name ?? "");
  const [type, setType] = useState(secret?.type ?? "git_ssh_key");
  const [description, setDescription] = useState(secret?.description ?? "");
  const [username, setUsername] = useState((secret?.metadata?.username as string) ?? "");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () => {
      setErr(null);
      const body: Record<string, any> = { name, type, description };
      if (username) body.username = username;
      if (password) body.password = password;
      if (token) body.token = token;
      if (privateKey) body.privateKey = privateKey;
      if (passphrase) body.passphrase = passphrase;
      if (isEdit) {
        return api("PUT", `/api/global/secrets/${encodeURIComponent(secret!.id)}`, body);
      }
      return api("POST", `/api/global/secrets`, body);
    },
    onSuccess: onSaved,
    onError: (e: Error) => setErr(e.message),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="bg-[var(--color-card)] rounded-lg shadow-xl w-full max-w-lg border border-[var(--color-border)]" onClick={e => e.stopPropagation()}>
        <div className="p-5 border-b border-[var(--color-border)]">
          <h2 className="text-lg font-semibold">{isEdit ? "Edit Secret" : "New Secret"}</h2>
        </div>
        <div className="p-5 space-y-3 max-h-[70vh] overflow-y-auto">
          <Field label="Name">
            <Input value={name} onChange={e => setName(e.target.value)} placeholder="my-secret" disabled={isEdit} />
          </Field>
          <Field label="Type" required>
            <div className="grid grid-cols-2 gap-3">
              {SECRET_TYPE_CARDS.map((opt) => {
                const isSelected = type === opt.value;
                const Icon = opt.icon;
                const isDisabled = opt.disabled || isEdit;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    disabled={isDisabled}
                    onClick={() => !isDisabled && setType(opt.value)}
                    className={[
                      "relative flex items-center gap-3 rounded-xl border-2 p-4 text-left transition-all",
                      isSelected
                        ? "border-[var(--color-primary)] bg-[var(--color-primary)]/5"
                        : "border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-primary)]/30",
                      isDisabled && "opacity-50 cursor-not-allowed hover:border-[var(--color-border)]",
                    ].join(" ")}
                  >
                    <div
                      className={[
                        "h-5 w-5 shrink-0 rounded-full border-2 flex items-center justify-center",
                        isSelected ? "border-[var(--color-primary)]" : "border-[var(--color-muted-foreground)]/30",
                      ].join(" ")}
                    >
                      {isSelected && <div className="h-2.5 w-2.5 rounded-full bg-[var(--color-primary)]" />}
                    </div>
                    <Icon className="h-5 w-5 shrink-0 text-[var(--color-muted-foreground)]" />
                    <span className="text-sm font-medium">{opt.label}</span>
                    {opt.comingSoon && (
                      <span className="ml-auto text-xs text-[var(--color-muted-foreground)]">Coming soon</span>
                    )}
                  </button>
                );
              })}
            </div>
          </Field>
          <Field label="Description">
            <Input value={description} onChange={e => setDescription(e.target.value)} placeholder="Optional description" />
          </Field>

          {(type === "basic_auth" || type === "git_ssh_key") && (
            <Field label="Username">
              <Input value={username} onChange={e => setUsername(e.target.value)} />
            </Field>
          )}

          {type === "basic_auth" && (
            <Field label={isEdit ? "Password (leave blank to keep)" : "Password"}>
              <Input type="password" value={password} onChange={e => setPassword(e.target.value)} />
            </Field>
          )}

          {(type === "git_token" || type === "registry_token" || type === "vault_token") && (
            <Field label={isEdit ? "Token (leave blank to keep)" : "Token"}>
              <Input type="password" value={token} onChange={e => setToken(e.target.value)} />
            </Field>
          )}

          {type === "git_ssh_key" && (
            <>
              <Field label={isEdit ? "Private key (leave blank to keep)" : "Private key"}>
                <textarea
                  value={privateKey}
                  onChange={e => setPrivateKey(e.target.value)}
                  rows={6}
                  className="w-full rounded-md border border-[var(--color-input)] bg-transparent p-2 text-sm font-mono"
                  placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                />
              </Field>
              <Field label="Passphrase (optional)">
                <Input type="password" value={passphrase} onChange={e => setPassphrase(e.target.value)} />
              </Field>
            </>
          )}


          {err && <div className="text-sm text-[var(--color-destructive)]">{err}</div>}
        </div>
        <div className="p-4 border-t border-[var(--color-border)] flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending || !name || !type}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs font-medium text-[var(--color-muted-foreground)] mb-1">
        {label}{required && <span className="text-[var(--color-destructive)] ml-0.5">*</span>}
      </div>
      {children}
    </div>
  );
}

function ConfirmDialog({ title, description, confirmLabel, destructive, onCancel, onConfirm }: {
  title: string; description: string; confirmLabel: string; destructive?: boolean;
  onCancel: () => void; onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onCancel}>
      <div className="bg-[var(--color-card)] rounded-lg shadow-xl w-full max-w-md border border-[var(--color-border)]" onClick={e => e.stopPropagation()}>
        <div className="p-5">
          <h2 className="text-lg font-semibold mb-2">{title}</h2>
          <p className="text-sm text-[var(--color-muted-foreground)]">{description}</p>
        </div>
        <div className="p-4 border-t border-[var(--color-border)] flex justify-end gap-2">
          <Button variant="outline" onClick={onCancel}>Cancel</Button>
          <Button variant={destructive ? "destructive" : "default"} onClick={onConfirm}>{confirmLabel}</Button>
        </div>
      </div>
    </div>
  );
}
