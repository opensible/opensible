import { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

type Props = {
  open: boolean;
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "default" | "destructive";
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

export function ConfirmDialog({
  open, title, description, confirmLabel = "Confirm", cancelLabel = "Cancel",
  variant = "default", busy, onConfirm, onCancel,
}: Props) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 animate-in fade-in"
      onClick={() => !busy && onCancel()}
    >
      <div
        className="bg-[var(--color-card)] rounded-lg shadow-2xl w-full max-w-md border border-[var(--color-border)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-5 flex gap-3">
          <div
            className={
              variant === "destructive"
                ? "h-10 w-10 rounded-full bg-[var(--color-destructive)]/10 flex items-center justify-center shrink-0"
                : "h-10 w-10 rounded-full bg-[var(--color-warning)]/10 flex items-center justify-center shrink-0"
            }
          >
            <AlertTriangle
              className={
                variant === "destructive"
                  ? "h-5 w-5 text-[var(--color-destructive)]"
                  : "h-5 w-5 text-[var(--color-warning)]"
              }
            />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold text-[var(--color-foreground)]">{title}</h2>
            {description && (
              <div className="mt-1 text-sm text-[var(--color-muted-foreground)]">{description}</div>
            )}
          </div>
        </div>
        <div className="p-4 border-t border-[var(--color-border)] flex justify-end gap-2">
          <Button variant="outline" onClick={onCancel} disabled={busy}>{cancelLabel}</Button>
          <Button
            variant={variant === "destructive" ? "destructive" : "default"}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "Working…" : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
