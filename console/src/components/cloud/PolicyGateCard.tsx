/**
 * Policy-as-code gate panel for a single Cloud stack.
 *
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ShieldCheck, ShieldAlert, ShieldX, Save, Info } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { CustomPolicyRules, type CustomRule } from "@/components/cloud/CustomPolicyRules";
import { api } from "@/lib/api";

type Severity = "warn" | "deny";
type Enforcement = "inherit" | "block" | "report";

type Rule = {
  enabled: boolean;
  severity: Severity;
  enforcement?: Enforcement;
  max_destroy?: number;
  types?: string[];
  keys?: string[];
  ports?: number[];
  limit?: number;
};

type PolicyConfig = {
  mode: "warn" | "enforce";
  rules: Record<string, Rule>;
  custom_rules?: CustomRule[];
};

type Violation = {
  rule: string;
  name?: string;
  severity: Severity | "info";
  blocking?: boolean;
  address?: string;
  type?: string;
  message: string;
};

type LastResult = {
  run_id?: string;
  action?: string;
  checked_at?: number | null;
  verdict?: "pass" | "warn" | "fail" | string;
  denies?: number;
  warns?: number;
  infos?: number;
  blocked?: boolean;
  blocked_by?: string[];
  violations?: Violation[];
} | null;

type PolicyResp = {
  enabled: boolean;
  policy: PolicyConfig;
  last_result: LastResult;
};


const RULE_META: Record<string, { title: string; help: string }> = {
  deny_destroy: {
    title: "Block destructive changes",
    help: "Fires when the plan would delete resources beyond the allowed count.",
  },
  denied_resource_types: {
    title: "Denied resource types",
    help: "Fires when the plan creates or updates a resource type on your block list.",
  },
  require_tags: {
    title: "Required tags",
    help: "Fires when a created or updated taggable resource is missing a required tag key.",
  },
  deny_public_ingress: {
    title: "No public ingress on sensitive ports",
    help: "Fires when a firewall or security-group rule opens a listed port to 0.0.0.0/0 or ::/0.",
  },
  max_created: {
    title: "Maximum resources created",
    help: "Fires when a single plan creates more resources than the limit — a blast-radius guard.",
  },
};

const RULE_ORDER = [
  "deny_public_ingress",
  "deny_destroy",
  "require_tags",
  "denied_resource_types",
  "max_created",
];

function Toggle({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50"
      style={{ background: checked ? "var(--color-primary)" : "var(--color-muted)" }}
    >
      <span
        className="inline-block h-5 w-5 rounded-full bg-[var(--color-background)] shadow transition-transform"
        style={{ transform: checked ? "translateX(22px)" : "translateX(2px)" }}
      />
    </button>
  );
}

function listToText(v?: Array<string | number>) {
  return (v || []).join(", ");
}

function textToList(s: string) {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}
function ruleLabel(key: string) {
  if (key?.startsWith("custom:")) return key.slice("custom:".length);
  return RULE_META[key]?.title || key;
}


/** Does this rule stop a run, given the gate-level mode? */
function ruleBlocks(rule: Rule, mode: PolicyConfig["mode"]) {
  const enf = rule.enforcement || "inherit";
  if (enf === "block") return true;
  if (enf === "report") return false;
  return rule.severity === "deny" && mode === "enforce";
}

