import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, X, Download, StopCircle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { LogViewer } from "@/components/cloud/LogViewer";
import { api } from "@/lib/api";
import { toast } from "sonner";

type ExecutionRecord = {
  id?: string;
  execution_id?: string;
  status?: string;
  started_at?: string;
  finished_at?: string;
  exit_code?: number;
  playbook_name?: string;
  play_name?: string;
  type?: string;
  project_id?: string;
};

function statusVariant(s?: string): "default" | "primary" | "success" | "warning" | "destructive" {
  const v = (s || "").toUpperCase();
  if (v === "SUCCESS" || v === "COMPLETED") return "success";
  if (v === "RUNNING" || v === "PENDING" || v === "QUEUED") return "warning";
  if (v === "FAILED" || v === "ERROR" || v === "CANCELED" || v === "CANCELLED") return "destructive";
  return "default";
}

export function RunLogDialog({
  executionId,
  onClose,
  title,
  live = false,
}: {
  executionId: string;
  onClose: () => void;
  title?: string;
  live?: boolean;
}) {
  const [text, setText] = useState("");
  const offsetRef = useRef(0);
  const [isComplete, setIsComplete] = useState(false);
  const [status, setStatus] = useState<string>("RUNNING");
  const [polling, setPolling] = useState(live);
  const timerRef = useRef<number | null>(null);

  const execQ = useQuery({
    queryKey: ["execution", executionId],
    queryFn: () => api<{ success?: boolean; execution?: ExecutionRecord }>(
      "GET", `/api/executions/${encodeURIComponent(executionId)}`
    ),
    refetchInterval: polling && !isComplete ? 5000 : false,
    refetchIntervalInBackground: false,
  });

  const fetchChunk = async () => {
    try {
      const res = await api<{
        success?: boolean; text?: string; nextOffset?: number;
        fileSize?: number; isComplete?: boolean; status?: string;
      }>("GET", `/api/executions/${encodeURIComponent(executionId)}/log?offset=${offsetRef.current}`);
      if (res.text) setText((t) => t + res.text);
      if (typeof res.nextOffset === "number") offsetRef.current = res.nextOffset;
      if (res.status) setStatus(res.status);
      if (res.isComplete) {
        setIsComplete(true);
        setPolling(false);
      }
    } catch (e) {
      // 404 means not yet flushed; keep polling silently
    }
  };

  useEffect(() => {
    fetchChunk();
    if (!polling) return;
    timerRef.current = window.setInterval(() => {
      if (document.hidden) return;
      fetchChunk();
    }, 3000);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [executionId, polling]);

  const cancel = async () => {
    try {
      const pid = execQ.data?.execution?.project_id || "_current";
      await api("POST", `/api/projects/${encodeURIComponent(pid)}/executions/${encodeURIComponent(executionId)}/cancel`, {});
      toast.success("Cancel requested");
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const download = () => {
    const blob = new Blob([text || ""], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${executionId}.log`; a.click();
    URL.revokeObjectURL(url);
  };

  const exec = execQ.data?.execution;
  const effStatus = status || exec?.status || "UNKNOWN";

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-start justify-center overflow-y-auto p-4 sm:p-8" onClick={onClose}>
      <div className="bg-[var(--color-card)] text-[var(--color-card-foreground)] rounded-lg shadow-2xl w-full max-w-5xl border border-[var(--color-border)] flex flex-col max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--color-border)]">
          <div className="flex items-center gap-3 min-w-0">
            {!isComplete && polling && <Loader2 className="h-4 w-4 animate-spin text-[var(--color-primary)]" />}
            <div className="min-w-0">
              <h2 className="text-base font-semibold truncate">
                {title || exec?.play_name || exec?.playbook_name || "Run logs"}
              </h2>
              <div className="text-xs text-[var(--color-muted-foreground)] flex items-center gap-2 mt-0.5">
                <span className="font-mono truncate">{executionId}</span>
                <Badge variant={statusVariant(effStatus)} className="text-[10px]">{effStatus}</Badge>
                {exec?.started_at && <span>· started {new Date(exec.started_at).toLocaleString()}</span>}
                {exec?.finished_at && <span>· finished {new Date(exec.finished_at).toLocaleString()}</span>}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={fetchChunk}>
              <RefreshCw className="h-4 w-4" />
            </Button>
            <Button variant="outline" size="sm" onClick={download} disabled={!text}>
              <Download className="h-4 w-4" />
            </Button>
            {!isComplete && (
              <Button variant="destructive" size="sm" onClick={cancel}>
                <StopCircle className="h-4 w-4 mr-1.5" /> Stop
              </Button>
            )}
            <button onClick={onClose} className="text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] text-2xl leading-none px-1">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>
        <div className="p-3 flex-1 overflow-hidden">
          <LogViewer text={text || "(waiting for output…)"} className="max-h-[70vh]" />
        </div>
      </div>
    </div>
  );
}
