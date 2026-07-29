import { createFileRoute, useSearch } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import * as yaml from "js-yaml";
import {
  FileCode, RefreshCw, Plus, Search, Lock, KeyRound, Trash2, Pencil, ShieldCheck, Wand2,
} from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

type TabKey = "keys" | "files" | "secrets";

export const Route = createFileRoute("/infrastructure/vaults-secrets")({
  component: VaultsSecretsPage,
  validateSearch: (s: Record<string, unknown>): { tab?: TabKey } => {
    const t = s.tab;
    return t === "keys" || t === "files" || t === "secrets" ? { tab: t } : {};
  },
});

// ---------- Types ----------
type Vault = { id: string; name: string; keyId?: string; keyName?: string; vaultId?: string };
type VaultKey = { id: string; name: string; type?: string };
type VaultFile = { path: string; key?: string; keyName?: string; vaultId?: string };
type Secret = { name: string; type?: string; username?: string; description?: string; updatedAt?: string };

// ---------- Modal ----------
function Modal({ open, onClose, title, children, footer, wide, size }: {
  open: boolean; onClose: () => void; title: string; children: React.ReactNode; footer?: React.ReactNode;
  wide?: boolean; size?: "md" | "lg" | "xl" | "2xl";
}) {
  if (!open) return null;
  const sizeCls =
    size === "2xl" ? "max-w-3xl" :
    size === "xl" ? "max-w-2xl" :
    size === "lg" ? "max-w-xl" :
    wide ? "max-w-lg" : "max-w-md";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className={cn(
        "bg-[var(--color-card)] rounded-lg shadow-2xl w-full border border-[var(--color-border)] flex flex-col max-h-[90vh]",
        sizeCls,
      )} onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-3 border-b border-[var(--color-border)] flex items-center justify-between">
          <h2 className="text-base font-semibold">{title}</h2>
          <button onClick={onClose} className="text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] text-xl leading-none">×</button>
        </div>
        <div className="p-5 overflow-y-auto flex-1">{children}</div>
        {footer && <div className="px-5 py-3 border-t border-[var(--color-border)] flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}


function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1">{label}</label>
      {children}
    </div>
  );
}

// ---------- Page ----------
function VaultsSecretsPage() {
  const t = useT();
  const search = useSearch({ from: "/infrastructure/vaults-secrets" });
  const navigate = Route.useNavigate();
  const tab: TabKey = search.tab ?? "keys";
  const setTab = (t: TabKey) => navigate({ search: { tab: t } });

  return (
    <div className="space-y-4">
      <Breadcrumbs items={[
        { label: "Infrastructure" },
        { label: "Vaults & Secrets", icon: <ShieldCheck className="h-3.5 w-3.5" /> },
      ]} />

      <div>
        <h1 className="text-2xl font-semibold">{t("page.vaults.title")}</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">{t("page.vaults.subtitle")}</p>
      </div>

      {/* Tab strip */}
      <div className="border-b border-[var(--color-border)] flex gap-1 text-sm">
        <TabButton active={tab === "keys"} onClick={() => setTab("keys")} icon={<KeyRound className="h-3.5 w-3.5" />}>
          {t("vaults.tab.keys")}
        </TabButton>
        <TabButton active={tab === "files"} onClick={() => setTab("files")} icon={<FileCode className="h-3.5 w-3.5" />}>
          {t("vaults.tab.files")}
        </TabButton>
        <TabButton active={tab === "secrets"} onClick={() => setTab("secrets")} icon={<ShieldCheck className="h-3.5 w-3.5" />}>
          {t("vaults.tab.secrets")}
        </TabButton>
      </div>

      {tab === "keys" && <KeysAndVaultsTab />}
      {tab === "files" && <EncryptedFilesTab />}
      {tab === "secrets" && <SecretsTab />}
    </div>
  );
}

function TabButton({
  active, onClick, icon, children,
}: { active: boolean; onClick: () => void; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "px-4 py-2 -mb-px border-b-2 inline-flex items-center gap-1.5 transition-colors",
        active
          ? "border-[var(--color-primary)] text-[var(--color-primary)] font-medium"
          : "border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]",
      )}
    >
      {icon}
      {children}
    </button>
  );
}

