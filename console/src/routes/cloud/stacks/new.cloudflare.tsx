import { ProviderIntro } from "@/components/cloud/ProviderIntro";
import { WizardStepper, type WizardStep } from "@/components/cloud/WizardStepper";
import { createFileRoute, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, ArrowRight, Loader2,
  Folder, KeyRound, Network, Globe, ClipboardCheck, Database, Cog, ShieldCheck,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { qk } from "@/lib/query";
import { cn } from "@/lib/utils";

function iconForGroup(g: { title: string; icon?: string }): LucideIcon {
  const key = (g.icon || g.title || "").toLowerCase();
  if (/cred|key|secret|auth/.test(key)) return KeyRound;
  if (/zone|globe|dns/.test(key)) return Globe;
  if (/network|record/.test(key)) return Network;
  if (/r2|bucket|storage|database/.test(key)) return Database;
  if (/worker|cog/.test(key)) return Cog;
  if (/access|zero|shield/.test(key)) return ShieldCheck;
  if (/review|save|summary/.test(key)) return ClipboardCheck;
  return Folder;
}

type FieldType = "string" | "secret" | "number" | "bool" | "select" | "json";

type Field = {
  name: string;
  label: string;
  type: FieldType;
  default?: any;
  required?: boolean;
  help?: string;
  options?: string[];
  visible_when?: Record<string, any>;
};

function isFieldVisible(f: Field, values: Values): boolean {
  if (!f.visible_when) return true;
  for (const [k, expected] of Object.entries(f.visible_when)) {
    const actual = values[k];
    if (Array.isArray(expected)) {
      if (!expected.includes(actual)) return false;
    } else if (actual !== expected) {
      return false;
    }
  }
  return true;
}

type Group = { title: string; icon?: string; secret?: boolean; fields: Field[] };
type Schema = { groups: Group[] };
type Values = Record<string, any>;
type Search = { edit?: string };

export const Route = createFileRoute("/cloud/stacks/new/cloudflare")({
  component: CloudflareWizard,
  validateSearch: (s: Record<string, unknown>): Search => ({
    edit: typeof s.edit === "string" ? s.edit : undefined,
  }),
});

function initDefaults(schema: Schema): Values {
  const v: Values = {};
  schema.groups.forEach(g => g.fields.forEach(f => {
    if (f.default !== undefined) v[f.name] = JSON.parse(JSON.stringify(f.default));
  }));
  return v;
}

function sanitizePart(s: string): string {
  return String(s || "").toLowerCase().trim().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24);
}

