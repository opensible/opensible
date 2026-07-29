import { useMemo, useState } from "react";
import { Plus, Trash2, ArrowUp, ArrowDown, Save, Download, Play, FileCode } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { YamlEditor } from "@/components/ui/yaml-editor";
import { api } from "@/lib/api";

type BlockKind = "role" | "shell" | "command" | "copy" | "template" | "service" | "package" | "docker_compose";

type Block = {
  id: string;
  kind: BlockKind;
  name: string;
  fields: Record<string, string>;
};

const PALETTE: { kind: BlockKind; label: string; fields: { key: string; placeholder: string }[] }[] = [
  { kind: "role", label: "Role", fields: [{ key: "role", placeholder: "role name (e.g. common)" }, { key: "tags", placeholder: "tags (comma separated, optional)" }] },
  { kind: "shell", label: "Shell", fields: [{ key: "cmd", placeholder: "echo hello" }] },
  { kind: "command", label: "Command", fields: [{ key: "cmd", placeholder: "/usr/bin/uptime" }] },
  { kind: "copy", label: "Copy", fields: [{ key: "src", placeholder: "files/app.conf" }, { key: "dest", placeholder: "/etc/app.conf" }, { key: "mode", placeholder: "0644 (optional)" }] },
  { kind: "template", label: "Template", fields: [{ key: "src", placeholder: "templates/app.j2" }, { key: "dest", placeholder: "/etc/app.conf" }] },
  { kind: "service", label: "Service", fields: [{ key: "name", placeholder: "nginx" }, { key: "state", placeholder: "started|stopped|restarted" }, { key: "enabled", placeholder: "yes|no (optional)" }] },
  { kind: "package", label: "Package", fields: [{ key: "name", placeholder: "nginx" }, { key: "state", placeholder: "present|latest|absent" }] },
  { kind: "docker_compose", label: "Docker Compose", fields: [{ key: "project_src", placeholder: "/opt/myapp" }, { key: "state", placeholder: "present|absent (default present)" }] },
];

function uid() { return Math.random().toString(36).slice(2, 10); }

function blockToTask(b: Block): Record<string, unknown> | null {
  const f = b.fields;
  switch (b.kind) {
    case "role": return null;
    case "shell": return f.cmd ? { name: b.name || "Run shell", shell: f.cmd } : null;
    case "command": return f.cmd ? { name: b.name || "Run command", command: f.cmd } : null;
    case "copy": {
      const args: Record<string, string> = {};
      if (f.src) args.src = f.src;
      if (f.dest) args.dest = f.dest;
      if (f.mode) args.mode = f.mode;
      return Object.keys(args).length ? { name: b.name || "Copy file", "ansible.builtin.copy": args } : null;
    }
    case "template": {
      const args: Record<string, string> = {};
      if (f.src) args.src = f.src;
      if (f.dest) args.dest = f.dest;
      return Object.keys(args).length ? { name: b.name || "Render template", "ansible.builtin.template": args } : null;
    }
    case "service": {
      const args: Record<string, string> = {};
      if (f.name) args.name = f.name;
      if (f.state) args.state = f.state;
      if (f.enabled) args.enabled = f.enabled;
      return Object.keys(args).length ? { name: b.name || `Service ${f.name}`, "ansible.builtin.service": args } : null;
    }
    case "package": {
      const args: Record<string, string> = {};
      if (f.name) args.name = f.name;
      if (f.state) args.state = f.state;
      return Object.keys(args).length ? { name: b.name || `Package ${f.name}`, "ansible.builtin.package": args } : null;
    }
    case "docker_compose": {
      const args: Record<string, string> = {};
      if (f.project_src) args.project_src = f.project_src;
      if (f.state) args.state = f.state;
      return Object.keys(args).length ? { name: b.name || "Docker Compose", "community.docker.docker_compose_v2": args } : null;
    }
  }
}

