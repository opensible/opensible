import type { Blueprint, BlueprintGroup, FormSchemaField } from "./types";

/**
 * Cloud hub source for Stack Blueprints (hub.opensible.com).
 */

export const HUB_URL = (
  (import.meta.env.VITE_STACK_HUB_URL as string | undefined) || "https://registry.opensible.com"
).replace(/\/$/, "");

type HubGroup = {
  slug: string;
  name: string;
  description?: string;
  logo?: string;
  sort?: number;
};

type HubBlueprint = {
  id: string;
  slug: string;
  name: string;
  description?: string;
  logo?: string;
  tags?: string[];
  edition?: string;
  available?: boolean;
  source?: string;
  template_id?: string;
  filename_stem?: string;
  stars_count?: number;
  installs_count?: number;
  latest_version_id?: string;
  expand?: {
    group?: HubGroup;
    publisher?: { slug?: string; name?: string };
  };
};

type HubVersion = {
  id: string;
  blueprint: string;
  version: string;
  defaults?: Record<string, unknown>;
  form_schema?: FormSchemaField[];
};

type PbList<T> = { items: T[] };

async function pbList<T>(path: string): Promise<T[]> {
  const res = await fetch(`${HUB_URL}${path}`, { headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`Stack Hub request failed (${res.status})`);
  const json = (await res.json()) as PbList<T>;
  return json.items ?? [];
}

async function pbListAll<T>(path: string): Promise<T[]> {
  const out: T[] = [];
  for (let page = 1; page <= 20; page++) {
    const res = await fetch(`${HUB_URL}${path}&page=${page}`, { headers: { accept: "application/json" } });
    if (!res.ok) throw new Error(`Stack Hub request failed (${res.status})`);
    const json = (await res.json()) as PbList<T> & { totalPages?: number };
    out.push(...(json.items ?? []));
    if (!json.totalPages || page >= json.totalPages) break;
  }
  return out;
}

async function tallyByBlueprint(collection: string): Promise<Record<string, number>> {
  const rows = await pbListAll<{ blueprint?: string }>(
    `/api/collections/${collection}/records?perPage=500&fields=blueprint`,
  );
  const counts: Record<string, number> = {};
  for (const r of rows) {
    if (!r.blueprint) continue;
    counts[r.blueprint] = (counts[r.blueprint] ?? 0) + 1;
  }
  return counts;
}


function toBlueprint(
  bp: HubBlueprint,
  version: HubVersion | undefined,
): Blueprint {
  return {
    id: bp.slug,
    name: bp.name,
    description: bp.description ?? "",
    logo: bp.logo || bp.expand?.group?.logo,
    tags: Array.isArray(bp.tags) ? bp.tags : [],
    author: bp.expand?.publisher?.slug,
    stars: bp.stars_count,
    installs: bp.installs_count,
    source: bp.source || undefined,
    available: bp.available ?? false,
    templateId: bp.template_id || undefined,
    filenameStem: bp.filename_stem || bp.slug,
    defaults: version?.defaults ?? {},
    formSchema: Array.isArray(version?.form_schema) ? version?.form_schema : undefined,
  };
}

/** Fetch the published, public blueprint catalog from the cloud hub. */
export async function fetchHubBlueprintGroups(): Promise<BlueprintGroup[]> {
  const [groups, blueprints] = await Promise.all([
    pbList<HubGroup>("/api/collections/osble_groups/records?perPage=200&sort=sort"),
    pbList<HubBlueprint>(
      "/api/collections/osble_blueprints/records?perPage=500&expand=group,publisher&filter=" +
        encodeURIComponent("(status='published' && visibility='public')"),
    ),
  ]);

  const versionIds = blueprints.map((b) => b.latest_version_id).filter(Boolean) as string[];
  let versions: HubVersion[] = [];
  if (versionIds.length > 0) {
    const filter = versionIds.map((id) => `id='${id}'`).join(" || ");
    try {
      versions = await pbList<HubVersion>(
        `/api/collections/osble_blueprint_versions/records?perPage=500&filter=${encodeURIComponent(filter)}`,
      );
    } catch {
      versions = [];
    }
  }
  const versionById = new Map(versions.map((v) => [v.id, v]));

  const byGroup = new Map<string, Blueprint[]>();
  for (const bp of blueprints) {
    const slug = bp.expand?.group?.slug;
    if (!slug) continue;
    const list = byGroup.get(slug) ?? [];
    list.push(toBlueprint(bp, bp.latest_version_id ? versionById.get(bp.latest_version_id) : undefined));
    byGroup.set(slug, list);
  }

  return groups
    .map((g) => ({
      id: g.slug,
      name: g.name,
      description: g.description ?? "",
      logo: g.logo ?? "",
      blueprints: byGroup.get(g.slug) ?? [],
    }))
    .filter((g) => g.blueprints.length > 0);
}

/* ------------------------------------------------------------------ stats */

export type HubStat = { id: string; slug: string; stars: number; installs: number };

export async function fetchHubStats(): Promise<Record<string, HubStat>> {
  const [rows, starCounts, installCounts] = await Promise.all([
    pbListAll<HubBlueprint & { installs_count?: number }>(
      "/api/collections/osble_blueprints/records?perPage=500&fields=id,slug,stars_count,installs_count&filter=" +
        encodeURIComponent("(status='published' && visibility='public')"),
    ),
    tallyByBlueprint("osble_stars").catch(() => ({}) as Record<string, number>),
    tallyByBlueprint("osble_installs").catch(() => ({}) as Record<string, number>),
  ]);
  const out: Record<string, HubStat> = {};
  for (const r of rows) {
    out[r.slug] = {
      id: r.id,
      slug: r.slug,
      stars: Math.max(starCounts[r.id] ?? 0, r.stars_count ?? 0),
      installs: Math.max(installCounts[r.id] ?? 0, r.installs_count ?? 0),
    };
  }
  return out;
}


const INSTALL_ID_KEY = "opensible.install_id";

function installId(): string {
  try {
    const existing = localStorage.getItem(INSTALL_ID_KEY);
    if (existing) return existing;
    const id = crypto.randomUUID();
    localStorage.setItem(INSTALL_ID_KEY, id);
    return id;
  } catch {
    return "anonymous";
  }
}

/** Resolve a blueprint record id from its slug (installs need the relation id). */
async function blueprintIdBySlug(slug: string): Promise<string | undefined> {
  const rows = await pbList<{ id: string }>(
    `/api/collections/osble_blueprints/records?perPage=1&fields=id&filter=${encodeURIComponent(`slug='${slug.replace(/'/g, "")}'`)}`,
  );
  return rows[0]?.id;
}

export async function recordHubInstall(
  slug: string,
  opts: { result?: "ok" | "failed"; blueprintId?: string; consoleVersion?: string } = {},
): Promise<boolean> {
  try {
    const id = opts.blueprintId ?? (await blueprintIdBySlug(slug));
    if (!id) return false;
    const res = await fetch(`${HUB_URL}/api/collections/osble_installs/records`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        blueprint: id,
        result: opts.result ?? "ok",
        install_id: installId(),
        console_version: opts.consoleVersion ?? "console/web",
      }),
    });
    return res.ok;
  } catch {
    return false;
  }
}
