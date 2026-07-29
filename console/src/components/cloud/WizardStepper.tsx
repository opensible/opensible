import { cn } from "@/lib/utils";
import { CheckCircle2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type WizardStep = {
  title: string;
  Icon: LucideIcon;
  active: boolean;
  done: boolean;
};

export function WizardStepper({ steps, onStepClick }: {
  steps: WizardStep[];
  onStepClick: (index: number) => void;
}) {
  return (
    <nav className="flex flex-col relative py-1">
      {/* Vertical connecting line behind the step circles */}
      <div
        className="absolute w-px bg-[var(--color-border)]"
        style={{ left: "1.625rem", top: "0.875rem", bottom: "0.875rem" }}
      />
      {steps.map((step, i) => (
        <StepRow
          key={i}
          index={i}
          title={step.title}
          Icon={step.Icon}
          active={step.active}
          done={step.done}
          onClick={() => onStepClick(i)}
        />
      ))}
    </nav>
  );
}

function StepRow({ index, active, done, title, Icon, onClick }: {
  index: number;
  active: boolean;
  done: boolean;
  title: string;
  Icon: LucideIcon;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex items-center gap-3 px-3 py-2.5 rounded-md text-sm text-left transition-colors relative",
        active && "bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium",
        !active && "text-[var(--color-muted-foreground)] hover:bg-[var(--color-muted)]",
      )}
    >
      <span
        className={cn(
          "inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-semibold shrink-0 relative z-10",
          active && "bg-blue-600 text-white",
          !active && done && "bg-[var(--color-success)]/15 text-[var(--color-success)]",
          !active && !done && "bg-[var(--color-muted)] text-[var(--color-muted-foreground)]",
        )}
      >
        {done ? <CheckCircle2 className="h-4 w-4" /> : index + 1}
      </span>
      <Icon className="h-4 w-4 shrink-0 opacity-80" />
      <span className="flex-1">{title}</span>
    </button>
  );
}
