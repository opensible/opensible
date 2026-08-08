/**
 * Custom (user-defined) policy rules editor.
 */
import { useMemo, useState } from "react";
import * as yaml from "js-yaml";
import { Plus, Trash2, Code2, ListChecks, FileWarning } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { YamlEditor } from "@/components/ui/yaml-editor";

export type CustomSeverity = "info" | "warn" | "deny";
export type Enforcement = "inherit" | "block" | "report";

export type CustomRule = {
  id: string;
  name: string;
  description?: string;
  enabled: boolean;
  severity: CustomSeverity;
  enforcement?: Enforcement;
  resource_types?: string[];
  addresses?: string[];
  actions?: string[];
  attribute?: string;
  operator?: string;
  value?: string;
  message?: string;
};

export const CUSTOM_OPERATORS: Array<{ value: string; label: string; description: string }> = [
  { value: "matches", label: "matches regex", description: "Violation when the attribute matches the pattern" },
  { value: "not_matches", label: "does not match regex", description: "Violation when the attribute fails the pattern" },
  { value: "equals", label: "equals", description: "Violation when the attribute equals the value" },
  { value: "not_equals", label: "does not equal", description: "Violation when the attribute differs from the value" },
  { value: "exists", label: "must be set", description: "Violation when the attribute is missing" },
  { value: "not_exists", label: "must not be set", description: "Violation when the attribute is present" },
  { value: "gt", label: "greater than", description: "Numeric comparison" },
  { value: "lt", label: "less than", description: "Numeric comparison" },
];

const ACTION_OPTIONS = ["create", "update", "delete", "no-op"];

export const EXAMPLE_RULES_YAML = `# One entry per rule. Regex is Go RE2 syntax.
- id: no-public-buckets
  name: No public object storage
  description: Object storage must not be world readable.
  enabled: true
  severity: deny          # info | warn | deny
  enforcement: inherit    # inherit | block | report
  resource_types: ["^aws_s3_bucket.*", "^google_storage_bucket$"]
  actions: [create, update]
  attribute: acl
  operator: matches
  value: "public"
  message: Bucket ACL must not be public.

- id: instance-naming
  name: Instance names must follow the naming standard
  enabled: true
  severity: warn
  resource_types: ["^hcloud_server$", "^aws_instance$"]
  attribute: name
  operator: not_matches
  value: "^(dev|stg|prd)-[a-z0-9-]+$"
  message: Name must look like prd-web-01.

- id: ingress-cidr-audit
  name: Audit wide-open ingress CIDRs
  enabled: false
  severity: info
  attribute: "ingress.*.cidr_blocks"
  operator: matches
  value: "^0\\\\.0\\\\.0\\\\.0/0$"
`;

