import { useMemo, useState } from "react";
import { X, Save, Play, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { YamlEditor } from "@/components/ui/yaml-editor";
import type { Blueprint, BlueprintGroup, FormSchemaField } from "@/lib/blueprints";
import opensibleLogo from "@/assets/opensible-logo-fvc.png.asset.json";

export type BlueprintFormAction = "create-job" | "run-once";

function toYamlList(items: string[]): string {
  const clean = items.map((s) => (s ?? "").toString().trim()).filter(Boolean);
  if (!clean.length) return "";
  return clean.map((s) => `- ${s}`).join("\n") + "\n";
}

function initialValue(f: FormSchemaField): unknown {
  if (f.default !== undefined) return f.default;
  switch (f.type) {
    case "bool": return false;
    case "number": return 0;
    case "list": return [];
    default: return "";
  }
}

function serialize(f: FormSchemaField, v: unknown): unknown {
  if (f.serialize === "yaml-list" && Array.isArray(v)) return toYamlList(v as string[]);
  return v;
}

export function BlueprintFormDialog({
  blueprint,
  group,
  onClose,
  onSubmit,
}: {
  blueprint: Blueprint;
  group: BlueprintGroup;
  onClose: () => void;
  onSubmit: (action: BlueprintFormAction, values: Record<string, unknown>) => void | Promise<void>;
}) {
  const schema = blueprint.formSchema || [];
  const [busy, setBusy] = useState<null | BlueprintFormAction>(null);
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const seed: Record<string, unknown> = { ...(blueprint.defaults || {}) };
    schema.forEach((f) => {
      if (seed[f.key] === undefined) seed[f.key] = initialValue(f);
    });
    return seed;
  });

  const groups = useMemo(() => {
    const order: string[] = [];
    const byGroup: Record<string, FormSchemaField[]> = {};
    schema.forEach((f) => {
      const g = f.group || "General";
      if (!byGroup[g]) { byGroup[g] = []; order.push(g); }
      byGroup[g]!.push(f);
    });
    return order.map((g) => ({ name: g, fields: byGroup[g] || [] }));
  }, [schema]);

  const setField = (k: string, v: unknown) =>
    setValues((prev) => ({ ...prev, [k]: v }));

  const submit = async (action: BlueprintFormAction) => {
    const out: Record<string, unknown> = {};
    schema.forEach((f) => { out[f.key] = serialize(f, values[f.key]); });
    // Preserve keys already set via `defaults` but not in schema
    Object.keys(values).forEach((k) => {
      if (!(k in out)) out[k] = values[k];
    });
    try {
      setBusy(action);
      await onSubmit(action, out);
    } finally {
      setBusy(null);
    }
  };

  const initialLogo = blueprint.logo || group.logo || opensibleLogo.url;
  const [logoSrc, setLogoSrc] = useState(initialLogo);

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-[var(--color-background)] border border-[var(--color-border)] rounded-lg shadow-xl w-full max-w-3xl max-h-[92vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)] gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="h-9 w-9 rounded-md bg-[var(--color-accent)] flex items-center justify-center shrink-0 overflow-hidden">
              <img src={logoSrc} alt="" className="h-5 w-5 object-contain" onError={() => { if (logoSrc !== opensibleLogo.url) setLogoSrc(opensibleLogo.url); }} />
            </div>
            <div className="min-w-0">
              <div className="font-medium text-sm truncate">{blueprint.name}</div>
              <div className="text-[11px] text-[var(--color-muted-foreground)] truncate">
                {group.name} · schema-driven
              </div>
            </div>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-[var(--color-accent)] rounded">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="p-4 overflow-auto flex-1 space-y-6">
          <p className="text-sm text-[var(--color-foreground)]">{blueprint.description}</p>

          {groups.map((g) => (
            <section key={g.name} className="space-y-3">
              <div className="flex items-center gap-2">
                <div className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]">
                  {g.name}
                </div>
                <Badge variant="default" className="text-[10px]">{g.fields.length}</Badge>
              </div>

              <div className="grid grid-cols-1 gap-3">
                {g.fields.map((f) => (
                  <FieldRow key={f.key} field={f} value={values[f.key]} onChange={(v) => setField(f.key, v)} />
                ))}
              </div>
            </section>
          ))}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2 px-4 py-3 border-t border-[var(--color-border)]">
          <div className="text-[11px] text-[var(--color-muted-foreground)]">
            Fields feed the <code className="font-mono">{blueprint.templateId}</code> template.
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
            <Button
              variant="outline"
              size="sm"
              disabled={busy !== null}
              onClick={() => submit("run-once")}
            >
              <Play className="h-3.5 w-3.5 mr-1.5" />
              {busy === "run-once" ? "Queuing…" : "Run once"}
            </Button>
            <Button
              size="sm"
              disabled={busy !== null}
              onClick={() => submit("create-job")}
            >
              <Save className="h-3.5 w-3.5 mr-1.5" />
              {busy === "create-job" ? "Saving…" : "Create Job"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function FieldRow({
  field,
  value,
  onChange,
}: {
  field: FormSchemaField;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const help = field.help ? (
    <div className="text-[11px] text-[var(--color-muted-foreground)] mt-1">{field.help}</div>
  ) : null;

  if (field.type === "bool") {
    return (
      <label className="flex items-start gap-2 cursor-pointer select-none">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={!!value}
          onChange={(e) => onChange(e.target.checked)}
        />
        <div className="min-w-0">
          <div className="text-sm">{field.label}</div>
          {help}
        </div>
      </label>
    );
  }

  return (
    <div>
      <label className="text-xs font-medium">{field.label}</label>
      <div className="mt-1">
        {field.type === "text" && (
          <Input
            value={(value as string) ?? ""}
            placeholder={field.placeholder}
            onChange={(e) => onChange(e.target.value)}
            className="h-8"
          />
        )}
        {field.type === "number" && (
          <Input
            type="number"
            value={(value as number | string) ?? ""}
            placeholder={field.placeholder}
            onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
            className="h-8"
          />
        )}
        {field.type === "textarea" && (
          <textarea
            className="w-full text-sm px-2 py-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-background)] font-mono"
            rows={field.rows || 4}
            value={(value as string) ?? ""}
            placeholder={field.placeholder}
            onChange={(e) => onChange(e.target.value)}
          />
        )}
        {field.type === "code" && (
          <YamlEditor
            value={(value as string) ?? ""}
            onChange={(v) => onChange(v)}
            height={(field.rows || 6) * 22}
          />
        )}
        {field.type === "select" && (
          <select
            value={(value as string) ?? ""}
            onChange={(e) => onChange(e.target.value)}
            className="h-8 text-sm px-2 rounded-md border border-[var(--color-border)] bg-[var(--color-background)]"
          >
            {(field.options || []).map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        )}
        {field.type === "list" && (
          <ListField value={(value as string[]) ?? []} onChange={(v) => onChange(v)} placeholder={field.placeholder} />
        )}
      </div>
      {help}
    </div>
  );
}

function ListField({
  value,
  onChange,
  placeholder,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  const setAt = (i: number, s: string) => {
    const next = [...value]; next[i] = s; onChange(next);
  };
  const remove = (i: number) => {
    const next = [...value]; next.splice(i, 1); onChange(next);
  };
  const add = () => onChange([...value, ""]);
  return (
    <div className="space-y-1.5">
      {value.map((s, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <Input
            value={s}
            placeholder={placeholder || "item"}
            onChange={(e) => setAt(i, e.target.value)}
            className="h-8"
          />
          <Button variant="ghost" size="sm" onClick={() => remove(i)} className="h-8 w-8 p-0">
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={add} className="h-7 text-xs">
        <Plus className="h-3.5 w-3.5 mr-1" /> Add item
      </Button>
    </div>
  );
}
