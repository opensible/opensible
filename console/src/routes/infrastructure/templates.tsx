import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  RefreshCw, Search, Container, Globe, Boxes, Package, RefreshCcw,
  Plus, X,
} from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { TemplateDialog } from "@/components/infrastructure/TemplateDialog";
import { TemplateInstancesTable } from "@/components/infrastructure/TemplateInstancesTable";
import { RunLogDialog } from "@/components/infrastructure/RunLogDialog";
import { StackBlueprintsPanel } from "@/components/infrastructure/StackBlueprintsPanel";
import { BlueprintDialog, type BlueprintAction } from "@/components/infrastructure/BlueprintDialog";
import { BlueprintFormDialog, type BlueprintFormAction } from "@/components/infrastructure/BlueprintFormDialog";
import type { Blueprint, BlueprintGroup } from "@/lib/blueprints";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

export const Route = createFileRoute("/infrastructure/templates")({
  component: TemplatesPage,
});

type TemplateCard = {
  id: string; name: string; description?: string; category?: string;
  icon?: string; tags?: string[];
};


const ICON_MAP: Record<string, typeof Container> = {
  container: Container, globe: Globe, boxes: Boxes, package: Package,
  "refresh-cw": RefreshCcw,
};

function IconFor({ name }: { name?: string }) {
  const Ico = ICON_MAP[name || ""] || Package;
  return <Ico className="h-5 w-5" />;
}

type Tab = "jobs" | "blueprints";