function slugify(v: string, fallback: string) {
  const s = v
    .toLowerCase()
    .replace(/[^a-z0-9-_]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
  return s || fallback;
}

function listToText(v?: string[]) {
  return (v || []).join(", ");
}

function textToList(s: string) {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

export function normalizeCustomRules(input: unknown): CustomRule[] {
  if (!Array.isArray(input)) return [];
  const seen = new Set<string>();
  return input.slice(0, 100).flatMap((raw: any, i): CustomRule[] => {
    if (!raw || typeof raw !== "object") return [];
    const name = String(raw.name ?? raw.id ?? "").trim();
    let id = slugify(String(raw.id ?? name), `rule-${i + 1}`);
    let n = 2;
    while (seen.has(id)) id = `${slugify(String(raw.id ?? name), `rule-${i + 1}`)}-${n++}`;
    seen.add(id);
    const sev = ["info", "warn", "deny"].includes(raw.severity) ? raw.severity : "warn";
    const enf = ["inherit", "block", "report"].includes(raw.enforcement) ? raw.enforcement : "inherit";
    const asList = (v: any) => (typeof v === "string" ? [v] : Array.isArray(v) ? v.map(String) : []);
    return [
      {
        id,
        name: name || id,
        description: raw.description ? String(raw.description) : "",
        enabled: raw.enabled !== false,
        severity: sev,
        enforcement: enf,
        resource_types: asList(raw.resource_types),
        addresses: asList(raw.addresses),
        actions: asList(raw.actions).filter((a) => ACTION_OPTIONS.includes(a)),
        attribute: raw.attribute ? String(raw.attribute) : "",
        operator: CUSTOM_OPERATORS.some((o) => o.value === raw.operator) ? String(raw.operator) : "matches",
        value: raw.value !== undefined && raw.value !== null ? String(raw.value) : String(raw.pattern ?? ""),
        message: raw.message ? String(raw.message) : "",
      },
    ];
  });
}

/** Compile-check every regex field so users see mistakes before saving. */
export function ruleRegexErrors(rule: CustomRule): string[] {
  const errs: string[] = [];
  const check = (pattern: string, where: string) => {
    try {
      new RegExp(pattern);
    } catch (e: any) {
      errs.push(`${where}: ${e?.message || "invalid regex"}`);
    }
  };
  (rule.resource_types || []).forEach((p) => check(p, `resource type /${p}/`));
  (rule.addresses || []).forEach((p) => check(p, `address /${p}/`));
  if ((rule.operator === "matches" || rule.operator === "not_matches") && rule.value) {
    check(rule.value, `pattern /${rule.value}/`);
  }
  return errs;
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors"
      style={{ background: checked ? "var(--color-primary)" : "var(--color-muted)" }}
    >
      <span
        className="inline-block h-5 w-5 rounded-full bg-[var(--color-background)] shadow transition-transform"
        style={{ transform: checked ? "translateX(22px)" : "translateX(2px)" }}
      />
    </button>
  );
}

function sevVariant(sev: CustomSeverity) {
  return sev === "deny" ? "destructive" : sev === "warn" ? "warning" : "primary";
}

export function CustomPolicyRules({
  rules,
  mode,
  onChange,
}: {
  rules: CustomRule[];
  mode: "warn" | "enforce";
  onChange: (next: CustomRule[]) => void;
}) {
  const [view, setView] = useState<"form" | "yaml">("form");
  const [yamlText, setYamlText] = useState<string>("");
  const [yamlError, setYamlError] = useState<string | null>(null);

  const errors = useMemo(
    () => rules.flatMap((r) => ruleRegexErrors(r).map((e) => `${r.name}: ${e}`)),
    [rules],
  );

  const openYaml = () => {
    setYamlError(null);
    setYamlText(rules.length ? yaml.dump(rules, { lineWidth: 1000, noRefs: true }) : EXAMPLE_RULES_YAML);
    setView("yaml");
  };

  const applyYaml = () => {
    try {
      const parsed = yaml.load(yamlText);
      if (parsed != null && !Array.isArray(parsed)) {
        setYamlError("The document must be a list of rules (start each rule with '- ').");
        return;
      }
      onChange(normalizeCustomRules(parsed || []));
      setYamlError(null);
      setView("form");
    } catch (e: any) {
      setYamlError(e?.message || "Could not parse the YAML.");
    }
  };

  const patch = (id: string, p: Partial<CustomRule>) =>
    onChange(rules.map((r) => (r.id === id ? { ...r, ...p } : r)));

  const addRule = () => {
    let base = "custom-rule";
    let id = base;
    let n = 2;
    while (rules.some((r) => r.id === id)) id = `${base}-${n++}`;
    onChange([
      ...rules,
      {
        id,
        name: "New rule",
        description: "",
        enabled: true,
        severity: "warn",
        enforcement: "inherit",
        resource_types: [],
        addresses: [],
        actions: [],
        attribute: "",
        operator: "matches",
        value: "",
        message: "",
      },
    ]);
  };

  const blocksRun = (r: CustomRule) =>
    r.enforcement === "block" || (r.enforcement !== "report" && r.severity === "deny" && mode === "enforce");

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-medium">Custom rules</div>
          <p className="text-xs text-[var(--color-muted-foreground)] max-w-2xl">
            Your own organisation-specific checks. Target resources by type or address with regex, then assert
            something about a planned attribute. Author them here or paste YAML.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {view === "form" ? (
            <>
              <Button size="sm" variant="secondary" onClick={openYaml}>
                <Code2 className="h-4 w-4 mr-1.5" /> Edit as YAML
              </Button>
              <Button size="sm" variant="secondary" onClick={addRule}>
                <Plus className="h-4 w-4 mr-1.5" /> Add rule
              </Button>
            </>
          ) : (
            <>
              <Button size="sm" variant="secondary" onClick={() => setView("form")}>
                <ListChecks className="h-4 w-4 mr-1.5" /> Back to list
              </Button>
              <Button size="sm" onClick={applyYaml}>
                Apply YAML
              </Button>
            </>
          )}
        </div>
      </div>

      {view === "yaml" ? (
        <div className="space-y-2">
          <YamlEditor value={yamlText} onChange={setYamlText} height={340} />
          {yamlError && (
            <p className="text-xs text-[var(--color-destructive)] flex items-center gap-1.5">
              <FileWarning className="h-3.5 w-3.5" /> {yamlError}
            </p>
          )}
          <p className="text-xs text-[var(--color-muted-foreground)]">
            Fields: <code className="font-mono">id, name, description, enabled, severity (info|warn|deny),
            enforcement (inherit|block|report), resource_types[], addresses[], actions[], attribute, operator,
            value, message</code>. Attribute paths support nesting and wildcards, e.g.{" "}
            <code className="font-mono">ingress.*.cidr_blocks</code>.
          </p>
          <Button size="sm" variant="ghost" onClick={() => setYamlText(EXAMPLE_RULES_YAML)}>
            Load examples
          </Button>
        </div>
      ) : rules.length === 0 ? (
        <div className="rounded-xl border border-dashed border-[var(--color-border)] p-4 text-sm text-[var(--color-muted-foreground)]">
          No custom rules yet. Add one, or paste a YAML rule pack.
        </div>
      ) : (
        <div className="space-y-3">
          {rules.map((r) => {
            const regexErrs = ruleRegexErrors(r);
            const needsValue = !["exists", "not_exists"].includes(r.operator || "matches");
            return (
              <div key={r.id} className="rounded-xl border border-[var(--color-border)] p-3 space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Input
                        className="w-full sm:w-72"
                        value={r.name}
                        placeholder="Rule name"
                        onChange={(e) => patch(r.id, { name: e.target.value })}
                      />
                      <Badge variant={sevVariant(r.severity)}>{r.severity.toUpperCase()}</Badge>
                      {r.enabled &&
                        (blocksRun(r) ? (
                          <Badge variant="destructive">Blocks run</Badge>
                        ) : (
                          <Badge variant="default">Reports only</Badge>
                        ))}
                      <code className="text-xs font-mono text-[var(--color-muted-foreground)]">{r.id}</code>
                    </div>
                    <Input
                      className="w-full"
                      value={r.description || ""}
                      placeholder="What this rule protects against (optional)"
                      onChange={(e) => patch(r.id, { description: e.target.value })}
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <Toggle
                      checked={r.enabled}
                      label={`Toggle ${r.name}`}
                      onChange={(v) => patch(r.id, { enabled: v })}
                    />
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Delete ${r.name}`}
                      onClick={() => onChange(rules.filter((x) => x.id !== r.id))}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>

                <div className="grid gap-2 sm:grid-cols-2">
                  <label className="text-xs space-y-1">
                    <span className="text-[var(--color-muted-foreground)]">
                      Resource types (regex, comma separated — empty = any)
                    </span>
                    <Input
                      placeholder="^aws_s3_bucket$, ^hcloud_.*"
                      value={listToText(r.resource_types)}
                      onChange={(e) => patch(r.id, { resource_types: textToList(e.target.value) })}
                    />
                  </label>
                  <label className="text-xs space-y-1">
                    <span className="text-[var(--color-muted-foreground)]">
                      Addresses (regex, comma separated — empty = any)
                    </span>
                    <Input
                      placeholder="^module\\.network\\..*"
                      value={listToText(r.addresses)}
                      onChange={(e) => patch(r.id, { addresses: textToList(e.target.value) })}
                    />
                  </label>
                  <label className="text-xs space-y-1">
                    <span className="text-[var(--color-muted-foreground)]">
                      Attribute path (empty = whole resource)
                    </span>
                    <Input
                      placeholder="tags.owner or ingress.*.cidr_blocks"
                      value={r.attribute || ""}
                      onChange={(e) => patch(r.id, { attribute: e.target.value })}
                    />
                  </label>
                  <label className="text-xs space-y-1">
                    <span className="text-[var(--color-muted-foreground)]">
                      Plan actions (empty = create &amp; update)
                    </span>
                    <Input
                      placeholder="create, update"
                      value={listToText(r.actions)}
                      onChange={(e) =>
                        patch(r.id, {
                          actions: textToList(e.target.value).filter((a) => ACTION_OPTIONS.includes(a)),
                        })
                      }
                    />
                  </label>
                </div>

                <div className="flex flex-wrap items-end gap-2">
                  <div className="w-52">
                    <span className="text-xs text-[var(--color-muted-foreground)]">Condition</span>
                    <Select
                      value={r.operator || "matches"}
                      onChange={(v) => patch(r.id, { operator: v })}
                      options={CUSTOM_OPERATORS}
                    />
                  </div>
                  {needsValue && (
                    <label className="text-xs space-y-1 flex-1 min-w-[12rem]">
                      <span className="text-[var(--color-muted-foreground)]">
                        {r.operator === "matches" || r.operator === "not_matches" ? "Pattern (regex)" : "Value"}
                      </span>
                      <Input
                        placeholder={r.operator === "gt" || r.operator === "lt" ? "10" : "^prd-"}
                        value={r.value || ""}
                        onChange={(e) => patch(r.id, { value: e.target.value })}
                      />
                    </label>
                  )}
                  <div className="w-32">
                    <span className="text-xs text-[var(--color-muted-foreground)]">Severity</span>
                    <Select
                      value={r.severity}
                      onChange={(v) => patch(r.id, { severity: v as CustomSeverity })}
                      options={[
                        { value: "info", label: "Info", description: "Informational only" },
                        { value: "warn", label: "Warn", description: "Reported as a warning" },
                        { value: "deny", label: "Deny", description: "Counts as a violation" },
                      ]}
                    />
                  </div>
                  <div className="w-48">
                    <span className="text-xs text-[var(--color-muted-foreground)]">Enforcement</span>
                    <Select
                      value={r.enforcement || "inherit"}
                      onChange={(v) => patch(r.id, { enforcement: v as Enforcement })}
                      options={[
                        { value: "inherit", label: "Follow gate mode", description: "Blocks only in enforce mode" },
                        { value: "block", label: "Always block", description: "Blocks even in warn mode" },
                        { value: "report", label: "Never block", description: "Only reports" },
                      ]}
                    />
                  </div>
                </div>

                <label className="text-xs space-y-1 block">
                  <span className="text-[var(--color-muted-foreground)]">
                    Message shown on a violation (optional)
                  </span>
                  <Input
                    placeholder="Buckets must not be public."
                    value={r.message || ""}
                    onChange={(e) => patch(r.id, { message: e.target.value })}
                  />
                </label>

                {regexErrs.length > 0 && (
                  <p className="text-xs text-[var(--color-destructive)] flex items-start gap-1.5">
                    <FileWarning className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                    <span>{regexErrs.join("; ")}</span>
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {view === "form" && errors.length > 0 && (
        <p className="text-xs text-[var(--color-destructive)]">
          Fix the invalid regex above — rules that fail to compile are dropped when saved.
        </p>
      )}
    </div>
  );
}
