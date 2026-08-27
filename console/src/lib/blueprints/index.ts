import * as yaml from "js-yaml";
import type { Blueprint, BlueprintGroup } from "./types";

export type { Blueprint, BlueprintGroup, FormSchemaField, FormFieldType } from "./types";


type RawGroup = {
  id: string;
  name: string;
  description: string;
  logo: string;
};

type RawBlueprint = {
  id: string;
  name: string;
  group: string;
  description: string;
  logo?: string;
  tags?: string[];
  author?: string;
  stars?: number;
  source?: string;
  available?: boolean;
  templateId?: string;
  filenameStem?: string;
  defaults?: Record<string, unknown>;
  variables?: Record<string, unknown>;
  formSchema?: unknown[];
};

const groupsRaw = import.meta.glob("../../../IaC/blueprints/groups.yaml", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

const blueprintsRaw = import.meta.glob("../../../IaC/blueprints/*/*/blueprint.yaml", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

function normalize(raw: RawBlueprint): Blueprint {
  return {
    id: raw.id,
    name: raw.name,
    description: raw.description,
    logo: raw.logo,
    tags: raw.tags,
    author: raw.author,
    stars: raw.stars,
    source: raw.source,
    available: raw.available ?? false,
    templateId: raw.templateId,
    filenameStem: raw.filenameStem,
    defaults: raw.defaults ?? raw.variables ?? {},
    formSchema: raw.formSchema as Blueprint["formSchema"],
  };
}

function buildGroups(): BlueprintGroup[] {
  const groupDefs = (Object.values(groupsRaw)
    .map((text) => yaml.load(text) as RawGroup[])
    .flat()
    .filter(Boolean)) as RawGroup[];

  const byGroup = new Map<string, Blueprint[]>();
  for (const path of Object.keys(blueprintsRaw).sort()) {
    const raw = yaml.load(blueprintsRaw[path] ?? "") as RawBlueprint | undefined;
    if (!raw || !raw.id || !raw.group) {
      console.warn(`[blueprints] skipping invalid blueprint file: ${path}`);
      continue;
    }
    const list = byGroup.get(raw.group) ?? [];
    list.push(normalize(raw));
    byGroup.set(raw.group, list);
  }

  for (const groupId of byGroup.keys()) {
    if (!groupDefs.some((g) => g.id === groupId)) {
      console.warn(`[blueprints] group "${groupId}" is not defined in groups.yaml`);
    }
  }

  return groupDefs.map((g) => ({
    id: g.id,
    name: g.name,
    description: g.description,
    logo: g.logo,
    blueprints: byGroup.get(g.id) ?? [],
  }));
}

export const BLUEPRINT_GROUPS: BlueprintGroup[] = buildGroups();
