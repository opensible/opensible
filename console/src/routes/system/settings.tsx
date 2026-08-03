import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  SlidersHorizontal, Info,
  KeyRound, Bug, Upload, History, CheckCircle2, XCircle, Plus, RefreshCw,
  Globe,
} from "lucide-react";
import { toast } from "sonner";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import opensibleLogo from "@/assets/opensible-logo.png";
import { SchedulerIntervalsCard } from "@/components/system/SchedulerIntervalsCard";

export const Route = createFileRoute("/system/settings")({ component: SettingsPage });

const TABS = [
  { id: "general", label: "General", icon: SlidersHorizontal },
  { id: "about", label: "About", icon: Info },
] as const;
type TabId = (typeof TABS)[number]["id"];


function SettingsPage() {
  const t = useT();
  const [tab, setTab] = useState<TabId>(() => {
    const saved = localStorage.getItem("settingsActiveTab") as TabId | null;
    return TABS.some(t => t.id === saved) ? saved! : "general";
  });
  useEffect(() => { localStorage.setItem("settingsActiveTab", tab); }, [tab]);


  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "System" }, { label: "Settings" }]} />
      <div>
        <h1 className="text-2xl font-semibold">{t("page.systemSettings.title")}</h1>
        <p className="text-sm text-[var(--color-muted-foreground)]">{t("page.systemSettings.subtitle")}</p>
      </div>

      <div className="flex gap-1 border-b border-[var(--color-border)]">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "flex items-center gap-2 px-4 py-2 text-sm border-b-2 -mb-px transition-colors",
              tab === t.id
                ? "border-[var(--color-primary)] text-[var(--color-primary)] font-medium"
                : "border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]",
            )}
          >
            <t.icon className="h-4 w-4" /> {t.label}
          </button>
        ))}
      </div>

      {tab === "general" && <GeneralTab />}
      {tab === "about" && <AboutTab />}
    </div>
  );
}

/* ---------- General Tab ---------- */

type ExecSettings = {
  debug_mode?: boolean;
  log_level?: string;
  max_log_size_mb?: number;
  max_upload_size_mb?: number;
};

function GeneralTab() {
  return (
    <div className="space-y-6">
      <Section title="System / Automation" icon="⚙️">
        <DebugLoggingCard />
        <MaxUploadCard />
        <EncryptionKeyCard />
        <ExecutionHistoryCard />
        <SchedulerIntervalsCard />
      </Section>
    </div>
  );
}

function Section({ title, icon, children }: { title: string; icon: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--color-muted-foreground)] mb-3 pl-1">
        {icon} {title}
      </h3>
      <div className="space-y-3">{children}</div>
    </div>
  );
}


function useExecSettings() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["execution-settings"],
    queryFn: () => api<{ success: boolean; settings: ExecSettings }>("GET", "/api/execution_settings"),
  });
  const save = useMutation({
    mutationFn: (body: ExecSettings) => api("POST", "/api/execution_settings", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["execution-settings"] }),
  });
  return { settings: q.data?.settings ?? {}, isLoading: q.isLoading, isFetched: q.isFetched, save };
}

