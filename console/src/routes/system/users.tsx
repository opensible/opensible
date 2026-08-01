import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, useEffect } from "react";
import { Users as UsersIcon, Shield, Plus, Pencil, Trash2, KeyRound, Search, X, Check } from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { api, ApiError, getStoredUser } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/system/users")({ component: UsersPage });

type Role = {
  id: string;
  name: string;
  description?: string;
  permissions: string[];
  permission_names?: string[];
  permission_details?: Permission[];
  created_at?: string;
  updated_at?: string;
};
type Permission = {
  id: string;
  name: string;
  description?: string;
  resource: string;
  action: string;
};
type User = {
  id: string;
  username: string;
  email?: string | null;
  is_active: boolean;
  roles: string[];
  role_names?: string[];
  role_details?: { id: string; name: string; description?: string }[];
  created_at?: string;
  updated_at?: string;
  last_login?: string | null;
};

function UsersPage() {
  const t = useT();
  const [tab, setTab] = useState<"users" | "roles">("users");
  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: "System" }, { label: t("page.users.title") }]} />
      <div>
        <h1 className="text-xl font-semibold">{t("page.users.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("page.users.subtitle")}</p>
      </div>


      <div className="border-b border-[var(--color-border)] flex gap-1">
        <TabBtn active={tab === "users"} onClick={() => setTab("users")} icon={UsersIcon}>{t("common.users")}</TabBtn>
        <TabBtn active={tab === "roles"} onClick={() => setTab("roles")} icon={Shield}>{t("common.roles")}</TabBtn>
      </div>

      {tab === "users" ? <UsersTab /> : <RolesTab />}
    </div>
  );
}

function TabBtn({ active, onClick, icon: Icon, children }: { active: boolean; onClick: () => void; icon: typeof UsersIcon; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-2 px-4 py-2.5 text-sm border-b-2 -mb-px transition-colors",
        active
          ? "border-[var(--color-primary)] text-[var(--color-primary)] font-medium"
          : "border-transparent text-muted-foreground hover:text-foreground"
      )}
    >
      <Icon className="h-4 w-4" />
      {children}
    </button>
  );
}

