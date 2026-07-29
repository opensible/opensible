import { useQuery } from "@tanstack/react-query";
import { Cpu } from "lucide-react";
import { api } from "@/lib/api";
import { Select } from "@/components/ui/select";

type Worker = {
  id: string;
  name?: string;
  enabled?: boolean;
  tags?: string[];
  lastSeenAt?: number;
};

type WorkersResp = { success?: boolean; workers?: Worker[] };

const HEARTBEAT_TTL = 60;

/**
 * Dropdown to pick which worker should run a job.
 * value === "" means "any available worker" (default, preserves existing behavior).
 */
export function WorkerSelect({
  value,
  onChange,
  className,
  label = "Worker",
  disabled,
  side = "bottom",
}: {
  value: string;
  onChange: (workerId: string) => void;
  className?: string;
  label?: string;
  disabled?: boolean;
  side?: "top" | "bottom";
}) {
  const q = useQuery({
    queryKey: ["workers-for-select"],
    queryFn: () => api<WorkersResp>("GET", "/api/admin/workers"),
    refetchInterval: 15000,
    retry: false,
  });

  const workers = q.data?.workers ?? [];
  const now = Date.now() / 1000;

  const options = [
    { value: "", label: "Any available worker", description: "Auto-assign to the first free worker" },
    ...workers.map((w) => {
      const online = w.lastSeenAt != null && now - w.lastSeenAt <= HEARTBEAT_TTL;
      const status = !w.enabled ? "disabled" : online ? "online" : "offline";
      return {
        value: w.id,
        label: `${w.name || w.id.slice(0, 8)} — ${status}`,
        description: w.tags && w.tags.length ? w.tags.join(", ") : undefined,
        disabled: !w.enabled,
      };
    }),
  ];

  return (
    <div className={`inline-flex items-center gap-2 ${className ?? ""}`}>
      <span className="text-xs text-[var(--color-muted-foreground)] whitespace-nowrap">{label}:</span>
      <div className="w-[240px]">
        <Select
          value={value}
          onChange={(v) => onChange(v)}
          disabled={disabled}
          placeholder="Any available worker"
          prefix={<Cpu className="h-3.5 w-3.5 text-[var(--color-foreground)] shrink-0" />}
          options={options}
          triggerClassName="h-9 rounded-md text-sm"
          panelClassName="rounded-md"
          align="start"
          side={side}
        />
      </div>
    </div>
  );
}
