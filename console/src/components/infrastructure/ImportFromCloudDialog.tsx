import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Boxes, Download, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { toast } from "sonner";

type Stack = { id?: string; name?: string; stack_id?: string };
type VM = { hostname?: string; private_ip?: string; public_ip?: string | null; status?: string; vpc_name?: string; subnet_name?: string };
type Inventory = { vms: VM[]; count: number };

export function ImportFromCloudDialog({
  open, onClose, onImported, inventoryFiles, defaultInventoryFile,
}: {
  open: boolean;
  onClose: () => void;
  onImported: () => void;
  inventoryFiles: { name: string; path: string; relative_path?: string }[];
  defaultInventoryFile?: string;
}) {
  const [stackId, setStackId] = useState<string>("");
  const [groupName, setGroupName] = useState<string>("");
  const [invFile, setInvFile] = useState<string>(defaultInventoryFile || "inventories/inventory.yml");
  const [ipType, setIpType] = useState<"private" | "public" | "hostname">("private");
  const [namePrefix, setNamePrefix] = useState<string>("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);

  const stacksQ = useQuery({
    queryKey: ["cloud", "stacks-for-import"],
    queryFn: () => api<{ stacks: Stack[] } | Stack[]>("GET", "/api/cloud/stacks"),
    enabled: open,
  });
  const stacks: Stack[] = useMemo(() => {
    const d = stacksQ.data as any;
    return Array.isArray(d?.stacks) ? d.stacks : Array.isArray(d) ? d : [];
  }, [stacksQ.data]);

  const invQ = useQuery({
    queryKey: ["cloud", "stack", stackId, "inventory-import"],
    queryFn: () => api<Inventory>("GET", `/api/cloud/stacks/${encodeURIComponent(stackId)}/inventory`),
    enabled: open && !!stackId,
  });
  const vms: VM[] = invQ.data?.vms ?? [];

  const resolveName = (vm: VM): string => {
    const base = ipType === "hostname"
      ? (vm.hostname || vm.private_ip || vm.public_ip || "")
      : ipType === "public"
        ? (vm.public_ip || vm.private_ip || vm.hostname || "")
        : (vm.private_ip || vm.public_ip || vm.hostname || "");
    return (namePrefix || "") + String(base || "");
  };

  const toggle = (key: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };
  const toggleAll = () => {
    if (selected.size === vms.length) setSelected(new Set());
    else setSelected(new Set(vms.map((_, i) => String(i))));
  };

  const [errors, setErrors] = useState<{ name: string; message: string }[]>([]);

  const doImport = async () => {
    if (selected.size === 0) { toast.error("Select at least one VM"); return; }
    if (!invFile) { toast.error("Pick an inventory file"); return; }
    setImporting(true);
    setErrors([]);
    let ok = 0;
    const fails: { name: string; message: string }[] = [];
    for (const idx of selected) {
      const vm = vms[Number(idx)];
      if (!vm) continue;
      const name = resolveName(vm);
      if (!name) { fails.push({ name: "(unnamed)", message: "Could not resolve host name (no IP/hostname)" }); continue; }
      // Normalize inventory_file: strip layout prefix like "IaC/ansible/" so the backend
      // resolves it against the project's inventories_dir consistently.
      const m = invFile.match(/(?:^|\/)inventories\/(.+)$/);
      const normalizedInv = m ? `inventories/${m[1]}` : invFile;
      try {
        const r = await api<{ success?: boolean; error?: string }>("POST", "/api/inventory/add_host", {
          host_name: name,
          host_ip: vm.private_ip || vm.public_ip || undefined,
          group_name: groupName || undefined,
          inventory_file: normalizedInv,
        });
        if (r && r.success === false) {
          fails.push({ name, message: r.error || "Unknown error" });
        } else {
          ok++;
        }
      } catch (e: any) {
        const msg = e?.message || String(e);
        console.error("import host failed", name, e);
        fails.push({ name, message: msg });
      }
    }
    setImporting(false);
    setErrors(fails);
    if (ok > 0) toast.success(`Imported ${ok} host${ok === 1 ? "" : "s"} into ${invFile}${fails.length ? ` · ${fails.length} failed` : ""}`);
    if (ok === 0 && fails.length > 0) {
      const first = fails[0]!;
      toast.error(`Import failed: ${first.message}${fails.length > 1 ? ` (+${fails.length - 1} more)` : ""}`);
    }
    if (ok === 0 && fails.length === 0) toast.error("Nothing was imported");
    onImported();
    if (fails.length === 0) onClose();
  };

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-start justify-center overflow-y-auto p-4">
      <div className="bg-[var(--color-card)] text-[var(--color-card-foreground)] rounded-lg shadow-xl w-full max-w-5xl border border-[var(--color-border)] mt-12">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)]">
          <div className="flex items-center gap-2">
            <Boxes className="h-5 w-5" />
            <h2 className="text-lg font-semibold">Import hosts from Cloud VM Inventory</h2>
          </div>
          <button onClick={onClose} className="text-xl leading-none text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]">×</button>
        </div>

        <div className="p-6 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-[var(--color-muted-foreground)] mb-1">Cloud stack</label>
              <select
                className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-2 text-sm"
                value={stackId} onChange={(e) => { setStackId(e.target.value); setSelected(new Set()); }}
              >
                <option value="">{stacksQ.isLoading ? "Loading…" : "Pick a stack…"}</option>
                {stacks.map((s, i) => {
                  const v = s.id || s.name || s.stack_id || "";
                  return <option key={i} value={String(v)}>{String(v)}</option>;
                })}
              </select>
            </div>
            <div>
              <label className="block text-xs text-[var(--color-muted-foreground)] mb-1">Inventory file</label>
              <select
                className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-2 text-sm"
                value={invFile} onChange={(e) => setInvFile(e.target.value)}
              >
                {inventoryFiles.length === 0 && <option value="inventories/inventory.yml">inventories/inventory.yml</option>}
                {inventoryFiles.map(f => (
                  <option key={f.path} value={f.relative_path || f.path}>{f.relative_path || f.path}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-[var(--color-muted-foreground)] mb-1">Target group (optional)</label>
              <Input value={groupName} onChange={(e) => setGroupName(e.target.value)} placeholder="e.g. app_vms" className="h-9" />
            </div>
            <div>
              <label className="block text-xs text-[var(--color-muted-foreground)] mb-1">Use as host name</label>
              <select
                className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-background)] px-2 text-sm"
                value={ipType} onChange={(e) => setIpType(e.target.value as any)}
              >
                <option value="private">Private IP</option>
                <option value="public">Public IP</option>
                <option value="hostname">Hostname</option>
              </select>
            </div>
            <div className="md:col-span-4">
              <label className="block text-xs text-[var(--color-muted-foreground)] mb-1">Name prefix (optional)</label>
              <Input value={namePrefix} onChange={(e) => setNamePrefix(e.target.value)} placeholder="e.g. prod-" className="h-9" />
            </div>
          </div>

          <div className="border border-[var(--color-border)] rounded-md overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2 bg-[var(--color-accent)]/30 border-b border-[var(--color-border)]">
              <div className="text-sm font-medium">
                VMs {stackId ? `· ${vms.length}` : ""}
                {selected.size > 0 && <Badge className="ml-2 text-[11px]">{selected.size} selected</Badge>}
              </div>
              {vms.length > 0 && (
                <button onClick={toggleAll} className="text-xs text-[var(--color-primary)] hover:underline">
                  {selected.size === vms.length ? "Clear all" : "Select all"}
                </button>
              )}
            </div>
            <div className="max-h-80 overflow-y-auto">
              {!stackId && (
                <div className="px-4 py-10 text-center text-sm text-[var(--color-muted-foreground)]">Pick a stack to load VMs.</div>
              )}
              {stackId && invQ.isLoading && (
                <div className="px-4 py-10 text-center text-sm text-[var(--color-muted-foreground)] flex items-center justify-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" /> Loading inventory…
                </div>
              )}
              {stackId && !invQ.isLoading && vms.length === 0 && (
                <div className="px-4 py-10 text-center text-sm text-[var(--color-muted-foreground)]">No VMs in this stack inventory.</div>
              )}
              {vms.length > 0 && (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left border-b border-[var(--color-border)] text-xs uppercase tracking-wider text-[var(--color-muted-foreground)]">
                      <th className="px-3 py-2 w-8"></th>
                      <th className="px-3 py-2">Hostname</th>
                      <th className="px-3 py-2">Private IP</th>
                      <th className="px-3 py-2">Public IP</th>
                      <th className="px-3 py-2">VPC / Subnet</th>
                      <th className="px-3 py-2">Will import as</th>
                    </tr>
                  </thead>
                  <tbody>
                    {vms.map((vm, i) => {
                      const key = String(i);
                      const checked = selected.has(key);
                      return (
                        <tr key={key} className="border-b border-[var(--color-border)] hover:bg-[var(--color-accent)]/30">
                          <td className="px-3 py-2"><input type="checkbox" checked={checked} onChange={() => toggle(key)} /></td>
                          <td className="px-3 py-2 font-mono text-xs">{vm.hostname || "—"}</td>
                          <td className="px-3 py-2 font-mono text-xs">{vm.private_ip || "—"}</td>
                          <td className="px-3 py-2 font-mono text-xs">{vm.public_ip || "—"}</td>
                          <td className="px-3 py-2 text-xs text-[var(--color-muted-foreground)]">{[vm.vpc_name, vm.subnet_name].filter(Boolean).join(" / ") || "—"}</td>
                          <td className="px-3 py-2 font-mono text-xs">{resolveName(vm) || <span className="text-[var(--color-destructive)]">—</span>}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {errors.length > 0 && (
            <div className="rounded-md border border-[var(--color-destructive)]/40 bg-[var(--color-destructive)]/5 p-3">
              <div className="text-xs font-semibold text-[var(--color-destructive)] mb-1">{errors.length} host{errors.length === 1 ? "" : "s"} failed to import</div>
              <ul className="text-xs space-y-0.5 max-h-32 overflow-y-auto">
                {errors.map((e, i) => (
                  <li key={i}><span className="font-mono">{e.name}</span>: <span className="text-[var(--color-muted-foreground)]">{e.message}</span></li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 px-6 py-4 border-t border-[var(--color-border)]">
          <Button variant="outline" onClick={onClose} disabled={importing}>Cancel</Button>
          <Button onClick={doImport} disabled={importing || selected.size === 0}>
            <Download className="h-4 w-4 mr-1.5" /> {importing ? "Importing…" : `Import ${selected.size || ""}`}
          </Button>
        </div>
      </div>
    </div>
  );
}
