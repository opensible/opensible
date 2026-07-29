import { cn } from "@/lib/utils";
import type { HTMLAttributes } from "react";

const styles = {
  default: "bg-[var(--color-muted)] text-[var(--color-muted-foreground)]",
  success: "bg-[var(--color-success)]/15 text-[var(--color-success)]",
  warning: "bg-[var(--color-warning)]/15 text-[var(--color-warning)]",
  destructive: "bg-[var(--color-destructive)]/15 text-[var(--color-destructive)]",
  primary: "bg-[var(--color-primary)]/15 text-[var(--color-primary)]",
} as const;

export function Badge({
  className,
  variant = "default",
  ...props
}: HTMLAttributes<HTMLSpanElement> & { variant?: keyof typeof styles }) {
  return (
    <span
      className={cn("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium", styles[variant], className)}
      {...props}
    />
  );
}

export function statusToVariant(status?: string): keyof typeof styles {
  switch ((status || "").toLowerCase()) {
    case "succeeded":
    case "success":
    case "ok":
      return "success";
    case "failed":
    case "error":
      return "destructive";
    case "running":
    case "queued":
    case "pending":
      return "warning";
    default:
      return "default";
  }
}
