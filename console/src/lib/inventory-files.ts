import { api } from "@/lib/api";

type InventoryListResp = {
  success?: boolean;
  files?: { name: string; path: string; env?: string | null }[];
};

/**
 * Resolve the inventory file(s) to run a playbook against.
 */
export async function resolveInventoryFiles(): Promise<string[]> {
  try {
    const res = await api<InventoryListResp>("GET", "/api/inventory/list");
    const paths = (res?.files ?? []).map((f) => f.path).filter(Boolean);
    if (paths.length === 0) return ["inventory.yml"];
    const preferred = paths.find((p) => /(^|\/)inventory\.ya?ml$/i.test(p));
    return [preferred || paths[0] || "inventory.yml"];
  } catch {
    return ["inventory.yml"];
  }
}