function generateYaml(playName: string, hosts: string, become: boolean, blocks: Block[]): string {
  const roles = blocks.filter(b => b.kind === "role" && b.fields.role).map(b => {
    const r: Record<string, unknown> = { role: b.fields.role };
    if (b.fields.tags) r.tags = b.fields.tags.split(",").map(s => s.trim()).filter(Boolean);
    return r;
  });
  const tasks = blocks.map(blockToTask).filter(Boolean) as Record<string, unknown>[];
  const play: Record<string, unknown> = { name: playName || "Generated play", hosts: hosts || "all" };
  if (become) play.become = true;
  if (roles.length) play.roles = roles;
  if (tasks.length) play.tasks = tasks;
  // Minimal hand-rolled YAML so we don't pull js-yaml just for this
  const lines: string[] = ["---"];
  lines.push(`- name: ${JSON.stringify(play.name)}`);
  lines.push(`  hosts: ${hosts || "all"}`);
  if (become) lines.push(`  become: true`);
  if (roles.length) {
    lines.push("  roles:");
    for (const r of roles) {
      lines.push(`    - role: ${r.role}`);
      if (r.tags) lines.push(`      tags: [${(r.tags as string[]).join(", ")}]`);
    }
  }
  if (tasks.length) {
    lines.push("  tasks:");
    for (const t of tasks) {
      const name = t.name as string;
      lines.push(`    - name: ${JSON.stringify(name)}`);
      for (const [k, v] of Object.entries(t)) {
        if (k === "name") continue;
        if (typeof v === "string") {
          lines.push(`      ${k}: ${JSON.stringify(v)}`);
        } else if (v && typeof v === "object") {
          lines.push(`      ${k}:`);
          for (const [ak, av] of Object.entries(v as Record<string, string>)) {
            lines.push(`        ${ak}: ${JSON.stringify(av)}`);
          }
        }
      }
    }
  }
  return lines.join("\n") + "\n";
}