export function PolicyGateCard({ stackId }: { stackId: string }) {
  const url = `/api/cloud/stacks/${encodeURIComponent(stackId)}/policy`;
  const q = useQuery({
    queryKey: ["cloud", "stack-policy", stackId],
    queryFn: () => api<PolicyResp>("GET", url),
    refetchInterval: 20000,
  });

  const [draft, setDraft] = useState<PolicyConfig | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (q.data?.policy && !dirty) setDraft(q.data.policy);
  }, [q.data, dirty]);

  const enabled = !!q.data?.enabled;
  const last = q.data?.last_result || null;

  const saveMut = useMutation({
    mutationFn: (body: { enabled?: boolean; policy?: PolicyConfig }) =>
      api<PolicyResp>("PUT", url, body),
    onSuccess: (_d, vars) => {
      setDirty(false);
      q.refetch();
      toast.success(
        vars.enabled === undefined
          ? "Policy rules saved"
          : vars.enabled
          ? "Policy gate enabled"
          : "Policy gate disabled",
      );
    },
    onError: (e: any) => toast.error(e?.message || "Failed to update the policy gate"),
  });

  const patchRule = (id: string, patch: Partial<Rule>) => {
    if (!draft) return;
    const current = draft.rules[id];
    if (!current) return;
    setDirty(true);
    setDraft({ ...draft, rules: { ...draft.rules, [id]: { ...current, ...patch } } });
  };

  const verdictBadge = (() => {
    switch (last?.verdict) {
      case "pass":
        return { label: "Last check passed", variant: "success" as const, Icon: ShieldCheck };
      case "warn":
        return { label: `${last?.warns || 0} warning(s)`, variant: "warning" as const, Icon: ShieldAlert };
      case "fail":
        return { label: `${last?.denies || 0} violation(s)`, variant: "destructive" as const, Icon: ShieldX };
      default:
        return { label: "Not evaluated yet", variant: "default" as const, Icon: ShieldCheck };
    }
  })();

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle className="text-base flex items-center gap-2">
            <verdictBadge.Icon className="h-4 w-4" /> Policy gate
            {enabled ? (
              <Badge variant={verdictBadge.variant}>{verdictBadge.label}</Badge>
            ) : (
              <Badge variant="default">Disabled</Badge>
            )}
          </CardTitle>
          <p className="text-xs text-[var(--color-muted-foreground)] mt-1 max-w-2xl">
            Evaluates every plan, apply and destroy against your rules before the change lands. Rules run
            inside the worker against the JSON plan — no extra service, agent or policy engine to operate.
            Disabled by default; when off, runs behave exactly as before.
          </p>
        </div>
        <Toggle
          checked={enabled}
          disabled={saveMut.isPending}
          label="Toggle policy gate"
          onChange={(v) => saveMut.mutate({ enabled: v })}
        />
      </CardHeader>

      <CardContent>
        {!enabled ? (
          <div className="text-sm text-[var(--color-muted-foreground)]">
            The policy gate is turned off for this stack. Enable it to check plans against guardrails such as
            "no public SSH", "no unexpected destroys" and "required tags".
          </div>
        ) : !draft ? (
          <div className="text-sm text-[var(--color-muted-foreground)]">Loading rules…</div>
        ) : (
          <div className="space-y-5">
            {/* Mode */}
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-sm font-medium">Mode</span>
              <div className="w-56">
                <Select
                  value={draft.mode}
                  onChange={(v) => {
                    setDirty(true);
                    setDraft({ ...draft, mode: v as PolicyConfig["mode"] });
                  }}
                  options={[
                    { value: "warn", label: "Warn", description: "Report violations, never block a run" },
                    { value: "enforce", label: "Enforce", description: "Block the run when a deny rule fires" },
                  ]}
                />
              </div>
              <span className="text-xs text-[var(--color-muted-foreground)] flex items-center gap-1">
                <Info className="h-3.5 w-3.5" />
                {draft.mode === "enforce"
                  ? "Deny-severity rules block the run unless a rule overrides it."
                  : "Rules only report — unless a rule is set to \u201cAlways block\u201d."}
              </span>
            </div>

            {/* Rules */}
            <div className="space-y-3">
              {RULE_ORDER.filter((id) => draft.rules[id]).map((id) => {
                const rule = draft.rules[id]!;
                const meta = RULE_META[id]!;
                return (
                  <div
                    key={id}
                    className="rounded-xl border border-[var(--color-border)] p-3 flex flex-col gap-3"
                  >
                    <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium flex flex-wrap items-center gap-2">
                        <span className="break-words">{meta.title}</span>
                        {rule.enabled &&
                          (ruleBlocks(rule, draft.mode) ? (
                            <Badge variant="destructive">Blocks run</Badge>
                          ) : (
                            <Badge variant="warning">Reports only</Badge>
                          ))}
                      </div>
                      <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">{meta.help}</p>

                      {rule.enabled && (
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          {id === "deny_destroy" && (
                            <label className="text-xs flex items-center gap-2">
                              Allowed destroys
                              <Input
                                type="number"
                                min={0}
                                className="w-24"
                                value={rule.max_destroy ?? 0}
                                onChange={(e) =>
                                  patchRule(id, { max_destroy: Math.max(0, Number(e.target.value) || 0) })
                                }
                              />
                            </label>
                          )}
                          {id === "max_created" && (
                            <label className="text-xs flex items-center gap-2">
                              Limit
                              <Input
                                type="number"
                                min={1}
                                className="w-24"
                                value={rule.limit ?? 50}
                                onChange={(e) => patchRule(id, { limit: Math.max(1, Number(e.target.value) || 1) })}
                              />
                            </label>
                          )}
                          {id === "deny_public_ingress" && (
                            <label className="text-xs flex items-center gap-2 w-full sm:w-auto">
                              Ports
                              <Input
                                className="w-full sm:w-64"
                                placeholder="22, 3389"
                                value={listToText(rule.ports)}
                                onChange={(e) =>
                                  patchRule(id, {
                                    ports: textToList(e.target.value)
                                      .map((p) => Number(p))
                                      .filter((n) => Number.isFinite(n) && n > 0 && n < 65536),
                                  })
                                }
                              />
                            </label>
                          )}
                          {id === "require_tags" && (
                            <label className="text-xs flex items-center gap-2 w-full sm:w-auto">
                              Tag keys
                              <Input
                                className="w-full sm:w-72"
                                placeholder="environment, owner"
                                value={listToText(rule.keys)}
                                onChange={(e) => patchRule(id, { keys: textToList(e.target.value) })}
                              />
                            </label>
                          )}
                          {id === "denied_resource_types" && (
                            <label className="text-xs flex items-center gap-2 w-full sm:w-auto">
                              Types
                              <Input
                                className="w-full sm:w-80"
                                placeholder="aws_iam_user, hcloud_server"
                                value={listToText(rule.types)}
                                onChange={(e) => patchRule(id, { types: textToList(e.target.value) })}
                              />
                            </label>
                          )}
                        </div>
                      )}
                    </div>
                      <Toggle
                        checked={rule.enabled}
                        label={`Toggle ${meta.title}`}
                        onChange={(v) => patchRule(id, { enabled: v })}
                      />
                    </div>

                    {rule.enabled && (
                      <div className="flex flex-wrap items-center gap-3">
                        <div className="w-32">
                          <Select
                            value={rule.severity}
                            onChange={(v) => patchRule(id, { severity: v as Severity })}
                            options={[
                              { value: "warn", label: "Warn" },
                              { value: "deny", label: "Deny" },
                            ]}
                          />
                        </div>
                        <div className="w-48">
                          <Select
                            value={rule.enforcement || "inherit"}
                            onChange={(v) => patchRule(id, { enforcement: v as Enforcement })}
                            options={[
                              {
                                value: "inherit",
                                label: "Follow gate mode",
                                description: "Blocks only when the gate is in enforce mode",
                              },
                              {
                                value: "block",
                                label: "Always block",
                                description: "Blocks the run even while the gate is in warn mode",
                              },
                              {
                                value: "report",
                                label: "Never block",
                                description: "Only reports, even while the gate is in enforce mode",
                              },
                            ]}
                          />
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            <div className="h-px bg-[var(--color-border)]" />

            <CustomPolicyRules
              rules={draft.custom_rules || []}
              mode={draft.mode}
              onChange={(next) => {
                setDirty(true);
                setDraft({ ...draft, custom_rules: next });
              }}
            />



            <div className="flex items-center gap-3">
              <Button
                size="sm"
                disabled={!dirty || saveMut.isPending}
                onClick={() => saveMut.mutate({ policy: draft })}
              >
                <Save className="h-4 w-4 mr-1.5" /> Save rules
              </Button>
              {dirty && (
                <span className="text-xs text-[var(--color-muted-foreground)]">Unsaved changes</span>
              )}
            </div>

            {/* Last verdict */}
            {last && (
              <div className="rounded-xl border border-[var(--color-border)] p-3">
                <div className="text-sm font-medium flex items-center gap-2">
                  Last evaluation
                  <Badge variant={verdictBadge.variant}>{verdictBadge.label}</Badge>
                  <span className="text-xs font-normal text-[var(--color-muted-foreground)]">
                    from {last.action || "run"}
                  </span>
                </div>
                {last.blocked && (last.blocked_by || []).length > 0 && (
                  <p className="mt-1 text-xs text-[var(--color-destructive)]">
                    Run blocked by: {(last.blocked_by || []).map(ruleLabel).join(", ")}
                  </p>
                )}
                {last.violations && last.violations.length > 0 ? (
                  <ul className="mt-2 space-y-1">
                    {last.violations.slice(0, 25).map((v, i) => (
                      <li key={i} className="text-xs flex flex-wrap items-baseline gap-2">
                        <Badge
                          variant={
                            v.blocking || v.severity === "deny"
                              ? "destructive"
                              : v.severity === "info"
                              ? "primary"
                              : "warning"
                          }
                        >
                          {v.blocking
                            ? "BLOCK"
                            : v.severity === "deny"
                            ? "DENY"
                            : v.severity === "info"
                            ? "INFO"
                            : "WARN"}
                        </Badge>
                        <span className="text-[var(--color-muted-foreground)]">
                          {v.name || ruleLabel(v.rule)}
                        </span>

                        <code className="font-mono">{v.address || "plan"}</code>
                        <span className="text-[var(--color-muted-foreground)]">{v.message}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-[var(--color-muted-foreground)] mt-1">
                    No violations in the last evaluated run.
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
