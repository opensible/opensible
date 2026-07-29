export type FormFieldType =
  | "text"
  | "number"
  | "textarea"
  | "code"
  | "select"
  | "bool"
  | "list"
  | "nodes";

export type FormSchemaField = {
  /** Variable key sent to the backend template. */
  key: string;
  label: string;
  type: FormFieldType;
  help?: string;
  placeholder?: string;
  default?: unknown;
  /** Grouping label used to render section headings. */
  group?: string;
  /** Options for `type: "select"`. */
  options?: { value: string; label: string }[];
  /** Language hint for `type: "code"` (e.g. "yaml", "shell"). */
  language?: string;
  /** Rows for textarea / code. */
  rows?: number;
  /** When true, the field is not required. Default: true (optional). */
  required?: boolean;
  /**
   * How to serialize the value before sending to the backend template.
   *  - "as-is" (default): raw value
   *  - "yaml-list": list<string> -> YAML block list string
   *  - "yaml-map": Record<string,string> -> YAML mapping string
   */
  serialize?: "as-is" | "yaml-list" | "yaml-map";
};

export type Blueprint = {
  id: string;
  name: string;
  description: string;
  /** Logo URL. If omitted, the group logo is used. */
  logo?: string;
  tags?: string[];
  author?: string;
  stars?: number;
  /** Optional link to source (git repo, gist, etc.) */
  source?: string;
  /** Repo-relative folder under IaC/blueprints/. Presence implies `available`. */
  path?: string;
  /** Whether a real playbook is shipped under `path`. Defaults to false. */
  available?: boolean;
  /** Backend template id used to instantiate this blueprint (Create Job / Run once). */
  templateId?: string;
  /** Suggested filename stem for the saved playbook (no extension). */
  filenameStem?: string;
  /** Optional preselected variable values to prefill the template dialog. */
  defaults?: Record<string, unknown>;
  /**
   * Optional schema-driven form. When present, the blueprint is rendered
   * through the generic <BlueprintFormDialog /> — no hand-coded UI required.
   * Blueprints without a formSchema fall back to the existing TemplateDialog.
   */
  formSchema?: FormSchemaField[];
};

export type BlueprintGroup = {
  id: string;
  name: string;
  description: string;
  /** Group logo URL (used as fallback for blueprints without their own logo). */
  logo: string;
  blueprints: Blueprint[];
};
