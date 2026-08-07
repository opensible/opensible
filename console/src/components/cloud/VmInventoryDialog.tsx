import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Boxes, X, Network, Download, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

type VM = {
  hostname?: string; instance_id?: string; status?: string; az?: string;
  flavor_id?: string; image_id?: string;
  private_ip?: string; public_ip?: string | null; mac?: string;
  subnet_name?: string; subnet_cidr?: string; subnet_gateway?: string;
  vpc_name?: string; vpc_cidr?: string;
  security_groups?: string[];
  system_disk_type?: string; system_disk_size?: number;
};

type Inventory = {
  vms: VM[]; count: number; generated_at?: number; state_present?: boolean;
  message?: string;
};

export function VmInventoryDialog({ stackId, onClose }: { stackId: string; onClose: () => void }) {
  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: ["cloud", "stack", stackId, "inventory"],
    queryFn: () => api<Inventory>("GET", `/api/cloud/stacks/${encodeURIComponent(stackId)}/inventory`),
  });

  function downloadJson() {
    if (!data) return;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${stackId}-inventory.json`; a.click();
    URL.revokeObjectURL(url);
  }

  const vms = data?.vms || [];

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-start justify-center overflow-y-auto p-4 sm:p-8">
      <div className="bg-[var(--color-card)] text-[var(--color-card-foreground)] rounded-lg shadow-xl w-full max-w-6xl border border-[var(--color-border)]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)]">
          <div className="flex items-center gap-3">
            <Boxes className="h-5 w-5" />
            <div>
              <h2 className="text-lg font-semibold">Inventory Resources · {stackId}</h2>
              <p className="text-xs text-[var(--color-muted-foreground)]">
                {data?.generated_at
                  ? `Last refreshed: ${new Date(data.generated_at * 1000).toLocaleString()}`
                  : "Parsed from terraform.tfstate · persisted snapshot"}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
              <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} /> Refresh
            </Button>
            <Button variant="outline" size="sm" onClick={downloadJson} disabled={!data}>
              <Download className="h-4 w-4" /> JSON
            </Button>
            <Button variant="ghost" size="icon" onClick={onClose}><X className="h-4 w-4" /></Button>
          </div>
        </div>

        <div className="px-6 py-5 space-y-4 max-h-[75vh] overflow-y-auto">
          {error && (() => {
            const msg = (error as any)?.message || "Failed to load inventory.";
            const isMissing = /not found|404/i.test(msg);
            return (
              <div className="rounded border border-[var(--color-warning)]/40 bg-[var(--color-warning)]/10 p-4 text-sm">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="h-4 w-4 mt-0.5 text-[var(--color-warning)]" />
                  <div className="space-y-1">
                    <div className="font-medium text-[var(--color-foreground)]">
                      {isMissing ? "Inventory endpoint not available on backend" : "Failed to load inventory"}
                    </div>
                    <div className="text-xs text-[var(--color-muted-foreground)]">
                      {isMissing ? (
                        <>The backend container at this server doesn't expose <code className="font-mono">/api/cloud/stacks/&lt;name&gt;/inventory</code> yet. Rebuild &amp; redeploy the backend image (it includes the new VM Inventory route) and try again.</>
                      ) : msg}
                    </div>
                  </div>
                </div>
              </div>
            );
          })()}

          {isLoading && <div className="text-sm text-[var(--color-muted-foreground)]">Loading inventory…</div>}

          {!isLoading && !error && vms.length === 0 && (
            <div className="rounded border border-dashed border-[var(--color-border)] p-8 text-center">
              <Network className="h-8 w-8 mx-auto mb-2 text-[var(--color-muted-foreground)]" />
              <p className="text-sm font-medium">No VMs provisioned yet</p>
              <p className="text-xs text-[var(--color-muted-foreground)] mt-1">
                {data?.message || "Run Apply on this stack to provision infrastructure, then return here to see the VM inventory."}
              </p>
              {data && data.state_present === false && (
                <p className="text-xs text-[var(--color-warning)] mt-2">No terraform.tfstate found.</p>
              )}
            </div>
          )}

          {vms.length > 0 && (
            <>
              <div className="text-xs text-[var(--color-muted-foreground)]">
                Showing <span className="font-semibold text-[var(--color-foreground)]">{vms.length}</span> virtual machine{vms.length === 1 ? "" : "s"}.
              </div>
              <div className="overflow-x-auto rounded border border-[var(--color-border)]">
                <table className="w-full text-xs">
                  <thead className="bg-[var(--color-muted)] text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    <tr>
                      <th className="text-left px-3 py-2 font-medium">Hostname</th>
                      <th className="text-left px-3 py-2 font-medium">Status</th>
                      <th className="text-left px-3 py-2 font-medium">Private IP</th>
                      <th className="text-left px-3 py-2 font-medium">Public IP</th>
                      <th className="text-left px-3 py-2 font-medium">Subnet</th>
                      <th className="text-left px-3 py-2 font-medium">VPC</th>
                      <th className="text-left px-3 py-2 font-medium">AZ</th>
                      <th className="text-left px-3 py-2 font-medium">Flavor</th>
                      <th className="text-left px-3 py-2 font-medium">Disk</th>
                    </tr>
                  </thead>
                  <tbody>
                    {vms.map((vm) => (
                      <tr key={vm.instance_id || vm.hostname} className="border-t border-[var(--color-border)] hover:bg-[var(--color-muted)]/40">
                        <td className="px-3 py-2 font-mono">
                          <div className="font-semibold text-[var(--color-foreground)]">{vm.hostname || "—"}</div>
                          {vm.instance_id && <div className="text-[10px] text-[var(--color-muted-foreground)]">{vm.instance_id}</div>}
                        </td>
                        <td className="px-3 py-2"><Badge variant={vm.status === "ACTIVE" ? "success" : "default"}>{vm.status || "—"}</Badge></td>
                        <td className="px-3 py-2 font-mono">{vm.private_ip || "—"}</td>
                        <td className="px-3 py-2 font-mono">{vm.public_ip || <span className="text-[var(--color-muted-foreground)]">—</span>}</td>
                        <td className="px-3 py-2">
                          <div>{vm.subnet_name || "—"}</div>
                          <div className="text-[10px] text-[var(--color-muted-foreground)] font-mono">{vm.subnet_cidr}</div>
                        </td>
                        <td className="px-3 py-2">
                          <div>{vm.vpc_name || "—"}</div>
                          <div className="text-[10px] text-[var(--color-muted-foreground)] font-mono">{vm.vpc_cidr}</div>
                        </td>
                        <td className="px-3 py-2 font-mono">{vm.az || "—"}</td>
                        <td className="px-3 py-2 font-mono">{vm.flavor_id || "—"}</td>
                        <td className="px-3 py-2 font-mono">
                          {vm.system_disk_size ? `${vm.system_disk_size} GB` : "—"}
                          {vm.system_disk_type && <span className="text-[var(--color-muted-foreground)]"> · {vm.system_disk_type}</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
