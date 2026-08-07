export type PlanChangeAction =
  | "create"
  | "update"
  | "replace"
  | "destroy"
  | "read"
  | "drift";

export type PlanAttrChange = {
  op: "+" | "-" | "~";
  name: string;
  before?: string;
  after?: string;
};

export type PlanResource = {
  address: string;
  type: string;
  name: string;
  action: PlanChangeAction;
  attrs: PlanAttrChange[];
};

export type PlanSummary = {
  add: number;
  change: number;
  destroy: number;
  /** true when tofu explicitly reported "No changes." */
  noChanges: boolean;
  /** true when the log came from an apply (counts are final, not planned) */
  applied: boolean;
};

export type ParsedPlan = {
  resources: PlanResource[];
  summary: PlanSummary | null;
  outputs: PlanAttrChange[];
  driftDetected: boolean;
  noDrift: boolean;
  /** Whether anything plan-like was found at all. */
  hasPlan: boolean;
};

const RESOURCE_HEADER =
  /^\s*#\s+(.+?)\s+(will be created|will be destroyed|will be updated in-place|must be replaced|will be replaced|will be read during apply|has been deleted|has changed|will no longer be managed|has been removed)/;

function actionFor(phrase: string): PlanChangeAction {
  if (phrase.includes("created")) return "create";
  if (phrase.includes("destroyed") || phrase.includes("no longer be managed") || phrase.includes("removed"))
    return "destroy";
  if (phrase.includes("replaced")) return "replace";
  if (phrase.includes("read during apply")) return "read";
  if (phrase.includes("has been deleted") || phrase.includes("has changed")) return "drift";
  return "update";
}

function splitAddress(address: string): { type: string; name: string } {
  // Strip module prefixes and any resource(...) wrapper quoting.
  const clean = address.replace(/^resource\s+/, "").replace(/"/g, "");
  const parts = clean.split(".");
  if (parts.length >= 2) {
    return { type: parts[parts.length - 2] ?? clean, name: parts[parts.length - 1] ?? clean };
  }
  return { type: clean, name: clean };
}

const ATTR_LINE = /^\s*([+~-])\s+([A-Za-z0-9_."\-\[\]]+)\s*=\s*(.*)$/;

function parseAttr(line: string): PlanAttrChange | null {
  const m = ATTR_LINE.exec(line);
  if (!m) return null;
  const op = (m[1] ?? "+") as "+" | "-" | "~";
  const name = (m[2] ?? "").replace(/"/g, "");
  const rhs = (m[3] ?? "").trim();
  if (op === "~") {
    const arrow = rhs.split("->");
    if (arrow.length >= 2) {
      return {
        op,
        name,
        before: cleanValue(arrow[0] ?? ""),
        after: cleanValue(arrow.slice(1).join("->")),
      };
    }
    return { op, name, after: cleanValue(rhs) };
  }
  if (op === "-") return { op, name, before: cleanValue(rhs.replace(/\s*->\s*null$/, "")) };
  return { op, name, after: cleanValue(rhs) };
}

function cleanValue(v: string): string {
  return v
    .trim()
    .replace(/,$/, "")
    .replace(/\s*#\s*forces replacement$/, "")
    .trim();
}

export function parseTofuPlan(log: string): ParsedPlan {
  const empty: ParsedPlan = {
    resources: [], summary: null, outputs: [], driftDetected: false, noDrift: false, hasPlan: false,
  };
  if (!log) return empty;

  const lines = log.split("\n");
  const resources: PlanResource[] = [];
  const outputs: PlanAttrChange[] = [];
  let current: PlanResource | null = null;
  let inOutputs = false;

  for (const raw of lines) {
    const line = raw.replace(/\r$/, "");
    const header = RESOURCE_HEADER.exec(line);
    if (header) {
      const address = (header[1] ?? "").trim();
      const { type, name } = splitAddress(address);
      current = { address, type, name, action: actionFor(header[2] ?? ""), attrs: [] };
      resources.push(current);
      inOutputs = false;
      continue;
    }

    if (/^Changes to Outputs:/.test(line)) {
      current = null;
      inOutputs = true;
      continue;
    }

    if (current) {
      if (/^\s*}\s*$/.test(line)) {
        current = null;
        continue;
      }
      if (current.attrs.length < 40) {
        const attr = parseAttr(line);
        if (attr) current.attrs.push(attr);
      }
      continue;
    }

    if (inOutputs) {
      if (line.trim() === "") {
        inOutputs = false;
        continue;
      }
      const attr = parseAttr(line);
      if (attr) outputs.push(attr);
    }
  }

  let summary: PlanSummary | null = null;
  const planMatch = /Plan:\s*(\d+)\s+to add,\s*(\d+)\s+to change,\s*(\d+)\s+to destroy/.exec(log);
  const applyMatch = /Resources:\s*(\d+)\s+added,\s*(\d+)\s+changed,\s*(\d+)\s+destroyed/.exec(log);
  if (applyMatch) {
    summary = {
      add: Number(applyMatch[1] ?? 0),
      change: Number(applyMatch[2] ?? 0),
      destroy: Number(applyMatch[3] ?? 0),
      noChanges: false,
      applied: true,
    };
  } else if (planMatch) {
    summary = {
      add: Number(planMatch[1] ?? 0),
      change: Number(planMatch[2] ?? 0),
      destroy: Number(planMatch[3] ?? 0),
      noChanges: false,
      applied: false,
    };
  } else if (/No changes\./.test(log)) {
    summary = { add: 0, change: 0, destroy: 0, noChanges: true, applied: false };
  }

  const driftDetected =
    /\[drift\]\s*DRIFT DETECTED/i.test(log) ||
    /Objects have changed outside of (OpenTofu|Terraform)/i.test(log) ||
    resources.some((r) => r.action === "drift");
  const noDrift = !driftDetected && /\[drift\]\s*no drift/i.test(log);

  return {
    resources,
    summary,
    outputs,
    driftDetected,
    noDrift,
    hasPlan: resources.length > 0 || summary !== null || outputs.length > 0 || driftDetected || noDrift,
  };
}

export const ACTION_META: Record<
  PlanChangeAction,
  { label: string; sign: string; tone: "create" | "update" | "destroy" | "neutral" }
> = {
  create: { label: "create", sign: "+", tone: "create" },
  update: { label: "update", sign: "~", tone: "update" },
  replace: { label: "replace", sign: "±", tone: "destroy" },
  destroy: { label: "destroy", sign: "-", tone: "destroy" },
  read: { label: "read", sign: "<", tone: "neutral" },
  drift: { label: "drifted", sign: "!", tone: "update" },
};
