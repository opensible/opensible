import { CheckCircle2, Circle, Loader2, XCircle, ChevronRight } from "lucide-react";

type StepState = "pending" | "running" | "success" | "failed" | "skipped";

type Step = { id: string; label: string };

const FLOWS: Record<string, Step[]> = {
  validate: [
    { id: "start", label: "Start" },
    { id: "validate", label: "Validate config" },
    { id: "done", label: "Done" },
  ],
  init: [
    { id: "start", label: "Start" },
    { id: "init", label: "Init backend" },
    { id: "providers", label: "Install providers" },
    { id: "done", label: "Done" },
  ],
  plan: [
    { id: "start", label: "Start" },
    { id: "init", label: "Init" },
    { id: "refresh", label: "Refresh state" },
    { id: "plan", label: "Plan changes" },
    { id: "done", label: "Done" },
  ],
  apply: [
    { id: "start", label: "Start" },
    { id: "init", label: "Init" },
    { id: "refresh", label: "Refresh state" },
    { id: "plan", label: "Plan" },
    { id: "apply", label: "Apply" },
    { id: "done", label: "Done" },
  ],
  refresh: [
    { id: "start", label: "Start" },
    { id: "init", label: "Init" },
    { id: "refresh", label: "Refresh state" },
    { id: "done", label: "Done" },
  ],
  destroy: [
    { id: "start", label: "Start" },
    { id: "init", label: "Init" },
    { id: "refresh", label: "Refresh state" },
    { id: "plan", label: "Plan destroy" },
    { id: "destroy", label: "Destroy" },
    { id: "done", label: "Done" },
  ],
  drift: [
    { id: "start", label: "Start" },
    { id: "init", label: "Init" },
    { id: "refresh", label: "Refresh state" },
    { id: "drift", label: "Detect drift" },
    { id: "done", label: "Done" },
  ],
};

function detectStates(action: string, log: string, status?: string): Record<string, StepState> {
  const steps = FLOWS[action] || [];
  const states: Record<string, StepState> = {};
  steps.forEach(s => (states[s.id] = "pending"));

  const L = log || "";
  const has = (re: RegExp) => re.test(L);

  // Start always completed once we have any log line
  if (L.length > 0) states.start = "success";

  if ("init" in states) {
    if (has(/Initializing (the backend|provider|modules)|Initializing\.\.\./i)) states.init = "running";
    if (has(/has been successfully initialized|Terraform has been successfully|OpenTofu has been successfully/i)) states.init = "success";
  }
  if ("providers" in states) {
    if (has(/Installing |Finding .+ versions|Using previously-installed/i)) states.providers = "running";
    if (has(/has been successfully initialized/i)) states.providers = "success";
  }
  if ("validate" in states) {
    if (has(/tofu validate|terraform validate/i)) states.validate = "running";
    if (has(/Success! The configuration is valid/i)) states.validate = "success";
    if (has(/Error: |configuration is invalid/i)) states.validate = "failed";
  }
  if ("refresh" in states) {
    if (has(/Refreshing state|Reading\.\.\.|Read complete/i)) states.refresh = "running";
    if (has(/Plan:|No changes\.|Apply complete!|Destroy complete!|Changes to Outputs:/i)) states.refresh = "success";
  }
  if ("plan" in states) {
    if (has(/Terraform will perform|OpenTofu will perform|Terraform used the selected providers|OpenTofu used the selected providers/i)) states.plan = "running";
    if (has(/Plan: \d+ to add|No changes\.|Saved the plan to/i)) states.plan = "success";
  }
  if ("drift" in states) {
    if (has(/Detecting drift|tofu plan -refresh-only|OpenTofu will perform|Terraform will perform/i)) states.drift = "running";
    if (has(/\[drift\]\s*(DRIFT DETECTED|no drift)|Objects have changed outside of (OpenTofu|Terraform)|No changes\./i)) states.drift = "success";
  }
  if ("apply" in states) {
    if (has(/: Creating\.\.\.|: Modifying\.\.\.|Still creating|Still modifying|Apply\.\.\./i)) states.apply = "running";
    if (has(/Apply complete!/i)) states.apply = "success";
  }
  if ("destroy" in states) {
    if (has(/: Destroying\.\.\.|Still destroying/i)) states.destroy = "running";
    if (has(/Destroy complete!/i)) states.destroy = "success";
  }

  // Final status resolution
  const finalSucceeded = status === "succeeded";
  const finalFailed = status === "failed" || status === "error" || status === "canceled" || status === "cancelled";

  if (finalSucceeded) {
    // Mark every pending/running before "done" as success
    let seenDone = false;
    for (const s of steps) {
      if (s.id === "done") { states.done = "success"; seenDone = true; continue; }
      if (!seenDone && (states[s.id] === "pending" || states[s.id] === "running")) states[s.id] = "success";
    }
  } else if (finalFailed) {
    // First running/pending after start becomes failed; rest skipped
    let failedSet = false;
    for (const s of steps) {
      if (s.id === "done") { states.done = "failed"; continue; }
      if (states[s.id] === "success") continue;
      if (!failedSet) { states[s.id] = "failed"; failedSet = true; }
      else states[s.id] = "skipped";
    }
    if (!failedSet) states.done = "failed";
  } else if (status === "running" || status === "queued") {
    // Mark the latest non-pending as running if none currently running
    const hasRunning = Object.values(states).some(v => v === "running");
    if (!hasRunning) {
      // pick first pending after a success
      const idx = steps.findIndex(s => states[s.id] === "pending");
    if (idx > 0) {
      const s = steps[idx];
      if (s) states[s.id] = "running";
    }
    }
  }

  return states;
}