function DebugLoggingCard() {
  const { settings, save } = useExecSettings();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2"><Bug className="h-4 w-4" /> Debug Mode & Logging</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="rounded-md border border-red-500/20 bg-red-500/5 p-3 flex gap-2 text-sm">
          <span className="text-red-500">⚠️</span>
          <div>
            <div className="font-medium">Flask Debug Mode is not recommended for production</div>
            <div className="text-xs text-[var(--color-muted-foreground)] mt-1">
              Changes will take effect after application restart.
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between border-b border-[var(--color-border)] pb-3">
          <div>
            <div className="text-sm font-medium">Flask Debug Mode</div>
            <div className="text-xs text-[var(--color-muted-foreground)]">Detailed logging and interactive debugger.</div>
          </div>
          <label className="inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              checked={!!settings.debug_mode}
              onChange={e => save.mutate({ debug_mode: e.target.checked })}
              className="h-4 w-4 accent-[var(--color-primary)]"
            />
          </label>
        </div>

        <div className="border-b border-[var(--color-border)] pb-3">
          <label className="block text-sm font-medium mb-1">Logging Level</label>
          <p className="text-xs text-[var(--color-muted-foreground)] mb-2">Changes apply immediately without restart.</p>
          <div className="max-w-xs">
            <Select
              value={(settings.log_level || "INFO").toUpperCase()}
              onChange={v => save.mutate({ log_level: v })}
              options={[
                { value: "DEBUG", label: "DEBUG" },
                { value: "INFO", label: "INFO" },
                { value: "WARNING", label: "WARNING" },
                { value: "ERROR", label: "ERROR" },
                { value: "CRITICAL", label: "CRITICAL" },
              ]}
            />
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Application log file size (MB)</label>
          <p className="text-xs text-[var(--color-muted-foreground)] mb-2">
            Rotation size for the backend application/debug log (<code>app.log</code>). Separate from execution/run log rotation. Applies after restart.
          </p>
          <div className="flex items-center gap-2 max-w-xs">
            <Input
              key={`applog-${settings.max_log_size_mb ?? 10}`}
              type="number"
              min={1}
              max={1024}
              defaultValue={settings.max_log_size_mb ?? 10}
              onBlur={e => save.mutate({ max_log_size_mb: Number(e.target.value) })}
              className="w-32"
            />
            <span className="text-sm">MB</span>
            <span className="text-xs text-[var(--color-muted-foreground)]">(1–1024)</span>
          </div>
        </div>

      </CardContent>
    </Card>
  );
}

function MaxUploadCard() {
  const { settings, save } = useExecSettings();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2"><Upload className="h-4 w-4" /> Maximum upload file size</CardTitle>
        <CardDescription>Limits file size when uploading. Applies immediately.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-2">
          <Input
            key={`upload-${settings.max_upload_size_mb ?? 10}`}
            type="number"
            min={1}
            max={1024}
            defaultValue={settings.max_upload_size_mb ?? 10}
            onBlur={e => save.mutate({ max_upload_size_mb: Number(e.target.value) })}
            className="w-32"
          />
          <span className="text-sm">MB</span>
          <span className="text-xs text-[var(--color-muted-foreground)]">(min: 1MB, max: 1024MB)</span>
        </div>
      </CardContent>
    </Card>
  );
}

type EncKey = { exists: boolean; source?: string };

