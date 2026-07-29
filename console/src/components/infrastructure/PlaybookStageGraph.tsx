import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as yaml from "js-yaml";
import { CheckCircle2, Circle, Loader2, XCircle, ChevronRight, Workflow } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ChevronDown, ChevronUp } from "lucide-react";
import { api } from "@/lib/api";

type StepState = "pending" | "running" | "success" | "failed" | "skipped";

type Stage = { id: string; label: string };

function parsePlays(yamlText: string): Stage[] {
  if (!yamlText) return [];
  try {
    const doc = yaml.load(yamlText);
    if (!Array.isArray(doc)) return [];
    const stages: Stage[] = [];
    doc.forEach((play: any, i: number) => {
      if (!play || typeof play !== "object") return;
      const raw = String(play.name || `Play ${i + 1}`);
      // strip leading "[stage] " prefix so labels look clean
      const label = raw.replace(/^\s*\[[^\]]+\]\s*/, "").trim() || `Play ${i + 1}`;
      stages.push({ id: `play-${i}`, label });
    });
    return stages;
  } catch {
    return [];
  }
}

function detectStates(
  stages: Stage[],
  yamlText: string,
  log: string,
  status?: string,
): Record<string, StepState> {
  const states: Record<string, StepState> = {};
  stages.forEach((s) => (states[s.id] = "pending"));
  if (stages.length === 0) return states;

  // recover original play names in order for PLAY marker matching
  let originalNames: string[] = [];
  try {
    const doc = yaml.load(yamlText);
    if (Array.isArray(doc)) {
      originalNames = doc.map((p: any, i: number) => String(p?.name || `Play ${i + 1}`));
    }
  } catch {
    originalNames = stages.map((s) => s.label);
  }

  const L = log || "";
  // Ansible prints:  PLAY [name] ****  and finally  PLAY RECAP ****
  const playRe = /PLAY\s*\[([^\]]+)\]/g;
  const seen: number[] = [];
  let m: RegExpExecArray | null;
  while ((m = playRe.exec(L)) !== null) {
    const name = (m[1] || "").trim();
    if (name.toUpperCase().startsWith("RECAP")) break;
    const idx = originalNames.findIndex((n, i) => n === name && !seen.includes(i));
    if (idx >= 0) seen.push(idx);
  }
  const hasRecap = /PLAY RECAP/.test(L);
  const failedFatal = /\bfatal:\s|\bfailed=([1-9]\d*)/i.test(L);

  const finalSucceeded = status === "succeeded";
  const finalFailed = status === "failed" || status === "error" || status === "canceled" || status === "cancelled";
  const isRunning = status === "running" || status === "queued";

  // Mark all seen plays as success by default; the currently running one is the last seen
  seen.forEach((idx, k) => {
    const isLast = k === seen.length - 1;
    const st = stages[idx];
    if (!st) return;
    if (isLast && !hasRecap && isRunning) states[st.id] = "running";
    else states[st.id] = "success";
  });

  if (hasRecap || finalSucceeded) {
    if (finalSucceeded) {
      stages.forEach((s) => { if (states[s.id] !== "failed") states[s.id] = "success"; });
    } else if (finalFailed || failedFatal) {
      // Mark last executed play failed, remaining skipped
      const lastIdx = seen.length > 0 ? (seen[seen.length - 1] ?? 0) : 0;
      stages.forEach((s, i) => {
        if (i < lastIdx) states[s.id] = "success";
        else if (i === lastIdx) states[s.id] = "failed";
        else states[s.id] = "skipped";
      });
    }
  } else if (finalFailed) {
    const lastIdx = seen.length > 0 ? (seen[seen.length - 1] ?? 0) : 0;
    stages.forEach((s, i) => {
      if (i < lastIdx) states[s.id] = "success";
      else if (i === lastIdx) states[s.id] = "failed";
      else states[s.id] = "skipped";
    });
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

const tone = (s: StepState) =>
  s === "success" ? "var(--color-success)" :
  s === "running" ? "var(--color-primary)" :
  s === "failed" ? "var(--color-destructive)" :
  s === "skipped" ? "var(--color-muted-foreground)" :
  "var(--color-muted-foreground)";

export function PlaybookStageGraph({
  yamlText,
  executionId,
  status,
}: {
  yamlText: string;
  executionId?: string | null;
  status?: string;
}) {
  const stages = parsePlays(yamlText);
  const [log, setLog] = useState("");
  const offsetRef = useRef(0);
  const [collapsed, setCollapsed] = useState(false);

  // Reset log when execution changes
  useEffect(() => {
    setLog("");
    offsetRef.current = 0;
  }, [executionId]);

  const done = status === "succeeded" || status === "failed" || status === "error" ||
               status === "canceled" || status === "cancelled";

  useQuery({
    queryKey: ["stage-graph-log", executionId, offsetRef.current, done],
    queryFn: async () => {
      if (!executionId) return null;
      const res = await api<{ text?: string; nextOffset?: number }>(
        "GET",
        `/api/executions/${encodeURIComponent(executionId)}/log?offset=${offsetRef.current}`,
      );
      if (res.text) setLog((t) => t + res.text);
      if (typeof res.nextOffset === "number") offsetRef.current = res.nextOffset;
      return res;
    },
    enabled: !!executionId,
    refetchInterval: !executionId ? false : done ? false : 3000,
  });

  if (stages.length === 0) return null;

  const states = detectStates(stages, yamlText, log, status);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base flex items-center gap-2">
          <Workflow className="h-4 w-4" /> Stage view
          <span className="text-xs font-normal text-[var(--color-muted-foreground)]">
            · {stages.length} play{stages.length === 1 ? "" : "s"}
          </span>
          {status && (
            <span className="text-xs font-normal text-[var(--color-muted-foreground)] ml-2">
              status:{" "}
              <b style={{ color: tone(status === "succeeded" ? "success" : status === "failed" ? "failed" : "running") }}>
                {status}
              </b>
            </span>
          )}
        </CardTitle>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setCollapsed((v) => !v)}
          title={collapsed ? "Show stages" : "Hide stages"}
        >
          {collapsed ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
        </Button>
      </CardHeader>
      {!collapsed && (
        <CardContent>
          <div className="flex items-start gap-1 overflow-x-auto pb-2 flex-wrap">
            {stages.map((step, i) => {
              const st: StepState = states[step.id] ?? "pending";
              return (
                <div key={step.id} className="flex items-center gap-1 shrink-0">
                  <div
                    className="flex items-center gap-2 rounded-md border px-3 py-2 min-w-[180px] max-w-[260px]"
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
                    <div className="flex flex-col min-w-0">
                      <span className="text-[10px] uppercase tracking-wide text-[var(--color-muted-foreground)]">
                        Stage {i + 1}
                      </span>
                      <span
                        className="text-xs font-medium truncate"
                        style={{ color: tone(st) }}
                        title={step.label}
                      >
                        {step.label}
                      </span>
                    </div>
                  </div>
                  {i < stages.length - 1 && (
                    <ChevronRight className="h-4 w-4 shrink-0" style={{ color: tone(st) }} />
                  )}
                </div>
              );
            })}
          </div>
          {!executionId && (
            <p className="mt-3 text-xs text-[var(--color-muted-foreground)]">
              Run this job to see live stage progress. Stages are derived from the rendered playbook plays.
            </p>
          )}
        </CardContent>
      )}
    </Card>
  );
}