function StepDot({ state }: { state: StepState }) {
  if (state === "success") return <CheckCircle2 className="h-5 w-5" style={{ color: "var(--color-success)" }} />;
  if (state === "running") return <Loader2 className="h-5 w-5 animate-spin" style={{ color: "var(--color-primary)" }} />;
  if (state === "failed") return <XCircle className="h-5 w-5" style={{ color: "var(--color-destructive)" }} />;
  if (state === "skipped") return <Circle className="h-5 w-5 opacity-30" />;
  return <Circle className="h-5 w-5 opacity-50" />;
}

export function RunFlowGraph({ action, log, status }: { action: string; log: string; status?: string }) {
  const steps = FLOWS[action];
  if (!steps) return null;
  const states = detectStates(action, log, status);

  const tone = (s: StepState) =>
    s === "success" ? "var(--color-success)" :
    s === "running" ? "var(--color-primary)" :
    s === "failed" ? "var(--color-destructive)" :
    s === "skipped" ? "var(--color-muted-foreground)" :
    "var(--color-muted-foreground)";

  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm font-semibold">
          Run flow · <span className="font-mono text-xs uppercase">{action}</span>
        </div>
        <div className="text-xs text-[var(--color-muted-foreground)]">
          {status ? <>status: <b style={{ color: tone(status === "succeeded" ? "success" : status === "failed" ? "failed" : "running") }}>{status}</b></> : null}
        </div>
      </div>
      <div className="flex items-center gap-1 overflow-x-auto">
        {steps.map((step, i) => {
          const st: StepState = states[step.id] ?? "pending";
          return (
            <div key={step.id} className="flex items-center gap-1 shrink-0">
              <div
                className="flex items-center gap-2 rounded-md border px-3 py-2 min-w-[140px]"
                style={{
                  borderColor: st === "pending" ? "var(--color-border)" : tone(st),
                  background: st === "running"
                    ? "color-mix(in srgb, var(--color-primary) 10%, transparent)"
                    : st === "success"
                    ? "color-mix(in srgb, var(--color-success) 10%, transparent)"
                    : st === "failed"
                    ? "color-mix(in srgb, var(--color-destructive) 10%, transparent)"
                    : "transparent",
                }}
              >
                <StepDot state={st} />
                <div className="flex flex-col">
                  <span className="text-[10px] uppercase tracking-wide text-[var(--color-muted-foreground)]">Step {i + 1}</span>
                  <span className="text-xs font-medium" style={{ color: tone(st) }}>{step.label}</span>
                </div>
              </div>
              {i < steps.length - 1 && (
                <ChevronRight className="h-4 w-4 shrink-0" style={{ color: tone(st) }} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
