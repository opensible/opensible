import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Shield, RefreshCw, ChevronRight, ChevronDown, FileCode, Folder, Save, X, Plus, Trash2, FolderPlus, FilePlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { YamlEditor } from "@/components/ui/yaml-editor";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

function Modal({ open, onClose, title, children, footer }: {
  open: boolean; onClose: () => void; title: string; children: React.ReactNode; footer?: React.ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="bg-[var(--color-card)] rounded-lg shadow-2xl w-full max-w-md border border-[var(--color-border)] flex flex-col max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-3 border-b border-[var(--color-border)] flex items-center justify-between">
          <h2 className="text-base font-semibold">{title}</h2>
          <button onClick={onClose} className="text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] text-xl leading-none">×</button>
        </div>
        <div className="p-5 overflow-y-auto flex-1 space-y-3">{children}</div>
        {footer && <div className="px-5 py-3 border-t border-[var(--color-border)] flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

type Node = {
  name: string;
  type?: "file" | "directory" | "role" | "pack";
  enabled?: boolean;
  children?: Node[];
  path?: string;
  packId?: string;
  roleName?: string;
};

type RoleSelection = { pack: string; role: string; path: string };
type RoleFile = { path: string; name: string; type: string; size?: number };

type PromptState =
  | { kind: "new-role" }
  | { kind: "new-file"; defaultDir?: string }
  | { kind: "new-folder"; defaultDir?: string }
  | null;

export function RolesPanel() {
  const t = useT();
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<RoleSelection | null>(null);
  const [openFile, setOpenFile] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [original, setOriginal] = useState("");
  const [prompt, setPrompt] = useState<PromptState>(null);
  const [promptValue, setPromptValue] = useState("");
  const [promptPack, setPromptPack] = useState("");

  const storage = useQuery({
    queryKey: ["roles", "storage"],
    queryFn: () => api<{ success: boolean; storageRoot: string; tree: Node[] }>("GET", "/api/roles/storage"),
  });
  const usageQ = useQuery({
    queryKey: ["roles", "usage"],
    queryFn: () => api<{ usage: Record<string, { playbooks: string[]; templates: string[] }> }>("GET", "/api/roles/usage"),
  });
  const usage = usageQ.data?.usage ?? {};
  const tree: Node[] = useMemo(() => storage.data?.tree ?? [], [storage.data]);

  const filesQ = useQuery({
    queryKey: ["role-files", selected?.pack, selected?.role],
    queryFn: () => api<{ success: boolean; files: RoleFile[] }>("GET", `/api/roles/files/${selected!.path}`),
    enabled: !!selected,
  });
  const files: RoleFile[] = filesQ.data?.files ?? [];

  const fileQ = useQuery({
    queryKey: ["role-file", selected?.pack, selected?.role, openFile],
    queryFn: async () => {
      const r = await api<{ success: boolean; content: string }>(
        "GET",
        `/api/roles/file/${selected!.pack}/${selected!.role}/${openFile}`
      );
      setContent(r.content || "");
      setOriginal(r.content || "");
      return r;
    },
    enabled: !!selected && !!openFile,
  });

  const saveMut = useMutation({
    mutationFn: () =>
      api("PUT", `/api/roles/file/${selected!.pack}/${selected!.role}/${openFile}`, { content }),
    onSuccess: () => {
      setOriginal(content);
      toast.success("File saved");
      qc.invalidateQueries({ queryKey: ["role-file"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const createRoleMut = useMutation({
    mutationFn: (vars: { name: string; pack?: string }) => api("POST", "/api/roles/role", vars),
    onSuccess: () => {
      toast.success("Role created");
      setPrompt(null);
      qc.invalidateQueries({ queryKey: ["roles", "storage"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const deleteRoleMut = useMutation({
    mutationFn: (path: string) => api("DELETE", `/api/roles/role/${path}`),
    onSuccess: () => {
      toast.success("Role deleted");
      setSelected(null);
      setOpenFile(null);
      qc.invalidateQueries({ queryKey: ["roles", "storage"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const createFileMut = useMutation({
    mutationFn: (filePath: string) =>
      api("POST", `/api/roles/file/${selected!.pack}/${selected!.role}/${filePath}`, { content: "" }),
    onSuccess: (_d, filePath) => {
      toast.success("File created");
      setPrompt(null);
      setOpenFile(filePath);
      qc.invalidateQueries({ queryKey: ["role-files", selected?.pack, selected?.role] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const createFolderMut = useMutation({
    mutationFn: (folderPath: string) =>
      api("POST", `/api/roles/folder/${selected!.pack}/${selected!.role}/${folderPath}`),
    onSuccess: () => {
      toast.success("Folder created");
      setPrompt(null);
      qc.invalidateQueries({ queryKey: ["role-files", selected?.pack, selected?.role] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const deleteFileMut = useMutation({
    mutationFn: (filePath: string) =>
      api("DELETE", `/api/roles/file/${selected!.pack}/${selected!.role}/${filePath}`),
    onSuccess: (_d, filePath) => {
      toast.success("Deleted");
      if (openFile === filePath) { setOpenFile(null); setContent(""); setOriginal(""); }
      qc.invalidateQueries({ queryKey: ["role-files", selected?.pack, selected?.role] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const dirty = content !== original;

  function submitPrompt() {
    const v = promptValue.trim();
    if (!v) return;
    if (prompt?.kind === "new-role") {
      createRoleMut.mutate({ name: v, pack: promptPack.trim() || undefined });
    } else if (prompt?.kind === "new-file" && selected) {
      createFileMut.mutate(v);
    } else if (prompt?.kind === "new-folder" && selected) {
      createFolderMut.mutate(v);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {storage.data?.storageRoot ?? "Roles storage"} — {countLeaves(tree)} roles
        </p>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => { setPromptValue(""); setPromptPack(""); setPrompt({ kind: "new-role" }); }}>
            <Plus className="h-4 w-4 mr-1.5" /> New Role
          </Button>
          <Button variant="outline" size="sm" onClick={() => storage.refetch()}>
            <RefreshCw className="h-4 w-4 mr-1.5" /> Refresh
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[320px_280px_1fr] gap-4">
        <Card>
          <CardHeader className="py-3"><CardTitle className="text-sm">Role Tree</CardTitle></CardHeader>
          <CardContent className="p-2 max-h-[70vh] overflow-auto">
            {storage.isLoading && <div className="text-sm text-[var(--color-muted-foreground)] p-3">Loading…</div>}
            {!storage.isLoading && tree.length === 0 && (
              <div className="text-xs text-[var(--color-muted-foreground)] p-3">No roles found. Configure the repository in Project Settings or click "New Role".</div>
            )}
            {tree.map((n) => (
              <TreeNode
                key={keyOf(n)} node={n} depth={0} expanded={expanded} setExpanded={setExpanded} parentPath=""
                onPick={(sel) => { setSelected(sel); setOpenFile(null); }} selected={selected}
                onDelete={(path) => { if (confirm(`Delete role "${path}"? This cannot be undone.`)) deleteRoleMut.mutate(path); }}
                usage={usage}
              />
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="py-3 flex flex-row items-center justify-between gap-2">
            <CardTitle className="text-sm truncate">{selected ? selected.role : "Files"}</CardTitle>
            {selected && (
              <div className="flex gap-1">
                <Button size="icon" variant="ghost" title="New file" className="h-7 w-7"
                  onClick={() => { setPromptValue(""); setPrompt({ kind: "new-file" }); }}>
                  <FilePlus className="h-3.5 w-3.5" />
                </Button>
                <Button size="icon" variant="ghost" title="New folder" className="h-7 w-7"
                  onClick={() => { setPromptValue(""); setPrompt({ kind: "new-folder" }); }}>
                  <FolderPlus className="h-3.5 w-3.5" />
                </Button>
              </div>
            )}
          </CardHeader>
          <CardContent className="p-1 max-h-[70vh] overflow-auto">
            {!selected && <div className="text-xs text-[var(--color-muted-foreground)] p-3">Select a role to view its files.</div>}
            {selected && filesQ.isLoading && <div className="text-xs text-[var(--color-muted-foreground)] p-3">Loading…</div>}
            {selected && !filesQ.isLoading && files.length === 0 && (
              <div className="text-xs text-[var(--color-muted-foreground)] p-3">Empty role. Use the + buttons above to add a file or folder.</div>
            )}
            {files.map((f) => (
              <div
                key={f.path}
                className={`group w-full flex items-center gap-2 px-2 py-1.5 rounded text-left text-sm hover:bg-[var(--color-accent)]/60 ${openFile === f.path ? "bg-[var(--color-accent)] text-[var(--color-primary)]" : ""}`}
              >
                <button onClick={() => setOpenFile(f.path)} className="flex items-center gap-2 flex-1 min-w-0">
                  <FileCode className="h-3.5 w-3.5 shrink-0 text-[var(--color-muted-foreground)]" />
                  <span className="truncate font-mono text-xs">{f.path}</span>
                </button>
                <button
                  title="Delete file"
                  onClick={(e) => { e.stopPropagation(); if (confirm(`Delete "${f.path}"?`)) deleteFileMut.mutate(f.path); }}
                  className="opacity-0 group-hover:opacity-100 text-[var(--color-muted-foreground)] hover:text-red-500"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="py-3 flex flex-row items-center justify-between">
            <CardTitle className="text-sm truncate">
              {openFile ? <span className="font-mono text-xs">{selected?.pack}/{selected?.role}/{openFile}</span> : "Editor"}
            </CardTitle>
            {openFile && (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => { setOpenFile(null); setContent(""); setOriginal(""); }}>
                  <X className="h-3.5 w-3.5 mr-1" /> Close
                </Button>
                <Button size="sm" disabled={!dirty || saveMut.isPending} onClick={() => saveMut.mutate()}>
                  <Save className="h-3.5 w-3.5 mr-1" /> {saveMut.isPending ? "Saving…" : dirty ? "Save" : "Saved"}
                </Button>
              </div>
            )}
          </CardHeader>
          <CardContent className="p-3">
            {!openFile && <div className="text-xs text-[var(--color-muted-foreground)] p-6 text-center">Pick a file from the list to edit.</div>}
            {openFile && fileQ.isLoading && <div className="text-xs text-[var(--color-muted-foreground)] p-3">Loading…</div>}
            {openFile && !fileQ.isLoading && <YamlEditor value={content} onChange={setContent} height={500} />}
            {saveMut.isError && <div className="text-xs text-red-500 mt-2">{(saveMut.error as Error).message}</div>}
          </CardContent>
        </Card>
      </div>

      <Modal
        open={!!prompt}
        onClose={() => setPrompt(null)}
        title={
          prompt?.kind === "new-role" ? "Create new role"
            : prompt?.kind === "new-file" ? "Create new file"
              : prompt?.kind === "new-folder" ? "Create new folder" : ""
        }
        footer={
          <>
            <Button variant="outline" onClick={() => setPrompt(null)}>Cancel</Button>
            <Button onClick={submitPrompt}
              disabled={!promptValue.trim() || createRoleMut.isPending || createFileMut.isPending || createFolderMut.isPending}>
              Create
            </Button>
          </>
        }
      >
        {prompt?.kind === "new-role" && (
          <div>
            <label className="text-xs font-medium">Pack (optional)</label>
            <Input value={promptPack} onChange={(e) => setPromptPack(e.target.value)} placeholder="e.g. common" />
            <p className="text-[10px] text-[var(--color-muted-foreground)] mt-1">Leave empty to create at top level.</p>
          </div>
        )}
        <div>
          <label className="text-xs font-medium">
            {prompt?.kind === "new-role" ? "Role name" : prompt?.kind === "new-folder" ? "Folder path" : "File path"}
          </label>
          <Input
            autoFocus
            value={promptValue}
            onChange={(e) => setPromptValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submitPrompt(); }}
            placeholder={
              prompt?.kind === "new-role" ? "my-role"
                : prompt?.kind === "new-folder" ? "templates/configs"
                  : "tasks/install.yml"
            }
          />
        </div>
      </Modal>
    </div>
  );
}

function keyOf(n: Node): string { return n.path || n.name; }

function countLeaves(nodes: Node[]): number {
  let n = 0;
  for (const x of nodes) {
    const isRoleLeaf = x.type === "role" || (!x.children && x.path);
    if (isRoleLeaf) n++;
    else if (x.children) n += countLeaves(x.children);
  }
  return n;
}

function TreeNode({
  node, depth, expanded, setExpanded, parentPath, onPick, selected, onDelete, usage,
}: {
  node: Node; depth: number;
  expanded: Record<string, boolean>;
  setExpanded: (e: Record<string, boolean>) => void;
  parentPath: string;
  onPick: (s: RoleSelection) => void;
  selected: RoleSelection | null;
  onDelete: (path: string) => void;
  usage: Record<string, { playbooks: string[]; templates: string[] }>;
}) {
  const path = node.path || (parentPath ? `${parentPath}/${node.name}` : node.name);
  const isRole = node.type === "role" || (!node.children?.length && !!node.path);
  const isFolder = !isRole && Array.isArray(node.children);
  const key = path;
  const open = expanded[key] ?? depth < 1;

  if (isRole) {
    const parts = path.split("/");
    const pack = parts[0] ?? path;
    const roleName = parts.length === 1 ? (parts[0] ?? path) : parts.slice(1).join("/");
    const isSel = selected?.path === path;
    const u = usage[roleName] || usage[node.name];
    const pbCount = u?.playbooks.length ?? 0;
    const tplCount = u?.templates.length ?? 0;
    const tip = u
      ? [
          pbCount ? `Playbooks:\n  ${u.playbooks.join("\n  ")}` : "",
          tplCount ? `Templates:\n  ${u.templates.join("\n  ")}` : "",
        ].filter(Boolean).join("\n\n") || "No references found"
      : "No references found";
    return (
      <div
        className={`group w-full flex items-center gap-2 px-2 py-1.5 rounded text-left text-sm hover:bg-[var(--color-accent)]/60 ${isSel ? "bg-[var(--color-accent)] text-[var(--color-primary)] font-medium" : ""}`}
        style={{ paddingLeft: 8 + depth * 14 }}
      >
        <button onClick={() => onPick({ pack, role: roleName, path })} className="flex items-center gap-2 flex-1 min-w-0">
          <Shield className="h-3.5 w-3.5 shrink-0 text-[var(--color-muted-foreground)]" />
          <span className="truncate">{node.name}</span>
          {node.enabled === false && <Badge variant="default" className="text-[9px] ml-1">off</Badge>}
        </button>
        {(pbCount > 0 || tplCount > 0) && (
          <span
            title={tip}
            className="text-[9px] font-mono px-1.5 py-0.5 rounded border border-[var(--color-border)] text-[var(--color-muted-foreground)]"
          >
            {pbCount}pb{tplCount ? ` · ${tplCount}tpl` : ""}
          </span>
        )}
        <button
          title="Delete role"
          onClick={(e) => { e.stopPropagation(); onDelete(path); }}
          className="opacity-0 group-hover:opacity-100 text-[var(--color-muted-foreground)] hover:text-red-500"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    );
  }

  return (
    <div>
      <button
        className="w-full flex items-center gap-1.5 px-2 py-1.5 rounded text-left text-sm hover:bg-[var(--color-accent)]/40"
        style={{ paddingLeft: 8 + depth * 14 }}
        onClick={() => setExpanded({ ...expanded, [key]: !open })}
      >
        {isFolder ? (open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />) : <span className="w-3" />}
        <Folder className="h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
        <span className="font-medium truncate">{node.name}</span>
        {node.children && <span className="text-[10px] text-[var(--color-muted-foreground)] ml-auto">{countLeaves(node.children)}</span>}
      </button>
      {open && node.children?.map((c) => (
        <TreeNode key={keyOf(c)} node={c} depth={depth + 1} expanded={expanded} setExpanded={setExpanded} parentPath={path} onPick={onPick} selected={selected} onDelete={onDelete} usage={usage} />
      ))}
    </div>
  );
}