/* ---------------- USERS TAB ---------------- */
function UsersTab() {
  const t = useT();
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<User | "new" | null>(null);
  const [deleting, setDeleting] = useState<User | null>(null);
  const [pwUser, setPwUser] = useState<User | null>(null);

  const usersQ = useQuery({
    queryKey: ["users"],
    queryFn: () => api<{ success: boolean; users: User[] }>("GET", "/api/users"),
  });
  const rolesQ = useQuery({
    queryKey: ["roles"],
    queryFn: () => api<{ success: boolean; roles: Role[] }>("GET", "/api/roles"),
  });

  const filtered = useMemo(() => {
    const items = usersQ.data?.users ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter(u =>
      u.username.toLowerCase().includes(q) ||
      (u.email ?? "").toLowerCase().includes(q) ||
      (u.role_names ?? []).some(r => r.toLowerCase().includes(q))
    );
  }, [usersQ.data, search]);

  const delMut = useMutation({
    mutationFn: (id: string) => api("DELETE", `/api/users/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); setDeleting(null); },
  });

  const currentUser = getStoredUser<{ user_id?: string; id?: string }>();
  const meId = currentUser?.user_id ?? currentUser?.id;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <div>
          <CardTitle>{t("common.users")}</CardTitle>
          <CardDescription>Create, edit, and assign roles.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search users…" className="pl-8 w-64" />
          </div>
          <Button onClick={() => setEditing("new")}>
            <Plus className="h-4 w-4 mr-1" /> New User
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
              <tr>
                <Th>{t("common.username")}</Th>
                <Th>{t("common.email")}</Th>
                <Th>{t("common.roles")}</Th>
                <Th>{t("common.status")}</Th>
                <Th>{t("common.lastLogin")}</Th>
                <Th className="text-right">{t("common.actions")}</Th>
              </tr>
            </thead>
            <tbody>
              {usersQ.isLoading && <tr><td colSpan={6} className="p-6 text-center text-muted-foreground">Loading…</td></tr>}
              {!usersQ.isLoading && filtered.length === 0 && (
                <tr><td colSpan={6} className="p-6 text-center text-muted-foreground">{t("users.emptyUsers")}</td></tr>
              )}
              {filtered.map(u => (
                <tr key={u.id} className="border-t hover:bg-muted/30">
                  <Td className="font-medium">{u.username}{u.id === meId && <span className="ml-2 text-xs text-muted-foreground">(you)</span>}</Td>
                  <Td className="text-muted-foreground">{u.email || "—"}</Td>
                  <Td>
                    <div className="flex flex-wrap gap-1">
                      {(u.role_names ?? []).map(r => <Badge key={r} variant="default">{r}</Badge>)}
                      {(!u.role_names || u.role_names.length === 0) && <span className="text-muted-foreground">—</span>}
                    </div>
                  </Td>
                  <Td>
                    {u.is_active
                      ? <Badge className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100">{t("common.active")}</Badge>
                      : <Badge variant="default">{t("common.disabled")}</Badge>}
                  </Td>
                  <Td className="text-muted-foreground text-xs">{u.last_login ? new Date(u.last_login).toLocaleString() : "Never"}</Td>
                  <Td className="text-right">
                    <div className="inline-flex gap-1">
                      <Button size="sm" variant="ghost" onClick={() => setPwUser(u)} title="Set password"><KeyRound className="h-4 w-4" /></Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditing(u)} title="Edit"><Pencil className="h-4 w-4" /></Button>
                      <Button size="sm" variant="ghost" onClick={() => setDeleting(u)} disabled={u.id === meId} title="Delete">
                        <Trash2 className="h-4 w-4 text-red-500" />
                      </Button>
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>

      {editing && (
        <UserEditor
          user={editing === "new" ? null : editing}
          roles={rolesQ.data?.roles ?? []}
          onClose={() => setEditing(null)}
          onSaved={() => { qc.invalidateQueries({ queryKey: ["users"] }); setEditing(null); }}
        />
      )}
      {pwUser && (
        <PasswordDialog
          user={pwUser}
          isSelf={pwUser.id === meId}
          onClose={() => setPwUser(null)}
        />
      )}
      <ConfirmDialog
        open={!!deleting}
        title="Delete user?"
        description={deleting ? <>This permanently deletes <b>{deleting.username}</b>. This action cannot be undone.</> : null}
        confirmLabel="Delete"
        variant="destructive"
        busy={delMut.isPending}
        onConfirm={() => deleting && delMut.mutate(deleting.id)}
        onCancel={() => setDeleting(null)}
      />
    </Card>
  );
}

function UserEditor({ user, roles, onClose, onSaved }: { user: User | null; roles: Role[]; onClose: () => void; onSaved: () => void }) {
  const t = useT();
  const isNew = !user;
  const [username, setUsername] = useState(user?.username ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [password, setPassword] = useState("");
  const [isActive, setIsActive] = useState(user?.is_active ?? true);
  const [selectedRoles, setSelectedRoles] = useState<string[]>(user?.roles ?? []);
  const [err, setErr] = useState<string | null>(null);

  const saveMut = useMutation({
    mutationFn: async () => {
      if (isNew) {
        return api("POST", "/api/users", { username, email: email || null, password, roles: selectedRoles });
      }
      const body: any = { username, email: email || null, is_active: isActive, roles: selectedRoles };
      if (password) body.password = password;
      return api("PUT", `/api/users/${user!.id}`, body);
    },
    onSuccess: onSaved,
    onError: (e: any) => setErr(e instanceof ApiError ? e.message : String(e)),
  });

  const toggleRole = (id: string) =>
    setSelectedRoles(r => r.includes(id) ? r.filter(x => x !== id) : [...r, id]);

  return (
    <Modal title={isNew ? "Create User" : `Edit User — ${user!.username}`} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Username *">
          <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="jdoe" />
        </Field>
        <Field label="Email">
          <Input value={email ?? ""} onChange={(e) => setEmail(e.target.value)} placeholder="user@example.com" />
        </Field>
        <Field label={isNew ? "Password *" : "Password (leave empty to keep current)"}>
          <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" />
        </Field>
        {!isNew && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
            Active
          </label>
        )}
        <Field label="Roles">
          <div className="border border-[var(--color-border)] rounded-md max-h-56 overflow-y-auto divide-y divide-[var(--color-border)]">
            {roles.length === 0 && <div className="p-3 text-sm text-muted-foreground">{t("users.noRoles")}</div>}
            {roles.map(r => {
              const checked = selectedRoles.includes(r.id);
              return (
                <label key={r.id} className="flex items-start gap-3 p-2.5 hover:bg-muted/40 cursor-pointer">
                  <input type="checkbox" checked={checked} onChange={() => toggleRole(r.id)} className="mt-1" />
                  <div className="min-w-0">
                    <div className="text-sm font-medium">{r.name}</div>
                    {r.description && <div className="text-xs text-muted-foreground">{r.description}</div>}
                  </div>
                </label>
              );
            })}
          </div>
        </Field>
        {err && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">{err}</div>}
      </div>
      <ModalFooter>
        <Button variant="ghost" onClick={onClose} disabled={saveMut.isPending}>{t("common.cancel")}</Button>
        <Button onClick={() => { setErr(null); saveMut.mutate(); }} disabled={saveMut.isPending || !username || (isNew && !password)}>
          {saveMut.isPending ? "Saving…" : (isNew ? "Create" : "Save Changes")}
        </Button>
      </ModalFooter>
    </Modal>
  );
}

function PasswordDialog({ user, isSelf, onClose }: { user: User; isSelf: boolean; onClose: () => void }) {
  const t = useT();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: () => {
      const body: any = { password: next };
      if (isSelf) body.current_password = current;
      return api("PUT", `/api/users/${user.id}`, body);
    },
    onSuccess: onClose,
    onError: (e: any) => setErr(e instanceof ApiError ? e.message : String(e)),
  });

  return (
    <Modal title={`Set password — ${user.username}`} onClose={onClose}>
      <div className="space-y-3">
        {isSelf && (
          <Field label="Current password *">
            <Input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} />
          </Field>
        )}
        <Field label="New password * (min 6 chars)">
          <Input type="password" value={next} onChange={(e) => setNext(e.target.value)} />
        </Field>
        <Field label="Confirm new password *">
          <Input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </Field>
        {err && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">{err}</div>}
      </div>
      <ModalFooter>
        <Button variant="ghost" onClick={onClose} disabled={mut.isPending}>{t("common.cancel")}</Button>
        <Button
          onClick={() => {
            setErr(null);
            if (next.length < 6) return setErr("Password must be at least 6 characters");
            if (next !== confirm) return setErr("Passwords do not match");
            mut.mutate();
          }}
          disabled={mut.isPending}
        >
          {mut.isPending ? "Saving…" : "Update Password"}
        </Button>
      </ModalFooter>
    </Modal>
  );
}

/* ---------------- ROLES TAB ---------------- */
function RolesTab() {
  const t = useT();
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<Role | "new" | null>(null);
  const [deleting, setDeleting] = useState<Role | null>(null);

  const rolesQ = useQuery({
    queryKey: ["roles"],
    queryFn: () => api<{ success: boolean; roles: Role[] }>("GET", "/api/roles"),
  });
  const permsQ = useQuery({
    queryKey: ["permissions"],
    queryFn: () => api<{ success: boolean; permissions: Permission[] }>("GET", "/api/permissions"),
  });

  const filtered = useMemo(() => {
    const items = rolesQ.data?.roles ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter(r => r.name.toLowerCase().includes(q) || (r.description ?? "").toLowerCase().includes(q));
  }, [rolesQ.data, search]);

  const delMut = useMutation({
    mutationFn: (id: string) => api("DELETE", `/api/roles/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["roles"] }); setDeleting(null); },
  });

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <div>
          <CardTitle>{t("common.roles")}</CardTitle>
          <CardDescription>Group permissions into roles assigned to users.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search roles…" className="pl-8 w-64" />
          </div>
          <Button onClick={() => setEditing("new")}>
            <Plus className="h-4 w-4 mr-1" /> New Role
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
              <tr>
                <Th>{t("common.name")}</Th>
                <Th>{t("common.description")}</Th>
                <Th>{t("common.permissions")}</Th>
                <Th className="text-right">{t("common.actions")}</Th>
              </tr>
            </thead>
            <tbody>
              {rolesQ.isLoading && <tr><td colSpan={4} className="p-6 text-center text-muted-foreground">Loading…</td></tr>}
              {!rolesQ.isLoading && filtered.length === 0 && <tr><td colSpan={4} className="p-6 text-center text-muted-foreground">{t("users.emptyRolesSearch")}</td></tr>}
              {filtered.map(r => (
                <tr key={r.id} className="border-t hover:bg-muted/30">
                  <Td className="font-medium">{r.name}</Td>
                  <Td className="text-muted-foreground">{r.description || "—"}</Td>
                  <Td>
                    <Badge variant="default">{r.permissions.length} perms</Badge>
                  </Td>
                  <Td className="text-right">
                    <div className="inline-flex gap-1">
                      <Button size="sm" variant="ghost" onClick={() => setEditing(r)}><Pencil className="h-4 w-4" /></Button>
                      <Button size="sm" variant="ghost" onClick={() => setDeleting(r)}><Trash2 className="h-4 w-4 text-red-500" /></Button>
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>

      {editing && (
        <RoleEditor
          role={editing === "new" ? null : editing}
          permissions={permsQ.data?.permissions ?? []}
          onClose={() => setEditing(null)}
          onSaved={() => { qc.invalidateQueries({ queryKey: ["roles"] }); setEditing(null); }}
        />
      )}
      <ConfirmDialog
        open={!!deleting}
        title="Delete role?"
        description={deleting ? <>This deletes role <b>{deleting.name}</b>. Users assigned this role will lose its permissions.</> : null}
        confirmLabel="Delete"
        variant="destructive"
        busy={delMut.isPending}
        onConfirm={() => deleting && delMut.mutate(deleting.id)}
        onCancel={() => setDeleting(null)}
      />
    </Card>
  );
}

function RoleEditor({ role, permissions, onClose, onSaved }: { role: Role | null; permissions: Permission[]; onClose: () => void; onSaved: () => void }) {
  const t = useT();
  const isNew = !role;
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [selected, setSelected] = useState<string[]>(role?.permissions ?? []);
  const [filter, setFilter] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const grouped = useMemo(() => {
    const f = filter.trim().toLowerCase();
    const list = f ? permissions.filter(p => p.name.toLowerCase().includes(f) || (p.description ?? "").toLowerCase().includes(f) || p.resource.toLowerCase().includes(f)) : permissions;
    const map = new Map<string, Permission[]>();
    list.forEach(p => {
      if (!map.has(p.resource)) map.set(p.resource, []);
      map.get(p.resource)!.push(p);
    });
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [permissions, filter]);

  const toggle = (id: string) => setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);
  const toggleGroup = (perms: Permission[]) => {
    const ids = perms.map(p => p.id);
    const allOn = ids.every(id => selected.includes(id));
    setSelected(s => allOn ? s.filter(x => !ids.includes(x)) : Array.from(new Set([...s, ...ids])));
  };

  const saveMut = useMutation({
    mutationFn: () => {
      const body = { name, description, permissions: selected };
      return isNew ? api("POST", "/api/roles", body) : api("PUT", `/api/roles/${role!.id}`, body);
    },
    onSuccess: onSaved,
    onError: (e: any) => setErr(e instanceof ApiError ? e.message : String(e)),
  });

  return (
    <Modal title={isNew ? "Create Role" : `Edit Role — ${role!.name}`} onClose={onClose} wide>
      <div className="grid md:grid-cols-2 gap-6">
        <div className="space-y-4">
          <Field label="Name *">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="operator" />
          </Field>
          <Field label="Description">
            <Input value={description ?? ""} onChange={(e) => setDescription(e.target.value)} placeholder="Short description" />
          </Field>
          <div className="text-xs text-muted-foreground">
            Selected: <b>{selected.length}</b> / {permissions.length} permissions
          </div>
        </div>
        <div>
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm font-medium">{t("common.permissions")}</div>
            <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter…" className="w-44 h-8" />
          </div>
          <div className="border border-[var(--color-border)] rounded-md max-h-[420px] overflow-y-auto">
            {grouped.length === 0 && <div className="p-3 text-sm text-muted-foreground">{t("users.noPermissions")}</div>}
            {grouped.map(([resource, perms]) => {
              const allOn = perms.every(p => selected.includes(p.id));
              return (
                <div key={resource} className="border-b last:border-b-0 border-[var(--color-border)]">
                  <button
                    type="button"
                    onClick={() => toggleGroup(perms)}
                    className="w-full flex items-center justify-between px-3 py-2 bg-muted/50 text-xs uppercase tracking-wide font-semibold"
                  >
                    <span>{resource}</span>
                    <span className="inline-flex items-center gap-1 normal-case text-[11px] text-muted-foreground">
                      {allOn ? <Check className="h-3 w-3" /> : null}
                      toggle all
                    </span>
                  </button>
                  {perms.map(p => {
                    const checked = selected.includes(p.id);
                    return (
                      <label key={p.id} className="flex items-start gap-3 px-3 py-2 hover:bg-muted/40 cursor-pointer text-sm">
                        <input type="checkbox" checked={checked} onChange={() => toggle(p.id)} className="mt-1" />
                        <div className="min-w-0 flex-1">
                          <div className="font-mono text-xs">{p.name}</div>
                          {p.description && <div className="text-xs text-muted-foreground">{p.description}</div>}
                        </div>
                        <Badge variant="default" className="shrink-0 text-[10px]">{p.action}</Badge>
                      </label>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>
      </div>
      {err && <div className="mt-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">{err}</div>}
      <ModalFooter>
        <Button variant="ghost" onClick={onClose} disabled={saveMut.isPending}>{t("common.cancel")}</Button>
        <Button onClick={() => { setErr(null); saveMut.mutate(); }} disabled={saveMut.isPending || !name}>
          {saveMut.isPending ? "Saving…" : (isNew ? "Create" : "Save Changes")}
        </Button>
      </ModalFooter>
    </Modal>
  );
}

/* ---------------- shared bits ---------------- */
function Th({ children, className }: { children: React.ReactNode; className?: string }) {
  return <th className={cn("text-left font-semibold px-4 py-2.5", className)}>{children}</th>;
}
function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={cn("px-4 py-2.5 align-middle", className)}>{children}</td>;
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs font-medium text-muted-foreground mb-1">{label}</div>
      {children}
    </label>
  );
}
function Modal({ title, children, onClose, wide }: { title: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 overflow-y-auto" onClick={onClose}>
      <div
        className={cn("bg-[var(--color-card)] rounded-lg shadow-2xl w-full border border-[var(--color-border)] mt-12", wide ? "max-w-3xl" : "max-w-lg")}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--color-border)]">
          <h2 className="font-semibold">{title}</h2>
          <button onClick={onClose} className="p-1 rounded hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}
function ModalFooter({ children }: { children: React.ReactNode }) {
  return <div className="flex justify-end gap-2 mt-5 pt-4 border-t border-[var(--color-border)]">{children}</div>;
}