// =====================================================================
// TAB 1 — Vault Keys + Named Vaults
// =====================================================================
function KeysAndVaultsTab() {
  const t = useT();
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [showNewVault, setShowNewVault] = useState(false);
  const [showNewKey, setShowNewKey] = useState(false);
  const [editVault, setEditVault] = useState<Vault | null>(null);
  const [editKey, setEditKey] = useState<VaultKey | null>(null);
  const [delVault, setDelVault] = useState<Vault | null>(null);
  const [delKey, setDelKey] = useState<VaultKey | null>(null);

  const vaultsQ = useQuery({
    queryKey: ["vaults"],
    queryFn: () => api<{ success: boolean; vaults: Vault[]; vaultFiles: VaultFile[] }>("GET", "/api/projects/_current/vaults"),
  });
  const keysQ = useQuery({
    queryKey: ["vault-keys"],
    queryFn: () => api<{ success: boolean; keys: VaultKey[] }>("GET", "/api/projects/_current/vault-keys"),
  });

  const vaults: Vault[] = useMemo(() => vaultsQ.data?.vaults ?? [], [vaultsQ.data]);
  const keys: VaultKey[] = useMemo(() => keysQ.data?.keys ?? [], [keysQ.data]);
  const filtered = vaults.filter((v) => !q || v.name.toLowerCase().includes(q.toLowerCase()));

  const delVaultMut = useMutation({
    mutationFn: (id: string) => api("DELETE", `/api/projects/_current/vaults/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["vaults"] }); setDelVault(null); },
  });
  const delKeyMut = useMutation({
    mutationFn: (id: string) => api("DELETE", `/api/projects/_current/vault-keys/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["vault-keys"] }); setDelKey(null); },
  });

  return (
    <div className="space-y-4">
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={() => { vaultsQ.refetch(); keysQ.refetch(); }}>
          <RefreshCw className="h-4 w-4 mr-1.5" /> Refresh
        </Button>
        <Button variant="outline" size="sm" onClick={() => setShowNewKey(true)}>
          <KeyRound className="h-4 w-4 mr-1.5" /> New Key
        </Button>
        <Button size="sm" onClick={() => setShowNewVault(true)} disabled={keys.length === 0}>
          <Plus className="h-4 w-4 mr-1.5" /> New Vault
        </Button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4">
        <Card>
          <CardHeader className="py-3 flex flex-row items-center justify-between">
            <CardTitle className="text-sm">{t("vaults.card.vaults")}</CardTitle>
            <div className="relative w-64">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("common.search")} className="h-8 pl-7 text-sm" />
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
                  <th className="px-4 py-2 font-medium">{t("common.name")}</th>
                  <th className="px-4 py-2 font-medium">{t("vaults.vaultId")}</th>
                  <th className="px-4 py-2 font-medium">Key</th>
                  <th className="px-4 py-2 font-medium text-right">{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {vaultsQ.isLoading && <tr><td colSpan={4} className="px-4 py-6 text-center text-[var(--color-muted-foreground)]">{t("common.loading")}</td></tr>}
                {!vaultsQ.isLoading && filtered.length === 0 && (
                  <tr><td colSpan={4} className="px-4 py-6 text-center text-[var(--color-muted-foreground)]">
                    {keys.length === 0 ? "Create a vault key first." : "No vaults yet."}
                  </td></tr>
                )}
                {filtered.map((v) => (
                  <tr key={v.id} className="border-b hover:bg-[var(--color-accent)]/40">
                    <td className="px-4 py-2 font-medium flex items-center gap-2">
                      <Lock className="h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />{v.name}
                    </td>
                    <td className="px-4 py-2 font-mono text-xs">{v.vaultId || "—"}</td>
                    <td className="px-4 py-2"><Badge variant="default" className="text-[10px]">{v.keyName || v.keyId || "—"}</Badge></td>
                    <td className="px-4 py-2 text-right">
                      <Button size="sm" variant="ghost" onClick={() => setEditVault(v)}><Pencil className="h-3.5 w-3.5" /></Button>
                      <Button size="sm" variant="ghost" onClick={() => setDelVault(v)}><Trash2 className="h-3.5 w-3.5 text-red-500" /></Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="py-3 flex flex-row items-center justify-between">
            <CardTitle className="text-sm">{t("vaults.card.vaultKeys")}</CardTitle>
            <Button size="sm" variant="ghost" onClick={() => setShowNewKey(true)}><Plus className="h-3.5 w-3.5" /></Button>
          </CardHeader>
          <CardContent className="p-2">
            {keysQ.isLoading && <div className="text-xs text-[var(--color-muted-foreground)] p-3">{t("common.loading")}</div>}
            {!keysQ.isLoading && keys.length === 0 && <div className="text-xs text-[var(--color-muted-foreground)] p-3">{t("vaults.noKeys")}</div>}
            {keys.map((k) => (
              <div key={k.id} className="px-3 py-2 rounded-md hover:bg-[var(--color-accent)] flex items-center justify-between">
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">{k.name}</div>
                  <div className="text-[11px] text-[var(--color-muted-foreground)]">{k.type || "vault_password"}</div>
                </div>
                <div className="flex">
                  <Button size="sm" variant="ghost" onClick={() => setEditKey(k)}><Pencil className="h-3.5 w-3.5" /></Button>
                  <Button size="sm" variant="ghost" onClick={() => setDelKey(k)}><Trash2 className="h-3.5 w-3.5 text-red-500" /></Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      {showNewVault && <VaultDialog keys={keys} onClose={() => setShowNewVault(false)} onSaved={() => { setShowNewVault(false); qc.invalidateQueries({ queryKey: ["vaults"] }); }} />}
      {editVault && <VaultDialog keys={keys} vault={editVault} onClose={() => setEditVault(null)} onSaved={() => { setEditVault(null); qc.invalidateQueries({ queryKey: ["vaults"] }); }} />}
      {showNewKey && <VaultKeyDialog onClose={() => setShowNewKey(false)} onSaved={() => { setShowNewKey(false); qc.invalidateQueries({ queryKey: ["vault-keys"] }); }} />}
      {editKey && <VaultKeyDialog vaultKey={editKey} onClose={() => setEditKey(null)} onSaved={() => { setEditKey(null); qc.invalidateQueries({ queryKey: ["vault-keys"] }); }} />}
      <ConfirmDialog open={!!delVault} title="Delete vault" description={`Delete vault "${delVault?.name}"?`} variant="destructive" confirmLabel="Delete"
        onCancel={() => setDelVault(null)} onConfirm={() => delVault && delVaultMut.mutate(delVault.id)} />
      <ConfirmDialog open={!!delKey} title="Delete vault key" description={`Delete key "${delKey?.name}"? Vaults using it will fail to decrypt.`} variant="destructive" confirmLabel="Delete"
        onCancel={() => setDelKey(null)} onConfirm={() => delKey && delKeyMut.mutate(delKey.id)} />
    </div>
  );
}

// =====================================================================
// TAB 2 — Encrypted Files
// =====================================================================
function EncryptedFilesTab() {
  const t = useT();
  const qc = useQueryClient();
  const [showEncryptVar, setShowEncryptVar] = useState(false);
  const [delFile, setDelFile] = useState<VaultFile | null>(null);
  const vaultsQ = useQuery({
    queryKey: ["vaults"],
    queryFn: () => api<{ success: boolean; vaults: Vault[]; vaultFiles: VaultFile[] }>("GET", "/api/projects/_current/vaults"),
  });
  const vaultFiles: VaultFile[] = useMemo(() => vaultsQ.data?.vaultFiles ?? [], [vaultsQ.data]);
  const vaults: Vault[] = useMemo(() => vaultsQ.data?.vaults ?? [], [vaultsQ.data]);

  // Delete an encrypted vars file. Path examples:
  //   "inventories/group_vars/all.yml"                → scope=group, inventory_file="inventories/inventory.yml" (or empty)
  //   "inventories/prod/host_vars/web1.yml"           → scope=host,  inventory_file="inventories/prod/…"
  //   "group_vars/all.yml"                             → project-storage fallback
  const delMut = useMutation({
    mutationFn: async (f: VaultFile) => {
      const path = f.path;
      const isHost = /(^|\/)host_vars\//.test(path);
      const url = isHost ? "/api/host_vars/delete" : "/api/group_vars/delete";
      // Send the full repo-relative path so the backend deletes the exact file.
      return api("POST", url, { path, file: path.split("/").pop() });
    },
    onSuccess: () => {
      setDelFile(null);
      qc.invalidateQueries({ queryKey: ["vaults"] });
      qc.invalidateQueries({ queryKey: ["inv"] });
    },
  });

  return (
    <div className="space-y-4">
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={() => vaultsQ.refetch()}>
          <RefreshCw className="h-4 w-4 mr-1.5" /> {t("common.refresh")}
        </Button>
        <Button size="sm" onClick={() => setShowEncryptVar(true)} disabled={vaults.length === 0}
          title={vaults.length === 0 ? "Create a vault first (Vault Keys tab)" : ""}>
          <Wand2 className="h-4 w-4 mr-1.5" /> Encrypt a Variable
        </Button>
      </div>

      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-sm">{t("vaults.tab.files")} ({vaultFiles.length})</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
                <th className="px-4 py-2 font-medium">{t("vaults.path")}</th>
                <th className="px-4 py-2 font-medium">{t("vaults.vault")}</th>
                <th className="px-4 py-2 font-medium">Key</th>
                <th className="px-4 py-2 font-medium text-right">{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {vaultsQ.isLoading && <tr><td colSpan={4} className="px-4 py-6 text-center text-[var(--color-muted-foreground)]">{t("common.loading")}</td></tr>}
              {!vaultsQ.isLoading && vaultFiles.length === 0 && (
                <tr><td colSpan={4} className="px-4 py-6 text-center text-[var(--color-muted-foreground)]">{t("vaults.noEncrypted")}</td></tr>
              )}
              {vaultFiles.map((f) => (
                <tr key={f.path} className="border-b hover:bg-[var(--color-accent)]/40">
                  <td className="px-4 py-2 font-mono text-xs">{f.path}</td>
                  <td className="px-4 py-2 text-xs">{f.key || "—"}</td>
                  <td className="px-4 py-2 text-xs">{f.keyName || "—"}</td>
                  <td className="px-4 py-2 text-right">
                    <Button size="sm" variant="ghost" onClick={() => setDelFile(f)} title="Delete encrypted file">
                      <Trash2 className="h-3.5 w-3.5 text-red-500" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {showEncryptVar && (
        <EncryptVariableDialog
          vaults={vaults}
          onClose={() => setShowEncryptVar(false)}
          onSaved={() => {
            setShowEncryptVar(false);
            qc.invalidateQueries({ queryKey: ["vaults"] });
            qc.invalidateQueries({ queryKey: ["inv"] });
            qc.invalidateQueries({ queryKey: ["group_vars"] });
            qc.invalidateQueries({ queryKey: ["host_vars"] });
          }}
        />
      )}

      <ConfirmDialog
        open={!!delFile}
        title="Delete encrypted file"
        description={<>Permanently delete <span className="font-mono">{delFile?.path}</span>? A backup is kept in the project's backups directory.</>}
        variant="destructive"
        confirmLabel={delMut.isPending ? "Deleting…" : "Delete"}
        onCancel={() => setDelFile(null)}
        onConfirm={() => delFile && delMut.mutate(delFile)}
      />
    </div>
  );
}


// =====================================================================
// TAB 3 — Secrets (project SSH keys / passwords)
// =====================================================================
function SecretsTab() {
  const t = useT();
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [editTarget, setEditTarget] = useState<Secret | null>(null);
  const [delTarget, setDelTarget] = useState<Secret | null>(null);

  const query = useQuery({
    queryKey: ["secrets", "infra"],
    queryFn: () => api<{ secrets: Secret[] }>("GET", "/api/secrets"),
  });
  const secrets: Secret[] = useMemo(() => (query.data?.secrets ?? []), [query.data]);
  const filtered = secrets.filter((s) => !q || `${s.name} ${s.description ?? ""}`.toLowerCase().includes(q.toLowerCase()));

  const delMut = useMutation({
    mutationFn: (name: string) => api("DELETE", `/api/secrets/${encodeURIComponent(name)}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["secrets", "infra"] }); setDelTarget(null); },
  });

  return (
    <div className="space-y-4">
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={() => query.refetch()}><RefreshCw className="h-4 w-4 mr-1.5" /> {t("common.refresh")}</Button>
        <Button size="sm" onClick={() => setShowCreate(true)}><Plus className="h-4 w-4 mr-1.5" /> New Secret</Button>
      </div>

      <Card>
        <CardHeader className="py-3 flex flex-row items-center justify-between">
          <CardTitle className="text-sm">{filtered.length} secrets</CardTitle>
          <div className="relative w-64">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("common.search")} className="h-8 pl-7 text-sm" />
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
                <th className="px-4 py-2 font-medium">{t("common.name")}</th>
                <th className="px-4 py-2 font-medium">{t("common.type")}</th>
                <th className="px-4 py-2 font-medium">{t("common.username")}</th>
                <th className="px-4 py-2 font-medium">{t("common.description")}</th>
                <th className="px-4 py-2 font-medium text-right">{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {query.isLoading && <tr><td colSpan={5} className="px-4 py-6 text-center text-[var(--color-muted-foreground)]">{t("common.loading")}</td></tr>}
              {!query.isLoading && filtered.length === 0 && <tr><td colSpan={5} className="px-4 py-6 text-center text-[var(--color-muted-foreground)]">{t("vaults.noSecrets")}</td></tr>}
              {filtered.map((s) => (
                <tr key={s.name} className="border-b hover:bg-[var(--color-accent)]/40">
                  <td className="px-4 py-2 font-medium font-mono text-xs">{s.name}</td>
                  <td className="px-4 py-2"><Badge variant="default" className="text-[10px]">{s.type || "generic"}</Badge></td>
                  <td className="px-4 py-2 font-mono text-xs">{s.username || "—"}</td>
                  <td className="px-4 py-2 text-[var(--color-muted-foreground)]">{s.description || "—"}</td>
                  <td className="px-4 py-2 text-right">
                    <Button size="sm" variant="ghost" onClick={() => setEditTarget(s)}><Pencil className="h-3.5 w-3.5" /></Button>
                    {s.name !== "vault_pass" && (
                      <Button size="sm" variant="ghost" onClick={() => setDelTarget(s)}><Trash2 className="h-3.5 w-3.5 text-red-500" /></Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {showCreate && <CreateSecretDialog onClose={() => setShowCreate(false)} onSaved={() => { setShowCreate(false); qc.invalidateQueries({ queryKey: ["secrets", "infra"] }); }} />}
      {editTarget && <EditSecretDialog secret={editTarget} onClose={() => setEditTarget(null)} onSaved={() => { setEditTarget(null); qc.invalidateQueries({ queryKey: ["secrets", "infra"] }); }} />}
      <ConfirmDialog
        open={!!delTarget}
        title="Delete secret"
        description={`Delete secret "${delTarget?.name}"? This cannot be undone.`}
        confirmLabel="Delete"
        variant="destructive"
        onCancel={() => setDelTarget(null)}
        onConfirm={() => delTarget && delMut.mutate(delTarget.name)}
      />
    </div>
  );
}

// ---------- Dialogs ----------
function VaultDialog({ vault, keys, onClose, onSaved }: { vault?: Vault; keys: VaultKey[]; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(vault?.name ?? "");
  const [keyId, setKeyId] = useState(vault?.keyId ?? keys[0]?.id ?? "");
  const [vaultId, setVaultId] = useState(vault?.vaultId ?? "");
  const [err, setErr] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: () => {
      const body = { name, keyId, vaultId: vaultId || null };
      return vault
        ? api("PUT", `/api/projects/_current/vaults/${vault.id}`, body)
        : api("POST", "/api/projects/_current/vaults", body);
    },
    onSuccess: onSaved,
    onError: (e: Error) => setErr(e.message),
  });

  return (
    <Modal open onClose={onClose} title={vault ? "Edit Vault" : "New Vault"}
      footer={<>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button onClick={() => { setErr(null); mut.mutate(); }} disabled={mut.isPending || !name || !keyId}>
          {mut.isPending ? "Saving…" : "Save"}
        </Button>
      </>}>
      <div className="space-y-3">
        <Field label="Name *"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="prod-secrets" /></Field>
        <Select label="Key *" value={keyId} onChange={setKeyId}
          options={keys.map((k) => ({ value: k.id, label: k.name }))}
          placeholder="Select a vault key…" />
        <Field label="Vault ID (label)"><Input value={vaultId} onChange={(e) => setVaultId(e.target.value)} placeholder="prod" /></Field>
        {err && <div className="text-xs text-red-500">{err}</div>}
      </div>
    </Modal>
  );
}

function VaultKeyDialog({ vaultKey, onClose, onSaved }: { vaultKey?: VaultKey; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(vaultKey?.name ?? "");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { name };
      if (password) body.password = password;
      return vaultKey
        ? api("PUT", `/api/projects/_current/vault-keys/${vaultKey.id}`, body)
        : api("POST", "/api/projects/_current/vault-keys", { ...body, type: "vault_password" });
    },
    onSuccess: onSaved,
    onError: (e: Error) => setErr(e.message),
  });

  return (
    <Modal open onClose={onClose} title={vaultKey ? "Edit Vault Key" : "New Vault Key"}
      footer={<>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button onClick={() => { setErr(null); mut.mutate(); }} disabled={mut.isPending || !name || (!vaultKey && !password)}>
          {mut.isPending ? "Saving…" : "Save"}
        </Button>
      </>}>
      <div className="space-y-3">
        <Field label="Name *"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="prod-key" /></Field>
        <Field label={vaultKey ? "Rotate Password (leave empty to keep)" : "Password *"}>
          <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {err && <div className="text-xs text-red-500">{err}</div>}
      </div>
    </Modal>
  );
}

function CreateSecretDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [type, setType] = useState<"ssh_key" | "login_password">("ssh_key");
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [description, setDescription] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: () =>
      api("POST", "/api/secrets", {
        type, name, username, description,
        ...(type === "ssh_key" ? { privateKey, passphrase } : { password }),
      }),
    onSuccess: onSaved,
    onError: (e: Error) => setErr(e.message),
  });

  return (
    <Modal
      open wide
      onClose={onClose}
      title="New Secret"
      footer={<>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button onClick={() => { setErr(null); mut.mutate(); }} disabled={mut.isPending || !name}>
          {mut.isPending ? "Creating…" : "Create"}
        </Button>
      </>}
    >
      <div className="space-y-3">
        <Select
          label="Type"
          value={type}
          onChange={(v) => setType(v as typeof type)}
          options={[
            { value: "ssh_key", label: "SSH Key", description: "Private key (PEM/OpenSSH)" },
            { value: "login_password", label: "Login / Password", description: "Username + password pair" },
          ]}
        />
        <Field label="Name *"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="my-ssh-key" /></Field>
        <Field label="Username"><Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="root" /></Field>
        <Field label="Description"><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        {type === "ssh_key" ? (
          <>
            <Field label="Private Key *">
              <textarea
                value={privateKey} onChange={(e) => setPrivateKey(e.target.value)} rows={8}
                placeholder="-----BEGIN OPENSSH PRIVATE KEY-----…"
                className="w-full font-mono text-xs rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 py-1.5"
              />
            </Field>
            <Field label="Passphrase (optional)"><Input type="password" value={passphrase} onChange={(e) => setPassphrase(e.target.value)} /></Field>
          </>
        ) : (
          <Field label="Password *"><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        )}
        {err && <div className="text-xs text-red-500">{err}</div>}
      </div>
    </Modal>
  );
}

function EditSecretDialog({ secret, onClose, onSaved }: { secret: Secret; onClose: () => void; onSaved: () => void }) {
  const [username, setUsername] = useState(secret.username || "");
  const [description, setDescription] = useState(secret.description || "");
  const [privateKey, setPrivateKey] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { username, description, type: secret.type };
      if (secret.type === "ssh_key") {
        if (privateKey) body.privateKey = privateKey;
        if (passphrase) body.passphrase = passphrase;
      } else if (secret.type === "login_password") {
        if (password) body.password = password;
      } else if (secret.name === "vault_pass") {
        if (password) body.password = password;
      }
      return api("PUT", `/api/secrets/${encodeURIComponent(secret.name)}`, body);
    },
    onSuccess: onSaved,
    onError: (e: Error) => setErr(e.message),
  });

  return (
    <Modal
      open wide
      onClose={onClose}
      title={`Edit "${secret.name}"`}
      footer={<>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button onClick={() => { setErr(null); mut.mutate(); }} disabled={mut.isPending}>
          {mut.isPending ? "Saving…" : "Save"}
        </Button>
      </>}
    >
      <div className="space-y-3">
        <div className="text-xs text-[var(--color-muted-foreground)]">Type: <Badge variant="default" className="text-[10px]">{secret.type}</Badge></div>
        <Field label="Username"><Input value={username} onChange={(e) => setUsername(e.target.value)} /></Field>
        <Field label="Description"><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        {secret.type === "ssh_key" && (
          <>
            <Field label="Rotate Private Key (leave empty to keep)">
              <textarea value={privateKey} onChange={(e) => setPrivateKey(e.target.value)} rows={6}
                className="w-full font-mono text-xs rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 py-1.5" />
            </Field>
            <Field label="Passphrase (optional)"><Input type="password" value={passphrase} onChange={(e) => setPassphrase(e.target.value)} /></Field>
          </>
        )}
        {(secret.type === "login_password" || secret.name === "vault_pass") && (
          <Field label="Rotate Password (leave empty to keep)"><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        )}
        {err && <div className="text-xs text-red-500">{err}</div>}
      </div>
    </Modal>
  );
}

// =====================================================================
// One-shot: create/update an encrypted variable in group_vars or host_vars
// =====================================================================
type VarScope = "group" | "host";
type VarFileEntry = { name: string; path: string };

function EncryptVariableDialog({
  vaults, onClose, onSaved,
}: { vaults: Vault[]; onClose: () => void; onSaved: () => void }) {
  const [scope, setScope] = useState<VarScope>("group");
  const [file, setFile] = useState<string>("");
  const [filePath, setFilePath] = useState<string>("");
  const [varName, setVarName] = useState("");
  const [varValue, setVarValue] = useState("");
  const [vaultId, setVaultId] = useState<string>(vaults[0]?.id ?? "");
  const [err, setErr] = useState<string | null>(null);

  const filesQ = useQuery({
    queryKey: ["var-files", scope],
    queryFn: () =>
      api<{ success: boolean; files: VarFileEntry[] }>(
        "GET",
        scope === "group" ? "/api/group_vars/list" : "/api/host_vars/list",
      ),
  });
  const files: VarFileEntry[] = filesQ.data?.files ?? [];

  const duplicateNames = useMemo(() => {
    const counts = new Map<string, number>();
    files.forEach((f) => counts.set(f.name, (counts.get(f.name) ?? 0) + 1));
    return counts;
  }, [files]);

  const selectedEntry =
    (filePath ? files.find((f) => f.path === filePath) : undefined) ??
    files.find((f) => f.name === file);

  const mut = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Pick a target file");
      if (!varName.trim()) throw new Error("Variable name is required");
      if (!vaultId) throw new Error("Pick a vault");

      // Derive inventory_file from path (strip trailing /group_vars/... or /host_vars/...)
      const path = selectedEntry?.path ?? filePath;
      let inventory_file = "";
      const m = path.match(/^(.*)\/(group_vars|host_vars)\//);
      if (m && m[1]) inventory_file = m[1];

      // 1. Fetch current content. Let the backend auto-resolve which vault
      // decrypts an already-encrypted file — the chosen vault below is only
      // used to re-encrypt on save, and may differ from the file's original
      // vault. Passing it here would cause a decrypt-failure 500.
      const getUrl =
        (scope === "group" ? "/api/group_vars/get" : "/api/host_vars/get") +
        `?file=${encodeURIComponent(file)}` +
        (path ? `&path=${encodeURIComponent(path)}` : "") +
        (inventory_file ? `&inventory_file=${encodeURIComponent(inventory_file)}` : "");
      let getRes: {
        success: boolean; content?: string; encrypted?: boolean; vaultId?: string; vaultIdRequired?: boolean; error?: string;
      };
      try {
        getRes = await api("GET", getUrl);
      } catch (e) {
        // 404 = file doesn't exist yet, treat as empty. Anything else bubbles up.
        if (e instanceof ApiError && e.status === 404) {
          getRes = { success: true, content: "" };
        } else {
          throw e;
        }
      }
      if (getRes.vaultIdRequired && !getRes.content) {
        throw new Error("File is encrypted and no configured vault key can decrypt it. Add the correct vault key first.");
      }

      // 2. Merge var into YAML.
      let data: Record<string, unknown> = {};
      const raw = getRes.content ?? "";
      if (raw.trim()) {
        try {
          const parsed = yaml.load(raw);
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            data = parsed as Record<string, unknown>;
          }
        } catch (e) {
          throw new Error(`Existing file has invalid YAML: ${(e as Error).message}`);
        }
      }
      data[varName.trim()] = varValue;
      const newContent = yaml.dump(data, { lineWidth: 1000, noRefs: true });

      // 3. Save encrypted. Existing encrypted files keep their original vault
      // so adding one variable does not accidentally rotate/replace another vault.
      const saveUrl = scope === "group" ? "/api/group_vars/save" : "/api/host_vars/save";
      const saveVaultId = getRes.encrypted && getRes.vaultId ? getRes.vaultId : vaultId;
      return api<{ success: boolean; error?: string }>("POST", saveUrl, {
        file,
        content: newContent,
        path: path || undefined,
        inventory_file: inventory_file || undefined,
        vaultId: saveVaultId,
      });
    },
    onSuccess: onSaved,
    onError: (e: Error) => setErr(e.message),
  });

  return (
    <Modal
      open size="2xl"
      onClose={onClose}
      title="Encrypt a Variable"
      footer={<>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button onClick={() => { setErr(null); mut.mutate(); }}
          disabled={mut.isPending || !file || !varName || !vaultId}>
          {mut.isPending ? "Saving…" : "Encrypt & Save"}
        </Button>
      </>}
    >
      <div className="space-y-4">
        <div className="text-xs text-[var(--color-muted-foreground)] bg-[var(--color-accent)]/40 border border-[var(--color-border)] rounded-md p-3 leading-relaxed">
          Adds <span className="font-mono">var_name: value</span> to a group_vars / host_vars file
          and encrypts the whole file with the chosen vault — no need to visit Hosts &amp; Groups.
          <div className="mt-1 opacity-80">
            Tip: if the dropdown is empty, just type a filename like <span className="font-mono">all.yml</span> —
            it will be created automatically.
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Select
            label="Scope"
            value={scope}
            onChange={(v) => { setScope(v as VarScope); setFile(""); setFilePath(""); }}
            options={[
              { value: "group", label: "group_vars", description: "Applies to a host group" },
              { value: "host", label: "host_vars", description: "Applies to a single host" },
            ]}
          />

          <Field label={scope === "group" ? "Target group file *" : "Target host file *"}>
            <Input
              value={file}
              onChange={(e) => { setFile(e.target.value); setFilePath(""); }}
              list="var-file-suggestions"
              placeholder={
                filesQ.isLoading ? "Loading…" :
                files.length ? "Pick or type a filename…" :
                scope === "group" ? "e.g. all.yml" : "e.g. myhost.yml"
              }
            />
            <datalist id="var-file-suggestions">
              {files.map((f) => (
                <option key={f.path} value={f.name}>{f.path}</option>
              ))}
            </datalist>
            {files.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {files.slice(0, 8).map((f) => (
                  <button
                    key={f.path}
                    type="button"
                    onClick={() => { setFile(f.name); setFilePath(f.path); }}
                    className={cn(
                      "text-[11px] px-2 py-0.5 rounded border border-[var(--color-border)] hover:bg-[var(--color-accent)]",
                      (filePath ? filePath === f.path : file === f.name) && "bg-[var(--color-accent)]",
                    )}
                    title={f.path}
                  >
                    {(duplicateNames.get(f.name) ?? 0) > 1 ? f.path : f.name}
                  </button>
                ))}
              </div>
            )}
          </Field>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="Variable name *">
            <Input value={varName} onChange={(e) => setVarName(e.target.value)} placeholder="zot_registry_password" />
          </Field>

          <Select
            label="Encrypt with vault *"
            value={vaultId}
            onChange={setVaultId}
            options={vaults.map((v) => ({
              value: v.id,
              label: v.name,
              description: v.keyName ? `key: ${v.keyName}` : undefined,
            }))}
            placeholder="Pick a vault…"
          />
        </div>

        <Field label="Value *">
          <textarea
            value={varValue}
            onChange={(e) => setVarValue(e.target.value)}
            rows={5}
            className="w-full font-mono text-xs rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 py-1.5"
            placeholder="P@$$w0rk"
          />
        </Field>

        {err && <div className="text-xs text-red-500">{err}</div>}
      </div>
    </Modal>
  );
}

