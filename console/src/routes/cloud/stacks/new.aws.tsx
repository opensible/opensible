import { ProviderIntro } from "@/components/cloud/ProviderIntro";
import { WizardStepper, type WizardStep } from "@/components/cloud/WizardStepper";
import { createFileRoute, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, ArrowRight, Loader2, Plus, Trash2,
  Folder, KeyRound, Network, Cpu, Layers, Globe, PlusSquare, ClipboardCheck,
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
  if (/net|vpc|subnet/.test(key)) return Network;
  if (/compute|cpu|flavor|ecs/.test(key)) return Cpu;
  if (/platform|pool|role/.test(key)) return Layers;
  if (/edge|elb|nat|dns|gateway|globe/.test(key)) return Globe;
  if (/extra|vm|additional/.test(key)) return PlusSquare;
  if (/review|save|summary/.test(key)) return ClipboardCheck;
  return Folder;
}


type FieldType =
  | "string" | "secret" | "number" | "bool" | "multiselect" | "select"
  | "role_counts" | "extra_vms" | "hidden_map" | "json";


type Field = {
  name: string;
  label: string;
  type: FieldType;
  default?: any;
  required?: boolean;
  help?: string;
  min?: number;
  max?: number;
  options?: string[];
  source?: string; // multiselect source
  roles?: string[];
  subnet_field?: string;
  subnet_options?: string[];
  flavor_field?: string;
  flavor_key?: string;
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

export const Route = createFileRoute("/cloud/stacks/new/aws")({
  component: AwsWizard,
  validateSearch: (s: Record<string, unknown>): Search => ({ edit: typeof s.edit === "string" ? s.edit : undefined }),
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

function AwsWizard() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { edit } = useSearch({ from: Route.id });
  const [values, setValues] = useState<Values>({});
  const [stackName, setStackName] = useState("");
  const [stackNameAuto, setStackNameAuto] = useState(true);
  const [step, setStep] = useState(0);
  const isEdit = !!edit;

  const { data: schema, error: schemaError } = useQuery({
    queryKey: qk.stackSchema("aws"),
    queryFn: () => api<Schema>("GET", "/api/cloud/aws/schema"),
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

  // When schema loads (and optionally edit data), hydrate values once
  useEffect(() => {
    if (!schema) return;
    if (edit) {
      if (!editData) return;
      const v = parseTfvarsToValues(editData.terraform_tfvars || "", schema);
      v.__name = editData.name;
      // (AWS wizard has no legacy reuse toggles to back-fill.)
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

  // Auto-derive stack name when project_name/env/name_prefix change
  useEffect(() => {
    if (!stackNameAuto || isEdit) return;
    const prefix = sanitizePart(values.name_prefix || values.project_name || "");
    const env = sanitizePart(values.env || "");
    if (!prefix || !env) return;
    const derived = `${prefix}-${env}`.replace(/-+/g, "-").slice(0, 50);
    setStackName(derived);
  }, [values.name_prefix, values.project_name, values.env, stackNameAuto, isEdit]);

  const totalSteps = (schema?.groups.length ?? 0) + 1; // +1 for review

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
      // Empty/null number fields should not silently inherit the .tf default (2).
      // If the user left "App VM count" blank we mean 0.
      if (payload.app_vm_count === null || payload.app_vm_count === undefined || payload.app_vm_count === "") {
        payload.app_vm_count = 0;
      }
      if (isEdit && edit) {
        return api("PUT", `/api/cloud/stacks/${encodeURIComponent(edit)}`, { values: payload });
      }
      return api("POST", "/api/cloud/stacks", { provider: "aws", name: stackName, values: payload });
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
        <Breadcrumbs items={[{ label: isEdit ? `Edit · ${edit}` : "New AWS Cloud Stack" }]} />
        <Card><CardContent className="p-8 text-center"><Loader2 className="h-6 w-6 animate-spin mx-auto" /></CardContent></Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: isEdit ? `Edit · ${edit}` : "New AWS Cloud Stack" }]} />
      <div>
        <h1 className="text-2xl font-bold">{isEdit ? "Edit Stack" : "New AWS Cloud Stack"}</h1>
        <p className="text-sm text-[var(--color-muted-foreground)] mt-1">
          Mirrors <code className="text-xs">IaC/opentofu-aws/envs/_template/variables.tf</code>. Saving creates <code className="text-xs">envs/&lt;name&gt;/</code>.
        </p>
      </div>
      {!isEdit && <ProviderIntro providerId="aws" />}

      {/* Two-column wizard: rail of steps + form card */}
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
        <FieldWrap label="Stack Name" required help="Folder name under envs/. Lowercase letters, digits, '-' or '_'. Different from 'Stack Project Name' (Naming Prefix), which controls VM/VPC/ELB resource names.">
          <Input
            value={stackName}
            onChange={(e) => setStackName(e.target.value)}
            placeholder="auto: <project_name>-<env>"
            disabled={isEdit}
          />
        </FieldWrap>
      )}

      {group.fields.filter(f => f.type !== "hidden_map" && isFieldVisible(f, values)).map(f => (
        <FieldRenderer key={f.name} field={f} value={values[f.name]} setValue={(v) => setField(f.name, v)} allValues={values} setOther={setField} />
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

function FieldRenderer({ field, value, setValue, allValues, setOther }: {
  field: Field; value: any; setValue: (v: any) => void;
  allValues: Values; setOther: (k: string, v: any) => void;
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
          <Input type="number" value={value ?? ""} min={f.min} max={f.max} onChange={(e) => setValue(e.target.value === "" ? null : Number(e.target.value))} />
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

    case "multiselect": {
      let opts = f.options || [];
      if (f.source && allValues[f.source] && typeof allValues[f.source] === "object") {
        opts = Object.keys(allValues[f.source]);
      }
      const arr: string[] = Array.isArray(value) ? value : [];
      return (
        <FieldWrap label={f.label} required={f.required} help={f.help}>
          <div className="flex flex-wrap gap-2">
            {opts.map(o => {
              const checked = arr.includes(o);
              return (
                <label key={o} className={cn(
                  "px-3 py-1 rounded-md border text-xs cursor-pointer",
                  checked ? "bg-[var(--color-primary)]/10 border-[var(--color-primary)] text-[var(--color-primary)]" : "border-[var(--color-border)]"
                )}>
                  <input
                    type="checkbox"
                    className="hidden"
                    checked={checked}
                    onChange={() => {
                      const next = checked ? arr.filter(x => x !== o) : [...arr, o];
                      setValue(next);
                    }}
                  />{o}
                </label>
              );
            })}
            {opts.length === 0 && <p className="text-xs text-[var(--color-muted-foreground)]">No options yet.</p>}
          </div>
        </FieldWrap>
      );
    }
    case "role_counts": {
      const map: Record<string, number> = (value && typeof value === "object") ? value : {};
      const subnetField = f.subnet_field || "";
      const subnetOpts = f.subnet_options || [];
      const subnetMap: Record<string, string> = (subnetField && allValues[subnetField]) || {};
      const flavorField = f.flavor_field || "";
      const flavorKey = f.flavor_key || "server_type";
      const flavorMap: Record<string, Record<string, string>> = (flavorField && allValues[flavorField]) || {};
      const names = Object.keys(map).length ? Object.keys(map) : (f.roles || []);

      const commit = (newNames: string[], newMap: Record<string, number>, newSub: Record<string, string>, newFlv: Record<string, Record<string, string>>) => {
        const cleanMap: Record<string, number> = {};
        const cleanSub: Record<string, string> = {};
        const cleanFlv: Record<string, Record<string, string>> = {};
        const seen = new Set<string>();
        newNames.forEach(n => {
          const k = n.trim().toLowerCase().replace(/[^a-z0-9_-]/g, "-");
          if (!k || seen.has(k)) return;
          seen.add(k);
          cleanMap[k] = newMap[n] ?? 1;
          if (subnetField) cleanSub[k] = newSub[n] || subnetOpts[0] || "data";
          if (flavorField && newFlv[n]?.[flavorKey]) cleanFlv[k] = { [flavorKey]: newFlv[n][flavorKey] };
        });
        setValue(cleanMap);
        if (subnetField) setOther(subnetField, cleanSub);
        if (flavorField) setOther(flavorField, cleanFlv);
      };

      return (
        <FieldWrap label={f.label} required={f.required} help={f.help}>
          <div className="space-y-2">
            {names.map((n, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <Input
                  className="flex-1"
                  value={n}
                  placeholder="role-name"
                  onChange={(e) => {
                    const next = [...names]; next[idx] = e.target.value;
                    const newMap = { ...map, [e.target.value]: map[n] ?? 1 }; delete newMap[n];
                    const newSub = { ...subnetMap, [e.target.value]: subnetMap[n] }; delete newSub[n];
                    const newFlv = { ...flavorMap, [e.target.value]: flavorMap[n] }; delete newFlv[n];
                    commit(next, newMap, newSub as any, newFlv as any);
                  }}
                />
                <Input
                  type="number" min={0} max={50} className="w-20"
                  value={map[n] ?? 1}
                  onChange={(e) => commit(names, { ...map, [n]: Number(e.target.value) || 0 }, subnetMap as any, flavorMap as any)}
                />
                {subnetField && (
                  <select
                    className="h-9 px-2 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] text-sm"
                    value={subnetMap[n] || subnetOpts[0] || ""}
                    onChange={(e) => commit(names, map, { ...subnetMap, [n]: e.target.value } as any, flavorMap as any)}
                  >
                    {subnetOpts.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                )}
                {flavorField && (
                  <Input
                    className="w-40" placeholder={allValues[flavorKey] || flavorKey}
                    value={flavorMap[n]?.[flavorKey] || ""}
                    onChange={(e) => commit(names, map, subnetMap as any, { ...flavorMap, [n]: { [flavorKey]: e.target.value } } as any)}
                  />
                )}
                <Button variant="ghost" size="sm" onClick={() => {
                  const next = names.filter(x => x !== n);
                  const nm = { ...map }; delete nm[n];
                  const ns = { ...subnetMap }; delete ns[n];
                  const nf = { ...flavorMap }; delete nf[n];
                  commit(next, nm, ns as any, nf as any);
                }}><Trash2 className="h-4 w-4" /></Button>
              </div>
            ))}
            <Button variant="outline" size="sm" onClick={() => {
              const newName = `role-${names.length + 1}`;
              commit([...names, newName], { ...map, [newName]: 1 }, subnetMap as any, flavorMap as any);
            }}><Plus className="h-4 w-4" /> Add role</Button>
          </div>
        </FieldWrap>
      );
    }
    case "extra_vms":
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
      rows={6}
      value={text}
      onChange={(e) => {
        const t = e.target.value; setText(t);
        if (!t.trim()) { setBad(false); setValue({}); return; }
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
  ["aws_access_key", "aws_secret_key", "admin_password", "rds_password"].forEach(k => {
    if ((preview as any)[k]) (preview as any)[k] = "***";
  });
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">Review & Save</h2>
      <p className="text-sm text-[var(--color-muted-foreground)]">
        Stack <strong>{stackName || "(unnamed)"}</strong> — files will be written to{" "}
        <code className="text-xs">IaC/opentofu-aws/envs/{stackName}/</code>.
      </p>
      <pre className="rounded-md bg-[var(--color-muted)] font-mono text-xs p-4 overflow-auto max-h-[500px]">
        {JSON.stringify(preview, null, 2)}
      </pre>
    </div>
  );
}

// ---- tfvars parser (best-effort, mirrors old frontend) ----
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
  }));
  return values;
}
function parseHclLiteral(s: string): any {
  s = s.trim();
  if (s === "true") return true;
  if (s === "false") return false;
  if (/^-?\d+(\.\d+)?$/.test(s)) return Number(s);
  if (s.startsWith('"') && s.endsWith('"')) return s.slice(1, -1).replace(/\\"/g, '"');
  if (s.startsWith("[")) {
    const inner = s.slice(1, s.lastIndexOf("]"));
    const items: string[] = [];
    let depth = 0, inStr = false, start = 0;
    for (let i = 0; i < inner.length; i++) {
      const c = inner[i]!;
      if (inStr) { if (c === '"' && inner[i - 1] !== "\\") inStr = false; continue; }
      if (c === '"') { inStr = true; continue; }
      if (c === "{" || c === "[") depth++;
      else if (c === "}" || c === "]") depth--;
      else if (c === "," && depth === 0) { items.push(inner.slice(start, i)); start = i + 1; }
    }
    items.push(inner.slice(start));
    return items.map(x => x.trim()).filter(Boolean).map(x => {
      const v = parseHclLiteral(x);
      return typeof v === "string" ? v.replace(/^"|"$/g, "") : v;
    });
  }
  if (s.startsWith("{")) {
    // Balanced-brace object parser: supports nested { ... } values.
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
      // Read value: object, array, string, or bare token
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