function TemplatesPage() {
  const t = useT();
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [instSearch, setInstSearch] = useState("");
  const [bpSearch, setBpSearch] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [openTemplateInitial, setOpenTemplateInitial] = useState<{
    values?: Record<string, unknown>;
    filename?: string;
    environment?: string;
    instancePath?: string;
  } | null>(null);
  const [tab, setTab] = useState<Tab>("jobs");
  const [runLogId, setRunLogId] = useState<string | null>(null);
  const [blueprintPreview, setBlueprintPreview] = useState<{ bp: Blueprint; group: BlueprintGroup } | null>(null);
  const [blueprintForm, setBlueprintForm] = useState<{ bp: Blueprint; group: BlueprintGroup } | null>(null);
  const [catalogOpen, setCatalogOpen] = useState(false);

  const openBlueprint = (bp: Blueprint, group: BlueprintGroup) => {
    // Schema-driven blueprints skip the preview and open the generic form directly.
    if (bp.available && bp.templateId && bp.formSchema && bp.formSchema.length > 0) {
      setBlueprintForm({ bp, group });
      return;
    }
    setBlueprintPreview({ bp, group });
  };

  const runBlueprintAction = async (
    bp: Blueprint,
    action: BlueprintAction | BlueprintFormAction,
    valuesOverride?: Record<string, unknown>,
  ) => {
    if (!bp.available || !bp.templateId) {
      toast.error("This blueprint is not yet wired to an executable template.");
      return;
    }

    const environment = action === "run-once" ? "sandbox" : "default";
    const stem = bp.filenameStem || bp.id;
    const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 12);
    const filename = `${stem}-${environment}-${stamp}.yml`;
    const values = valuesOverride ?? bp.defaults ?? {};

    try {
      const saved = await api<{ ok: boolean; filename: string; playbook_id?: string; instance_path?: string }>(
        "POST",
        `/api/templates/${bp.templateId}/save`,
        { values, targets: {}, environment, filename },
      );

      await qc.invalidateQueries({ queryKey: ["template-instances"] });
      setBlueprintPreview(null);
      setBlueprintForm(null);
      setTab("jobs");
      toast.success(`Job created: ${saved.filename}`);

      if (action === "run-once") {
        const pbId = saved.playbook_id || saved.filename.replace(/\.ya?ml$/i, "");
        try {
          const run = await api<{ executionId?: string; execution_id?: string }>(
            "POST",
            `/api/projects/_current/playbooks/${encodeURIComponent(pbId)}/run`,
            {
              inventory_files: ["inventory.yml"],
              ansible_config: "ansible.cfg",
              play_name: `Blueprint: ${bp.name} [${environment}]`,
              become: true,
              strategy: "linear",
            },
          );
          const id = run.executionId || run.execution_id;
          if (id) {
            toast.success(`Run queued (${id})`);
            setRunLogId(String(id));
          } else {
            toast.success("Run queued");
          }
        } catch (e) {
          toast.error(`Run failed: ${(e as Error).message}`);
        }
      } else {
        // Create Job: open the saved instance so user can review/tweak variables and re-save.
        setOpenTemplateInitial({
          values,
          filename: saved.filename,
          environment,
          instancePath: saved.instance_path,
        });
        setOpenId(bp.templateId);
      }
    } catch (e) {
      toast.error(`Failed to create job: ${(e as Error).message}`);
    }
  };

  const handleBlueprintAction = async (action: BlueprintAction) => {
    const preview = blueprintPreview;
    if (!preview) return;
    await runBlueprintAction(preview.bp, action);
  };

  const handleBlueprintFormSubmit = async (
    action: BlueprintFormAction,
    values: Record<string, unknown>,
  ) => {
    const form = blueprintForm;
    if (!form) return;
    await runBlueprintAction(form.bp, action, values);
  };


  const listQ = useQuery({
    queryKey: ["templates"],
    queryFn: () => api<{ templates?: TemplateCard[] }>("GET", "/api/templates"),
  });

  const templates = listQ.data?.templates || [];
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return templates;
    return templates.filter((t) =>
      t.name.toLowerCase().includes(s) ||
      (t.description || "").toLowerCase().includes(s) ||
      (t.tags || []).some((x) => x.toLowerCase().includes(s))
    );
  }, [templates, q]);

  const byCategory = useMemo(() => {
    const m: Record<string, TemplateCard[]> = {};
    filtered.forEach((t) => {
      const c = t.category || "Other";
      (m[c] ||= []).push(t);
    });
    return m;
  }, [filtered]);

  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "Infrastructure" }, { label: "Stack Deployment & Templates" }]} />
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold">{t("page.templates.title")}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">{t("page.templates.subtitle")}</p>
        </div>
        <div className="flex items-center gap-2">
          {tab === "jobs" && (
            <div className="relative">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
              <Input value={instSearch} onChange={(e) => setInstSearch(e.target.value)} placeholder={t("templates.searchJobs")} className="pl-7 h-8 w-56" />
            </div>
          )}
          {tab === "blueprints" && (
            <div className="relative">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
              <Input value={bpSearch} onChange={(e) => setBpSearch(e.target.value)} placeholder="Search blueprints…" className="pl-7 h-8 w-56" />
            </div>
          )}
          <Button variant="outline" size="sm" onClick={() => {
            listQ.refetch();
            qc.invalidateQueries({ queryKey: ["template-instances"] });
          }}>
            <RefreshCw className="h-4 w-4 mr-1.5" /> {t("common.refresh")}
          </Button>
          <Button size="sm" onClick={() => setCatalogOpen(true)}>
            <Plus className="h-4 w-4 mr-1.5" /> Create New Build
          </Button>
        </div>
      </div>

      <div className="flex items-center gap-1 border-b border-[var(--color-border)]">
        {([
          ["jobs", t("templates.tab.jobs")],
          ["blueprints", t("templates.tab.blueprints")],
        ] as const).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id as Tab)}
            className={`px-3 py-1.5 text-sm border-b-2 -mb-px transition-colors ${
              tab === id
                ? "border-[var(--color-primary)] text-[var(--color-foreground)] font-medium"
                : "border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <p className="text-xs text-[var(--color-muted-foreground)]">
        {tab === "jobs" ? t("templates.tab.jobs.desc") : t("templates.tab.blueprints.desc")}
      </p>

      {tab === "jobs" && (
        <TemplateInstancesTable filterText={instSearch} onRunLog={(id) => setRunLogId(id)} />
      )}

      {tab === "blueprints" && <StackBlueprintsPanel search={bpSearch} onSelect={openBlueprint} />}

      {catalogOpen && (
        <>
          <div
            className="fixed inset-0 bg-black/40 z-40 animate-in fade-in"
            onClick={() => setCatalogOpen(false)}
          />
          <div className="fixed inset-y-0 right-0 z-50 w-full max-w-3xl bg-[var(--color-background)] border-l border-[var(--color-border)] shadow-xl flex flex-col animate-in slide-in-from-right">
            <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-border)]">
              <div>
                <div className="text-base font-semibold">Create New Build</div>
                <div className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                  Choose a resource from the catalog to configure a new build.
                </div>
              </div>
              <div className="flex items-center gap-2">
                <div className="relative">
                  <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
                  <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("templates.searchTemplates")} className="pl-7 h-8 w-56" />
                </div>
                <Button variant="ghost" size="sm" onClick={() => setCatalogOpen(false)}>
                  <X className="h-4 w-4" />
                </Button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-5 space-y-6">
              {listQ.isLoading && <div className="text-sm text-[var(--color-muted-foreground)]">{t("common.loading")}</div>}
              {!listQ.isLoading && filtered.length === 0 && (
                <Card><CardContent className="p-6 text-center text-sm text-[var(--color-muted-foreground)]">{t("templates.noMatch")}</CardContent></Card>
              )}
              {Object.entries(byCategory).map(([cat, items]) => (
                <div key={cat} className="space-y-2">
                  <div className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]">{cat}</div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {items.map((tc) => (
                      <Card
                        key={tc.id}
                        className="cursor-pointer hover:border-[var(--color-primary)] transition-colors"
                        onClick={() => { setOpenId(tc.id); setCatalogOpen(false); }}
                      >
                        <CardContent className="p-4">
                          <div className="flex items-start gap-3">
                            <div className="h-10 w-10 rounded-md bg-[var(--color-accent)] flex items-center justify-center shrink-0">
                              <IconFor name={tc.icon} />
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="font-medium text-sm truncate">{tc.name}</div>
                              <div className="text-xs text-[var(--color-muted-foreground)] line-clamp-2 mt-0.5">{tc.description}</div>
                              <div className="flex flex-wrap gap-1 mt-2">
                                {(tc.tags || []).map((x) => (
                                  <Badge key={x} variant="default" className="text-[10px]">{x}</Badge>
                                ))}
                              </div>
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}


      {openId && (
        <TemplateDialog
          templateId={openId}
          onClose={() => { setOpenId(null); setOpenTemplateInitial(null); qc.invalidateQueries({ queryKey: ["template-instances"] }); }}
          onQueued={(id) => setRunLogId(id)}
          initialValues={openTemplateInitial?.values}
          initialFilename={openTemplateInitial?.filename}
          initialEnvironment={openTemplateInitial?.environment}
          instancePath={openTemplateInitial?.instancePath}
        />
      )}

      {blueprintPreview && (
        <BlueprintDialog
          blueprint={blueprintPreview.bp}
          group={blueprintPreview.group}
          onClose={() => setBlueprintPreview(null)}
          onAction={handleBlueprintAction}
        />
      )}

      {blueprintForm && (
        <BlueprintFormDialog
          blueprint={blueprintForm.bp}
          group={blueprintForm.group}
          onClose={() => setBlueprintForm(null)}
          onSubmit={handleBlueprintFormSubmit}
        />
      )}

      {runLogId && (
        <RunLogDialog executionId={runLogId} live onClose={() => setRunLogId(null)} />
      )}
    </div>
  );
}
