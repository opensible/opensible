import { useMemo, useState } from "react";
import { ChevronRight, FilePlus2, FileMinus2, FileDiff, RefreshCcwDot, Eye, CheckCircle2, ShieldCheck, ShieldAlert, ShieldX } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { parseTofuPlan, ACTION_META, type PlanChangeAction, type PlanResource, type PolicyReport } from "@/lib/tofu-plan";
import { cn } from "@/lib/utils";

const TONE_CLASS: Record<string, string> = {
  create: "text-emerald-600 dark:text-emerald-400",
  update: "text-amber-600 dark:text-amber-400",
  destroy: "text-red-600 dark:text-red-400",
  neutral: "text-[var(--color-muted-foreground)]",
};

const ACTION_ICON: Record<PlanChangeAction, any> = {
  create: FilePlus2,
  update: FileDiff,
  replace: RefreshCcwDot,
  destroy: FileMinus2,
  read: Eye,
  drift: RefreshCcwDot,
};

function ResourceRow({ res }: { res: PlanResource }) {
  const [open, setOpen] = useState(false);
  const meta = ACTION_META[res.action];
  const Icon = ACTION_ICON[res.action];
  const tone = TONE_CLASS[meta.tone] ?? TONE_CLASS.neutral;
  return (
    <div className="border-b border-[var(--color-border)] last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-[var(--color-accent)]/40"
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-[var(--color-muted-foreground)] transition-transform",
            open && "rotate-90",
            res.attrs.length === 0 && "opacity-0"
          )}
        />
        <Icon className={cn("h-4 w-4 shrink-0", tone)} />
        <span className={cn("font-mono text-xs font-semibold w-4 shrink-0", tone)}>{meta.sign}</span>
        <span className="font-mono text-xs truncate flex-1">{res.address}</span>
        <span className={cn("text-[10px] uppercase tracking-wide shrink-0", tone)}>{meta.label}</span>
      </button>
      {open && res.attrs.length > 0 && (
        <div className="bg-[var(--color-muted)]/40 px-4 py-2 font-mono text-[11px] leading-5 overflow-x-auto">
          {res.attrs.map((a, i) => (
            <div key={`${a.name}-${i}`} className="whitespace-pre">
              <span
                className={cn(
                  a.op === "+" ? TONE_CLASS.create : a.op === "-" ? TONE_CLASS.destroy : TONE_CLASS.update
                )}
              >
                {a.op}
              </span>{" "}
              <span className="text-[var(--color-foreground)]">{a.name}</span>
              {" = "}
              {a.op === "~" ? (
                <>
                  <span className={TONE_CLASS.destroy}>{a.before ?? "(known after apply)"}</span>
                  <span className="text-[var(--color-muted-foreground)]"> → </span>
                  <span className={TONE_CLASS.create}>{a.after ?? "(known after apply)"}</span>
                </>
              ) : (
                <span className="text-[var(--color-muted-foreground)]">{a.after ?? a.before}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({ value, label, tone }: { value: number; label: string; tone: string }) {
  return (
    <div className="flex flex-col items-center rounded-md border border-[var(--color-border)] px-4 py-2 min-w-[92px]">
      <span className={cn("text-xl font-semibold tabular-nums", tone)}>{value}</span>
      <span className="text-[10px] uppercase tracking-wide text-[var(--color-muted-foreground)]">{label}</span>
    </div>
  );
}

function PolicySection({ policy }: { policy: PolicyReport }) {
  const Icon = policy.blocked ? ShieldX : policy.violations.length ? ShieldAlert : ShieldCheck;
  const tone = policy.blocked
    ? TONE_CLASS.destroy
    : policy.violations.length
      ? TONE_CLASS.update
      : TONE_CLASS.create;
  return (
    <div className="rounded-md border border-[var(--color-border)]">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-b border-[var(--color-border)]">
        <Icon className={cn("h-4 w-4", tone)} />
        <span className="text-xs font-medium">Policy-as-code gate</span>
        <Badge variant="default" className="text-[10px]">mode: {policy.mode}</Badge>
        {policy.blocked ? (
          <Badge variant="destructive" className="text-[10px]">Run blocked</Badge>
        ) : policy.violations.length ? (
          <Badge variant="warning" className="text-[10px]">Violations reported</Badge>
        ) : (
          <Badge variant="success" className="text-[10px]">Passed</Badge>
        )}
        <span className="ml-auto text-[11px] text-[var(--color-muted-foreground)] tabular-nums">
          {policy.denies} deny · {policy.warns} warn
        </span>
      </div>

      {policy.blocked && policy.blockedBy.length > 0 && (
        <div className="px-3 py-2 text-[11px] border-b border-[var(--color-border)]">
          <span className="text-[var(--color-muted-foreground)]">Blocked by: </span>
          <span className={cn("font-mono", TONE_CLASS.destroy)}>{policy.blockedBy.join(", ")}</span>
        </div>
      )}

      {policy.violations.length > 0 ? (
        <div className="divide-y divide-[var(--color-border)]">
          {policy.violations.map((v, i) => (
            <div key={`${v.rule}-${i}`} className="flex flex-wrap items-start gap-2 px-3 py-2">
              <span
                className={cn(
                  "text-[10px] font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 shrink-0",
                  v.level === "warn" ? TONE_CLASS.update : TONE_CLASS.destroy
                )}
              >
                {v.level}
              </span>
              <span className="font-mono text-[11px] shrink-0">{v.rule}</span>
              {v.address && (
                <span className="font-mono text-[11px] text-[var(--color-muted-foreground)] truncate">
                  {v.address}
                </span>
              )}
              <span className="text-[11px] basis-full sm:basis-auto sm:flex-1 text-[var(--color-muted-foreground)]">
                {v.message}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="px-3 py-2 text-[11px] text-[var(--color-muted-foreground)]">
          No policy violations found.
        </p>
      )}
    </div>
  );
}

export function PlanDiff({
  log,
  action,
  className,
}: {
  log: string;
  action?: string;
  className?: string;
}) {
  const parsed = useMemo(() => parseTofuPlan(log), [log]);
  const [filter, setFilter] = useState<"all" | PlanChangeAction>("all");

  if (!parsed.hasPlan) return null;

  const counts = parsed.resources.reduce<Record<string, number>>((acc, r) => {
    acc[r.action] = (acc[r.action] ?? 0) + 1;
    return acc;
  }, {});

  const shown = filter === "all" ? parsed.resources : parsed.resources.filter((r) => r.action === filter);
  const s = parsed.summary;
  const isDrift = action === "drift" || action === "refresh";

  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-center justify-between gap-3 pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <FileDiff className="h-4 w-4" />
          {isDrift ? "Drift diff" : s?.applied ? "Apply result" : "Plan diff"}
        </CardTitle>
        <div className="flex items-center gap-2">
          {parsed.driftDetected && (
            <Badge variant="destructive" className="text-[10px] gap-1">
              <RefreshCcwDot className="h-3 w-3" /> Drift detected
            </Badge>
          )}
          {parsed.noDrift && (
            <Badge variant="success" className="text-[10px] gap-1">
              <CheckCircle2 className="h-3 w-3" /> No drift
            </Badge>
          )}
          {s?.noChanges && (
            <Badge variant="success" className="text-[10px] gap-1">
              <CheckCircle2 className="h-3 w-3" /> No changes
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {parsed.policy && <PolicySection policy={parsed.policy} />}

        {isDrift && parsed.driftDetected && parsed.resources.length === 0 && (
          <p className="text-xs text-[var(--color-muted-foreground)]">
            The live infrastructure differs from the recorded state. No managed resource blocks were
            reported — the difference is in the state/outputs shown below.
          </p>
        )}

        {s && !s.noChanges && (
          <div className="flex flex-wrap gap-2">
            <Stat value={s.add} label={s.applied ? "added" : "to add"} tone={TONE_CLASS.create!} />
            <Stat value={s.change} label={s.applied ? "changed" : "to change"} tone={TONE_CLASS.update!} />
            <Stat value={s.destroy} label={s.applied ? "destroyed" : "to destroy"} tone={TONE_CLASS.destroy!} />
          </div>
        )}

        {parsed.resources.length > 0 && (
          <>
            <div className="flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() => setFilter("all")}
                className={cn(
                  "text-[11px] rounded-full border border-[var(--color-border)] px-2.5 py-1",
                  filter === "all" ? "bg-[var(--color-accent)]" : "hover:bg-[var(--color-accent)]/50"
                )}
              >
                All ({parsed.resources.length})
              </button>
              {(Object.keys(counts) as PlanChangeAction[]).map((a) => (
                <button
                  key={a}
                  type="button"
                  onClick={() => setFilter(a)}
                  className={cn(
                    "text-[11px] rounded-full border border-[var(--color-border)] px-2.5 py-1",
                    filter === a ? "bg-[var(--color-accent)]" : "hover:bg-[var(--color-accent)]/50",
                    TONE_CLASS[ACTION_META[a].tone]
                  )}
                >
                  {ACTION_META[a].label} ({counts[a]})
                </button>
              ))}
            </div>

            <div className="border border-[var(--color-border)] rounded-md overflow-hidden">
              {shown.map((r) => (
                <ResourceRow key={r.address + r.action} res={r} />
              ))}
            </div>
          </>
        )}

        {parsed.outputs.length > 0 && (
          <div>
            <div className="text-xs font-medium mb-1">Output changes</div>
            <div className="rounded-md bg-[var(--color-muted)]/40 px-3 py-2 font-mono text-[11px] leading-5">
              {parsed.outputs.map((o, i) => (
                <div key={`${o.name}-${i}`} className="whitespace-pre">
                  <span
                    className={cn(
                      o.op === "+" ? TONE_CLASS.create : o.op === "-" ? TONE_CLASS.destroy : TONE_CLASS.update
                    )}
                  >
                    {o.op}
                  </span>{" "}
                  {o.name} = {o.op === "~" ? `${o.before} → ${o.after}` : (o.after ?? o.before)}
                </div>
              ))}
            </div>
          </div>
        )}

        <p className="text-[11px] text-[var(--color-muted-foreground)]">
          Parsed from the run log. The full OpenTofu output remains below as the source of truth.
        </p>
      </CardContent>
    </Card>
  );
}
