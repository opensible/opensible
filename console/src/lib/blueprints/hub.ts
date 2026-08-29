import type { Blueprint, BlueprintGroup, FormSchemaField } from "./types";

/**
 * Cloud hub source for Stack Blueprints (hub.opensible.com).
 */

export const HUB_URL = (
  (import.meta.env.VITE_STACK_HUB_URL as string | undefined) || "https://hub.opensible.com"
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