function CloudflareWizard() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { edit } = useSearch({ from: Route.id });
  const [values, setValues] = useState<Values>({});
  const [stackName, setStackName] = useState("");
  const [stackNameAuto, setStackNameAuto] = useState(true);
  const [step, setStep] = useState(0);
  const isEdit = !!edit;

  const { data: schema, error: schemaError } = useQuery({
    queryKey: qk.stackSchema("cloudflare"),
    queryFn: () => api<Schema>("GET", "/api/cloud/cloudflare/schema"),
  });

  useEffect(() => {
    if (schemaError) {
      const e = schemaError as any;
      toast.error("Failed to load schema: " + (e?.status ? `[${e.status}] ` : "") + (e?.message || "(no backend response)"));
    }
  }, [schemaError]);

  const { data: editData, error: editError } = useQuery({
    queryKey: qk.stack(edit || ""),
    queryFn: () => api<{ name: string; terraform_tfvars?: string }>("GET", `/api/cloud/stacks/${encodeURIComponent(edit!)}`),
    enabled: !!edit && !!schema,
  });

  useEffect(() => {
    if (!schema) return;
    if (edit) {
      if (!editData) return;
      const v = parseTfvarsToValues(editData.terraform_tfvars || "", schema);
      v.__name = editData.name;
      setValues(v);
      setStackName(editData.name);
      setStackNameAuto(false);
    } else {
      setValues(initDefaults(schema));
    }
  }, [schema, editData, edit]);

  useEffect(() => {
    if (editError) {
      const e = editError as any;
      toast.error("Load stack failed: " + (e?.status ? `[${e.status}] ` : "") + (e?.message || ""));
    }
  }, [editError]);

  useEffect(() => {
    if (!stackNameAuto || isEdit) return;
    const prefix = sanitizePart(values.project_name || "");
    const env = sanitizePart(values.env || "");
    if (!prefix || !env) return;
    const derived = `${prefix}-${env}`.replace(/-+/g, "-").slice(0, 50);
    setStackName(derived);
  }, [values.project_name, values.env, stackNameAuto, isEdit]);

  function setField(name: string, v: any) {
    setValues(prev => ({ ...prev, [name]: v }));
  }

  function onNext() {
    if (!schema) return;
    if (step === 0 && !isEdit) {
      if (!/^[a-z0-9][a-z0-9_-]{1,48}[a-z0-9]$/.test(stackName)) {
        toast.error("Stack name must be 3-50 chars, lowercase letters/digits/-/_");
        return;
      }
    }
    if (step < schema.groups.length) {
      setStep(s => s + 1);
      return;
    }
    save();
  }

  const saveMut = useMutation({
    mutationFn: async () => {
      const payload: Values = { ...values };
      delete payload.__name;
      // Ensure list defaults are arrays even if user cleared the JSON textareas.
      ["dns_records", "r2_buckets", "workers", "worker_routes", "access_apps"].forEach(k => {
        if (payload[k] == null || payload[k] === "") payload[k] = [];
      });
      if (isEdit && edit) {
        return api("PUT", `/api/cloud/stacks/${encodeURIComponent(edit)}`, { values: payload });
      }
      return api("POST", "/api/cloud/stacks", { provider: "cloudflare", name: stackName, values: payload });
    },
    onSuccess: () => {
      const finalName = isEdit ? edit! : stackName;
      toast.success(`Stack '${finalName}' saved`);
      queryClient.invalidateQueries({ queryKey: qk.stacks });
      if (isEdit && edit) queryClient.invalidateQueries({ queryKey: qk.stack(edit) });
      navigate({ to: "/cloud/stacks/$stackId", params: { stackId: finalName } });
    },
    onError: (e: any) => {
      const code = e?.status ? `[${e.status}] ` : "";
      const detail = e?.body && typeof e.body === "object" ? (e.body.error || e.body.message) : null;
      toast.error("Save failed: " + code + (detail || e?.message || String(e)));
    },
  });

  function save() {
    if (!schema) return;
    if (!isEdit && !/^[a-z0-9][a-z0-9_-]{1,48}[a-z0-9]$/.test(stackName)) {
      toast.error("Stack name must be 3-50 chars, lowercase letters/digits/-/_");
      return;
    }
    saveMut.mutate();
  }
  const saving = saveMut.isPending;

  if (!schema) {
    return (
      <div className="space-y-6">
        <Breadcrumbs items={[{ label: isEdit ? `Edit · ${edit}` : "New Cloudflare Stack" }]} />
        <Card><CardContent className="p-8 text-center"><Loader2 className="h-6 w-6 animate-spin mx-auto" /></CardContent></Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: isEdit ? `Edit · ${edit}` : "New Cloudflare Stack" }]} />
      <div>
        <h1 className="text-2xl font-bold">{isEdit ? "Edit Stack" : "New Cloudflare Stack"}</h1>
        <p className="text-sm text-[var(--color-muted-foreground)] mt-1">
          Mirrors <code className="text-xs">IaC/opentofu-cloudflare/envs/_template/variables.tf</code>. Saving creates <code className="text-xs">envs/&lt;name&gt;/</code>.
        </p>
      </div>
      {!isEdit && <ProviderIntro providerId="cloudflare" />}

      <div className="grid grid-cols-1 md:grid-cols-[260px_1fr] gap-6 items-start">
        <Card className="p-2 md:sticky md:top-20">
          <WizardStepper
            steps={[
              ...schema.groups.map<WizardStep>((g, i) => ({
                title: g.title,
                Icon: iconForGroup(g),
                active: step === i,
                done: step > i,
              })),
              {
                title: "Review & Save",
                Icon: ClipboardCheck,
                active: step === schema.groups.length,
                done: false,
              },
            ]}
            onStepClick={(i) => setStep(i)}
          />
        </Card>

        <Card>
          <CardContent className="p-6 space-y-4">
            {step < schema.groups.length ? (
              <StepPanel
                group={schema.groups[step]!}
                Icon={iconForGroup(schema.groups[step]!)}
                values={values}
                setField={setField}
                showStackName={step === 0}
                isEdit={isEdit}
                stackName={stackName}
                setStackName={(s) => { setStackName(s); setStackNameAuto(false); }}
              />
            ) : (
              <ReviewPanel values={values} stackName={isEdit ? (edit || "") : stackName} />
            )}

            <div className="flex justify-between pt-4 border-t border-[var(--color-border)]">
              <Button variant="outline" disabled={step === 0} onClick={() => setStep(s => Math.max(0, s - 1))}>
                <ArrowLeft className="h-4 w-4" /> Back
              </Button>
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => navigate({ to: "/cloud/stacks" })}>Cancel</Button>
                <Button onClick={onNext} disabled={saving}>
                  {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  {step >= schema.groups.length ? "Save Stack" : "Next"}
                  {!saving && <ArrowRight className="h-4 w-4" />}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function StepPanel({
  group, Icon, values, setField, showStackName, isEdit, stackName, setStackName,
}: {
  group: Group; Icon: LucideIcon; values: Values; setField: (k: string, v: any) => void;
  showStackName: boolean; isEdit: boolean; stackName: string; setStackName: (s: string) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        <span className="inline-flex items-center justify-center w-10 h-10 rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400 shrink-0">
          <Icon className="h-5 w-5" />
        </span>
        <div>
          <h2 className="text-lg font-semibold">{group.title}</h2>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {group.secret
              ? "Stored encrypted on the server, never written to the repo as plaintext."
              : "Maps directly to variables in terraform.tfvars."}
          </p>
        </div>
      </div>

      {showStackName && (
        <FieldWrap label="Stack Name" required help="Folder name under envs/. Lowercase letters, digits, '-' or '_'.">
          <Input
            value={stackName}
            onChange={(e) => setStackName(e.target.value)}
            placeholder="auto: <project_name>-<env>"
            disabled={isEdit}
          />
        </FieldWrap>
      )}

      {group.fields.filter(f => isFieldVisible(f, values)).map(f => (
        <FieldRenderer key={f.name} field={f} value={values[f.name]} setValue={(v) => setField(f.name, v)} />
      ))}
    </div>
  );
}

function FieldWrap({ label, required, help, children }: { label: string; required?: boolean; help?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="text-sm font-medium">
        {label}{required && <span className="text-[var(--color-destructive)] ml-1">*</span>}
      </label>
      {children}
      {help && <p className="text-xs text-[var(--color-muted-foreground)]">{help}</p>}
    </div>
  );
}

function FieldRenderer({ field, value, setValue }: {
  field: Field; value: any; setValue: (v: any) => void;
}) {
  const f = field;
  switch (f.type) {
    case "bool":
      return (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!value} onChange={(e) => setValue(e.target.checked)} className="h-4 w-4" />
          <span className="font-medium">{f.label}{f.required && <span className="text-[var(--color-destructive)] ml-1">*</span>}</span>
          {f.help && <span className="text-xs text-[var(--color-muted-foreground)]">— {f.help}</span>}
        </label>
      );
    case "number":
      return (
        <FieldWrap label={f.label} required={f.required} help={f.help}>
          <Input type="number" value={value ?? ""} onChange={(e) => setValue(e.target.value === "" ? null : Number(e.target.value))} />
        </FieldWrap>
      );
    case "secret":
      return (
        <FieldWrap label={f.label} required={f.required} help={f.help}>
          <Input type="password" value={value ?? ""} autoComplete="new-password" onChange={(e) => setValue(e.target.value)} />
        </FieldWrap>
      );
    case "select":
      return (
        <FieldWrap label={f.label} required={f.required} help={f.help}>
          <select
            className="w-full h-9 rounded-md border border-[var(--color-border)] bg-transparent px-3 text-sm"
            value={value ?? f.default ?? ""}
            onChange={(e) => setValue(e.target.value)}
          >
            {(f.options || []).map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        </FieldWrap>
      );
    case "json":
      return (
        <FieldWrap label={f.label} required={f.required} help={f.help}>
          <JsonField value={value} setValue={setValue} />
        </FieldWrap>
      );
    case "string":
    default:
      return (
        <FieldWrap label={f.label} required={f.required} help={f.help}>
          <Input value={value ?? ""} onChange={(e) => setValue(e.target.value)} />
        </FieldWrap>
      );
  }
}

function JsonField({ value, setValue }: { value: any; setValue: (v: any) => void }) {
  const initial = useMemo(() => (value == null ? "" : JSON.stringify(value, null, 2)), []);
  const [text, setText] = useState(initial);
  const [bad, setBad] = useState(false);
  return (
    <textarea
      rows={8}
      value={text}
      onChange={(e) => {
        const t = e.target.value; setText(t);
        if (!t.trim()) { setBad(false); setValue([]); return; }
        try { const parsed = JSON.parse(t); setBad(false); setValue(parsed); }
        catch { setBad(true); }
      }}
      className={cn(
        "w-full font-mono text-xs p-3 rounded-md border bg-[var(--color-background)]",
        bad ? "border-[var(--color-destructive)]" : "border-[var(--color-border)]"
      )}
    />
  );
}

function ReviewPanel({ values, stackName }: { values: Values; stackName: string }) {
  const preview = { ...values };
  delete (preview as any).__name;
  ["api_token"].forEach(k => {
    if ((preview as any)[k]) (preview as any)[k] = "***";
  });
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">Review & Save</h2>
      <p className="text-sm text-[var(--color-muted-foreground)]">
        Stack <strong>{stackName || "(unnamed)"}</strong> — files will be written to{" "}
        <code className="text-xs">IaC/opentofu-cloudflare/envs/{stackName}/</code>.
      </p>
      <pre className="rounded-md bg-[var(--color-muted)] font-mono text-xs p-4 overflow-auto max-h-[500px]">
        {JSON.stringify(preview, null, 2)}
      </pre>
    </div>
  );
}

