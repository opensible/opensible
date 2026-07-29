import type { BlueprintGroup } from "./types";
import { genericGroup } from "./generic";
import { kubernetesGroup } from "./kubernetes";
import { dockerGroup } from "./docker";
import { databasesGroup } from "./databases";
import {
  observabilityGroup,
  securityGroup,
  messagingGroup,
} from "./observability";
import { storageGroup } from "./storage";
import { uninstallGroup } from "./uninstall";
import { cicdGroup } from "./cicd";

export type { Blueprint, BlueprintGroup, FormSchemaField, FormFieldType } from "./types";

/**
 * Registry of Stack Blueprint groups. Add a new group by creating a file
 * under `src/lib/blueprints/<group>.ts` and appending it here.
 */
export const BLUEPRINT_GROUPS: BlueprintGroup[] = [
  kubernetesGroup,
  dockerGroup,
  cicdGroup,
  databasesGroup,
  observabilityGroup,
  messagingGroup,
  storageGroup,
  securityGroup,
  uninstallGroup,
];
// `genericGroup` intentionally omitted — Generic / General-purpose is already
// available from the "Create New Build" Resource Catalog drawer.
void genericGroup;