function EncryptionKeyCard() {
  const qc = useQueryClient();
  const [confirmReplace, setConfirmReplace] = useState(false);
  const q = useQuery({
    queryKey: ["encryption-key"],
    queryFn: () => api<{ success: boolean; key: EncKey }>("GET", "/api/global/secrets/encryption-key"),
  });
  const key = q.data?.key;
  const create = useMutation({
    mutationFn: () => api("POST", "/api/global/secrets/encryption-key/create", {}),
    onSuccess: () => { toast.success("Encryption key created"); qc.invalidateQueries({ queryKey: ["encryption-key"] }); },
    onError: (e: Error) => toast.error(e.message),
  });
  const replace = useMutation({
    mutationFn: (newKey: string) => api("POST", "/api/global/secrets/encryption-key", { key: newKey }),
    onSuccess: () => { toast.success("Encryption key replaced"); qc.invalidateQueries({ queryKey: ["encryption-key"] }); setConfirmReplace(false); },
    onError: (e: Error) => toast.error(e.message),
  });

  const generateAndReplace = () => {
    const arr = new Uint8Array(32);
    crypto.getRandomValues(arr);
    const b64 = btoa(String.fromCharCode(...arr)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
    replace.mutate(b64);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2"><KeyRound className="h-4 w-4" /> Global Secrets Encryption Key</CardTitle>
        <CardDescription>The key is auto-generated on first launch. Replacing it makes existing secrets undecryptable.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-muted)]/30 p-3 text-sm">
          {q.isLoading ? "Loading…" : key ? (
            <div className="flex items-center gap-2">
              {key.exists
                ? <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                : <XCircle className="h-4 w-4 text-red-500" />}
              <span className="font-medium">Status: {key.exists ? "Found" : "Not found"}</span>
              <span className="text-[var(--color-muted-foreground)]">·  Source: {key.source === "environment" ? "Environment variable" : key.source === "file" ? "File" : "—"}</span>
            </div>
          ) : <span className="text-red-500">Error loading key information</span>}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => create.mutate()} disabled={create.isPending || key?.exists}>
            <Plus className="h-4 w-4 mr-1" /> Create New
          </Button>
          <Button variant="destructive" onClick={() => setConfirmReplace(true)} disabled={!key?.exists}>
            <RefreshCw className="h-4 w-4 mr-1" /> Replace
          </Button>
        </div>
        <p className="text-xs text-[var(--color-muted-foreground)]">
          Key viewing/downloading disabled: the key is a secret and is never exposed through UI/API.
        </p>
      </CardContent>
      <ConfirmDialog
        open={confirmReplace}
        onCancel={() => setConfirmReplace(false)}
        title="Replace Encryption Key?"
        description="Replacing the encryption key will make ALL existing secrets undecryptable. Delete or recreate them first. This action cannot be undone."
        confirmLabel="Replace Key"
        variant="destructive"
        busy={replace.isPending}
        onConfirm={generateAndReplace}
      />
    </Card>
  );
}

function ExecutionHistoryCard() {
  const { settings, save } = useExecSettings();
  const s = settings as any;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2"><History className="h-4 w-4" /> Execution history</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-sm font-medium">Save execution history</div>
            <div className="text-xs text-[var(--color-muted-foreground)]">Persist run logs and metadata for later review.</div>
          </div>
          <input
            type="checkbox"
            checked={s.save_execution_history !== false}
            onChange={e => save.mutate({ save_execution_history: e.target.checked } as any)}
            className="h-4 w-4 accent-[var(--color-primary)]"
          />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">Retention (days)</label>
            <Input
              key={`ret-${s.execution_retention_days ?? 30}`}
              type="number"
              min={1}
              max={365}
              defaultValue={s.execution_retention_days ?? 30}
              onBlur={e => save.mutate({ execution_retention_days: Number(e.target.value) } as any)}
            />
            <p className="text-xs text-[var(--color-muted-foreground)] mt-1">Delete records older than N days.</p>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Max log files</label>
            <Input
              key={`rc-${s.retention_count ?? 200}`}
              type="number"
              min={1}
              max={10000}
              defaultValue={s.retention_count ?? 200}
              onBlur={e => save.mutate({ retention_count: Number(e.target.value) } as any)}
            />
            <p className="text-xs text-[var(--color-muted-foreground)] mt-1">Keep only the latest N execution log files.</p>
          </div>
        </div>
        <p className="text-xs text-[var(--color-muted-foreground)]">
          Note: rotation size for the backend application log is configured under <span className="font-medium">Debug Mode & Logging → Application log file size</span>.
        </p>
      </CardContent>
    </Card>
  );
}

/* ---------- Backup Tab ---------- */

