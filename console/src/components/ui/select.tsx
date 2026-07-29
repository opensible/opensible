/**
 * Lightweight custom Select — styled to match the reference screenshot:
 * - White rounded "trigger" with chevron
 * - Floating panel with soft border + shadow
 * - Selected row uses a soft indigo highlight with a check
 * - Keyboard friendly: Enter/Space to open, Esc/click-outside to close
 */
import { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export type SelectOption = {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
};

type Props = {
  value: string | null | undefined;
  onChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  triggerClassName?: string;
  panelClassName?: string;
  label?: string;
  align?: "start" | "end";
  /** Open the panel above the trigger instead of below. */
  side?: "top" | "bottom";
  /** Render a custom prefix inside the trigger (e.g. an icon). */
  prefix?: React.ReactNode;
};

export function Select({
  value,
  onChange,
  options,
  placeholder = "Select…",
  disabled,
  className,
  triggerClassName,
  panelClassName,
  label,
  align = "start",
  side = "bottom",
  prefix,
}: Props) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const current = options.find(o => o.value === value) ?? null;

  return (
    <div ref={rootRef} className={cn("relative inline-block", className)}>
      {label && <label htmlFor={id} className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1">{label}</label>}
      <button
        id={id}
        type="button"
        disabled={disabled}
        onClick={() => !disabled && setOpen(o => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          "w-full inline-flex items-center justify-between gap-2 h-10 px-3 rounded-xl",
          "bg-[var(--color-card)] border border-[var(--color-border)] text-sm text-[var(--color-foreground)]",
          "shadow-sm hover:bg-[var(--color-muted)]/40 transition-colors",
          "focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]/30 focus:border-[var(--color-primary)]",
          disabled && "opacity-60 cursor-not-allowed",
          triggerClassName,
        )}
      >
        <span className="flex items-center gap-2 min-w-0 truncate">
          {prefix}
          <span className={cn("truncate", !current && "text-[var(--color-muted-foreground)]")}>
            {current?.label ?? placeholder}
          </span>
        </span>
        <ChevronDown className={cn("h-4 w-4 text-[var(--color-muted-foreground)] shrink-0 transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div
          role="listbox"
          className={cn(
            "absolute z-50 min-w-full max-h-72 overflow-auto rounded-xl",
            "bg-[var(--color-card)] border border-[var(--color-border)] shadow-lg p-1.5",
            side === "top" ? "bottom-full mb-2" : "top-full mt-2",
            align === "end" ? "right-0" : "left-0",
            panelClassName,
          )}
        >
          {options.length === 0 && (
            <div className="px-3 py-2 text-sm text-[var(--color-muted-foreground)]">No options</div>
          )}
          {options.map(opt => {
            const selected = opt.value === value;
            return (
              <button
                key={opt.value}
                type="button"
                role="option"
                aria-selected={selected}
                disabled={opt.disabled}
                onClick={() => { if (!opt.disabled) { onChange(opt.value); setOpen(false); } }}
                className={cn(
                  "w-full flex items-center justify-between gap-3 px-3 py-2 rounded-lg text-sm text-left",
                  "transition-colors",
                  selected
                    ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)] font-medium"
                    : "hover:bg-[var(--color-muted)] text-[var(--color-foreground)]",
                  opt.disabled && "opacity-50 cursor-not-allowed",
                )}
              >
                <span className="flex flex-col min-w-0">
                  <span className="truncate">{opt.label}</span>
                  {opt.description && (
                    <span className="truncate text-[11px] text-[var(--color-muted-foreground)]">{opt.description}</span>
                  )}
                </span>
                {selected && <Check className="h-4 w-4 shrink-0" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