export function PlaybookBuilder({ onSaved }: { onSaved?: () => void }) {
  const [playName, setPlayName] = useState("My playbook");
  const [hosts, setHosts] = useState("all");
  const [become, setBecome] = useState(true);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const yaml = useMemo(() => generateYaml(playName, hosts, become, blocks), [playName, hosts, become, blocks]);

  const add = (kind: BlockKind) => {
    const def = PALETTE.find(p => p.kind === kind)!;
    const fields: Record<string, string> = {};
    def.fields.forEach(f => { fields[f.key] = ""; });
    setBlocks(b => [...b, { id: uid(), kind, name: "", fields }]);
  };
  const update = (id: string, patch: Partial<Block> | { fields: Record<string, string> }) =>
    setBlocks(bs => bs.map(b => b.id === id ? { ...b, ...patch, fields: { ...b.fields, ...(patch as any).fields } } : b));
  const remove = (id: string) => setBlocks(bs => bs.filter(b => b.id !== id));
  const move = (id: string, dir: -1 | 1) => setBlocks(bs => {
    const i = bs.findIndex(b => b.id === id);
    if (i < 0) return bs;
    const j = i + dir;
    if (j < 0 || j >= bs.length) return bs;
    const copy = [...bs];
    const tmp = copy[i]!;
    copy[i] = copy[j]!;
    copy[j] = tmp;
    return copy;
  });

  const download = () => {
    const blob = new Blob([yaml], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${playName.replace(/\s+/g, "-").toLowerCase() || "playbook"}.yml`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const saveToLibrary = async () => {
    setSaving(true); setMsg(null);
    try {
      const projectId = window.localStorage.getItem("current_project_id");
      const token = window.localStorage.getItem("auth_token");
      const fd = new FormData();
      const fileName = `${playName.replace(/\s+/g, "-").toLowerCase() || "playbook"}.yml`;
      fd.append("file", new File([yaml], fileName, { type: "text/yaml" }));
      const res = await fetch(`/api/projects/${projectId}/playbooks/upload`, {
        method: "POST",
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(projectId ? { "X-Project-Id": projectId } : {}),
        },
        body: fd,
      });
      if (!res.ok) throw new Error(await res.text());
      setMsg("Saved to Playbook Library.");
      onSaved?.();
    } catch (e) {
      setMsg(`Save failed: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const runInline = async () => {
    setSaving(true); setMsg(null);
    try {
      const r = await api("POST", "/api/run_inline_playbook", { yaml, ansible_config: "ansible.cfg" });
      setMsg(`Queued: ${JSON.stringify(r)}`);
    } catch (e) {
      setMsg(`Run failed: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_1fr] gap-4">
      <div className="space-y-3">
        <Card>
          <CardHeader className="py-3"><CardTitle className="text-sm">Play Settings</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block text-xs text-[var(--color-muted-foreground)] mb-1">Name</label>
                <Input value={playName} onChange={e => setPlayName(e.target.value)} />
              </div>
              <div>
                <label className="block text-xs text-[var(--color-muted-foreground)] mb-1">Hosts</label>
                <Input value={hosts} onChange={e => setHosts(e.target.value)} placeholder="all" />
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={become} onChange={e => setBecome(e.target.checked)} /> become (sudo)
            </label>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="py-3 flex flex-row items-center justify-between">
            <CardTitle className="text-sm">Add Block</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {PALETTE.map(p => (
              <Button key={p.kind} variant="outline" size="sm" onClick={() => add(p.kind)}>
                <Plus className="h-3.5 w-3.5 mr-1" /> {p.label}
              </Button>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="py-3"><CardTitle className="text-sm">Blocks ({blocks.length})</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {blocks.length === 0 && (
              <div className="text-xs text-[var(--color-muted-foreground)] text-center py-6">
                <FileCode className="h-8 w-8 mx-auto mb-2 opacity-40" />
                No blocks yet. Pick one above to start composing your playbook.
              </div>
            )}
            {blocks.map((b, i) => {
              const def = PALETTE.find(p => p.kind === b.kind)!;
              return (
                <div key={b.id} className="border border-[var(--color-border)] rounded-md p-2 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Badge variant="default" className="text-[10px]">{def.label}</Badge>
                      <Input
                        value={b.name} onChange={e => update(b.id, { name: e.target.value })}
                        placeholder="Optional name" className="h-7 text-xs w-56"
                      />
                    </div>
                    <div className="flex">
                      <Button size="sm" variant="ghost" onClick={() => move(b.id, -1)} disabled={i === 0}><ArrowUp className="h-3.5 w-3.5" /></Button>
                      <Button size="sm" variant="ghost" onClick={() => move(b.id, 1)} disabled={i === blocks.length - 1}><ArrowDown className="h-3.5 w-3.5" /></Button>
                      <Button size="sm" variant="ghost" onClick={() => remove(b.id)}><Trash2 className="h-3.5 w-3.5 text-[var(--color-destructive)]" /></Button>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {def.fields.map(f => (
                      <Input
                        key={f.key} placeholder={f.placeholder}
                        value={b.fields[f.key] ?? ""}
                        onChange={e => update(b.id, { fields: { [f.key]: e.target.value } } as any)}
                        className="h-7 text-xs"
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </CardContent>
        </Card>
      </div>

      <div className="space-y-3">
        <Card>
          <CardHeader className="py-3 flex flex-row items-center justify-between">
            <CardTitle className="text-sm">Generated YAML</CardTitle>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={download}><Download className="h-3.5 w-3.5 mr-1" /> Download</Button>
              <Button size="sm" variant="outline" onClick={runInline} disabled={saving}><Play className="h-3.5 w-3.5 mr-1" /> Run</Button>
              <Button size="sm" onClick={saveToLibrary} disabled={saving}><Save className="h-3.5 w-3.5 mr-1" /> Save to Library</Button>
            </div>
          </CardHeader>
          <CardContent>
            <YamlEditor value={yaml} readOnly height={520} />
            {msg && <div className="mt-2 text-xs text-[var(--color-muted-foreground)]">{msg}</div>}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