function BackupTab() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["backup-settings"],
    queryFn: () => api<{ success: boolean; settings: Record<string, any> }>("GET", "/api/backup-settings"),
  });
  const settings = q.data?.settings ?? {};
  const [form, setForm] = useState<Record<string, any> | null>(null);
  const current = form ?? settings;

  const save = useMutation({
    mutationFn: (body: Record<string, any>) => api("PUT", "/api/backup-settings", body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["backup-settings"] }); setForm(null); },
  });

  if (q.isLoading) return <Card><CardContent className="py-12 text-center text-sm text-[var(--color-muted-foreground)]">Loading…</CardContent></Card>;
  if (q.isError) return <Card><CardContent className="py-6 text-sm text-[var(--color-destructive)]">{(q.error as Error).message}</CardContent></Card>;

  const update = (k: string, v: any) => setForm({ ...current, [k]: v });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Backup Settings</CardTitle>
        <CardDescription>Automated backups of platform data and configuration.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label="Enabled">
          <input type="checkbox" checked={!!current.enabled} onChange={e => update("enabled", e.target.checked)} />
        </Field>
        <Field label="Schedule (cron)">
          <Input value={current.schedule ?? ""} onChange={e => update("schedule", e.target.value)} placeholder="0 2 * * *" />
        </Field>
        <Field label="Retention (days)">
          <Input type="number" value={current.retention_days ?? 7} onChange={e => update("retention_days", Number(e.target.value))} />
        </Field>
        <Field label="Destination">
          <Input value={current.destination ?? ""} onChange={e => update("destination", e.target.value)} placeholder="/var/backups or s3://bucket" />
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={() => setForm(null)} disabled={!form}>Discard</Button>
          <Button onClick={() => save.mutate(current)} disabled={!form || save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
        {save.isError && <div className="text-sm text-[var(--color-destructive)]">{(save.error as Error).message}</div>}
      </CardContent>
    </Card>
  );
}


/* ---------- About Tab ---------- */

function AboutTab() {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-center gap-4 mb-5">
          <img
            src={opensibleLogo}
            alt="OpenSible"
            className="h-12 w-12 rounded-xl object-contain bg-[var(--color-background)]"
          />
          <div>
            <div className="text-xs uppercase tracking-wider text-[var(--color-muted-foreground)] font-semibold">About OpenSible</div>
            <h2 className="text-lg font-bold leading-snug">The Modern GitOps Control Plane for OpenTofu and Ansible</h2>
          </div>
        </div>
        <p className="text-sm text-[var(--color-muted-foreground)] leading-relaxed">
          A unified automation platform for cloud provisioning and infrastructure operations. Provision infrastructure with OpenTofu, configure servers with Ansible, manage secrets securely, execute reusable deployment workflows, and automate your entire infrastructure lifecycle through GitOps. From infrastructure provisioning to operations, everything is version-controlled, repeatable, secure, and scalable across cloud, on-premises, and hybrid environments.
        </p>
        <div className="flex items-center gap-3 pt-5 mt-5 border-t border-[var(--color-border)] flex-wrap">
          <span className="text-xs font-semibold text-[var(--color-muted-foreground)]">Follow the project:</span>
          <a
            href="https://www.linkedin.com/showcase/opensible"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md border border-[var(--color-border)] text-sm hover:border-[var(--color-primary)] hover:text-[var(--color-primary)] transition-colors"
          >
            LinkedIn
          </a>
          <a
            href="https://github.com/opensible"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md border border-[var(--color-border)] text-sm hover:border-[var(--color-primary)] hover:text-[var(--color-primary)] transition-colors"
          >
            GitHub
          </a>
          <a
            href="https://www.youtube.com/@opensible"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md border border-[var(--color-border)] text-sm hover:border-[var(--color-primary)] hover:text-[var(--color-primary)] transition-colors"
          >
            YouTube
          </a>
          <a
            href="https://docs.opensible.com"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md border border-[var(--color-border)] text-sm hover:border-[var(--color-primary)] hover:text-[var(--color-primary)] transition-colors"
          >
            Documentation
          </a>
        </div>
        <div className="flex items-center gap-3 pt-3 mt-3 border-t border-[var(--color-border)] flex-wrap">
          <a
            href="https://opensible.com"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-[var(--color-primary)] text-[var(--color-primary-foreground)] text-sm hover:bg-[var(--color-primary)]/90 transition-colors"
          >
            <Globe className="h-4 w-4" /> Website
          </a>
        </div>
      </CardContent>
    </Card>
  );
}

/* ---------- Shared ---------- */

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-3 items-center">
      <label className="text-sm font-medium">{label}</label>
      <div className="col-span-2">{children}</div>
    </div>
  );
}