// ---- tfvars parser (best-effort) ----
function parseTfvarsToValues(text: string, schema: Schema): Values {
  const values: Values = {};
  if (!text) return values;
  const cleaned = text.split("\n").filter(l => !/^\s*#/.test(l)).join("\n") + "\n__END__ =";
  const re = /^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([\s\S]*?)(?=^[a-zA-Z_][a-zA-Z0-9_]*\s*=)/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(cleaned)) !== null) {
    const key = m[1]; if (!key || key === "__END__") continue;
    const raw = (m[2] ?? "").trim().replace(/\s+$/, "");
    values[key] = parseHclLiteral(raw);
  }
  schema.groups.forEach(g => g.fields.forEach(f => {
    if (values[f.name] === undefined) return;
    if (f.type === "number") values[f.name] = Number(values[f.name]);
    if (f.type === "bool") values[f.name] = values[f.name] === true || values[f.name] === "true";
    if (f.type === "json") values[f.name] = normalizeJsonField(values[f.name]);
  }));
  return values;
}

// Legacy stacks saved before the parser fix stored HCL-object elements as
// escaped strings (e.g. `"{\n content = \"...\"\n }"`). Recursively re-parse
// any such strings back into objects so the editor shows real JSON.
function normalizeJsonField(v: any): any {
  if (Array.isArray(v)) return v.map(normalizeJsonField);
  if (v && typeof v === "object") {
    const out: Record<string, any> = {};
    for (const [k, val] of Object.entries(v)) out[k] = normalizeJsonField(val);
    return out;
  }
  if (typeof v === "string") {
    const t = v.trim();
    if ((t.startsWith("{") && t.endsWith("}")) || (t.startsWith("[") && t.endsWith("]"))) {
      try {
        const parsed = parseHclLiteral(t);
        if (parsed && typeof parsed === "object") return normalizeJsonField(parsed);
      } catch { /* keep original string */ }
    }
  }
  return v;
}
function parseHclLiteral(s: string): any {
  s = s.trim();
  if (s === "true") return true;
  if (s === "false") return false;
  if (/^-?\d+(\.\d+)?$/.test(s)) return Number(s);
  if (s.startsWith('"') && s.endsWith('"')) return s.slice(1, -1).replace(/\\"/g, '"');
  if (s.startsWith("[")) {
    const inner = s.slice(1, s.lastIndexOf("]"));
    // Try JSON first for arrays of primitives / objects.
    try { return JSON.parse("[" + inner + "]"); } catch { /* fallthrough */ }
    // Balanced split on commas at depth 0 (handles arrays of HCL objects).
    const parts: string[] = [];
    let depth = 0, inStr = false, start = 0;
    for (let i = 0; i < inner.length; i++) {
      const c = inner[i]!;
      if (inStr) { if (c === '"' && inner[i - 1] !== "\\") inStr = false; continue; }
      if (c === '"') inStr = true;
      else if (c === "{" || c === "[") depth++;
      else if (c === "}" || c === "]") depth--;
      else if (c === "," && depth === 0) { parts.push(inner.slice(start, i)); start = i + 1; }
    }
    parts.push(inner.slice(start));
    return parts.map(p => p.trim()).filter(Boolean).map(parseHclLiteral);
  }
  if (s.startsWith("{")) {
    const body = s.slice(1, s.lastIndexOf("}"));
    const out: Record<string, any> = {};
    let i = 0;
    const n = body.length;
    const skipWs = () => { while (i < n && /[\s,]/.test(body[i]!)) i++; };
    while (i < n) {
      skipWs();
      if (i >= n) break;
      const keyMatch = /^([a-zA-Z_][\w-]*)\s*=\s*/.exec(body.slice(i));
      if (!keyMatch) { i++; continue; }
      const key = keyMatch[1]!;
      i += keyMatch[0].length;
      let value: any;
      if (body[i] === "{" || body[i] === "[") {
        const open = body[i]!;
        const close = open === "{" ? "}" : "]";
        let depth = 0, start = i, inStr = false;
        for (; i < n; i++) {
          const c = body[i]!;
          if (inStr) { if (c === '"' && body[i - 1] !== "\\") inStr = false; continue; }
          if (c === '"') inStr = true;
          else if (c === open) depth++;
          else if (c === close) { depth--; if (depth === 0) { i++; break; } }
        }
        value = parseHclLiteral(body.slice(start, i));
      } else if (body[i] === '"') {
        let start = i++;
        for (; i < n; i++) { if (body[i] === '"' && body[i - 1] !== "\\") { i++; break; } }
        value = parseHclLiteral(body.slice(start, i));
      } else {
        let start = i;
        while (i < n && body[i] !== "\n" && body[i] !== ",") i++;
        value = parseHclLiteral(body.slice(start, i).trim());
      }
      out[key] = value;
    }
    return out;
  }
  return s;
}
