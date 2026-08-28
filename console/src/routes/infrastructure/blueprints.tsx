import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Cloud, HardDrive, Loader2, RefreshCw, Search } from "lucide-react";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { RunLogDialog } from "@/components/infrastructure/RunLogDialog";
import { TemplateDialog } from "@/components/infrastructure/TemplateDialog";
import { StackBlueprintsPanel } from "@/components/infrastructure/StackBlueprintsPanel";
import { BlueprintDialog, type BlueprintAction } from "@/components/infrastructure/BlueprintDialog";
import { BlueprintFormDialog, type BlueprintFormAction } from "@/components/infrastructure/BlueprintFormDialog";
import type { Blueprint, BlueprintGroup } from "@/lib/blueprints";
import { fetchHubBlueprintGroups, HUB_URL } from "@/lib/blueprints/hub";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { resolveInventoryFiles } from "@/lib/inventory-files";
import { useProjects } from "@/lib/project";

export const Route = createFileRoute("/infrastructure/blueprints")({
  component: BlueprintsPage,
});

type BlueprintSource = "local" | "hub";

function BlueprintsPage() {
  const t = useT();
  const qc = useQueryClient();
  const { currentId } = useProjects();
  const [bpSearch, setBpSearch] = useState("");
  const [source, setSource] = useState<BlueprintSource>("local");
  const [runLogId, setRunLogId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [openTemplateInitial, setOpenTemplateInitial] = useState<{
    values?: Record<string, unknown>;
    filename?: string;
    environment?: string;
    instancePath?: string;
  } | null>(null);
  const [blueprintPreview, setBlueprintPreview] = useState<{ bp: Blueprint; group: BlueprintGroup } | null>(null);
  const [blueprintForm, setBlueprintForm] = useState<{ bp: Blueprint; group: BlueprintGroup } | null>(null);

  const hub = useQuery({
    queryKey: ["hub-blueprints", HUB_URL],
    queryFn: fetchHubBlueprintGroups,
    enabled: source === "hub",
    staleTime: 5 * 60_000,
    retry: false,
  });


  const openBlueprint = (bp: Blueprint, group: BlueprintGroup) => {
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

    if (!currentId) {
      toast.error("Select a project first", {
        description: "Blueprint jobs are saved inside a project. Pick or create one from the project switcher.",
      });
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
      toast.success(`Job created: ${saved.filename}`);

      if (action === "run-once") {
        const pbId = saved.playbook_id || saved.filename.replace(/\.ya?ml$/i, "");
        try {
          const inventoryFiles = await resolveInventoryFiles();
          const run = await api<{ executionId?: string; execution_id?: string }>(
            "POST",
            `/api/projects/_current/playbooks/${encodeURIComponent(pbId)}/run`,
            {
              inventory_files: inventoryFiles,
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
        setOpenTemplateInitial({
          values,
          filename: saved.filename,
          environment,
          instancePath: saved.instance_path,
        });
        setOpenId(bp.templateId);
      }
    } catch (e) {
      const err = e as { message?: string; body?: { field_errors?: Record<string, string | string[]> } };
      const fieldErrors = err?.body?.field_errors;
      if (fieldErrors && Object.keys(fieldErrors).length > 0) {
        const details = Object.entries(fieldErrors)
          .map(([field, msg]) => `${field}: ${Array.isArray(msg) ? msg.join(", ") : msg}`)
          .join("\n");
        toast.error("Failed to create job", { description: details });
      } else {
        toast.error(`Failed to create job: ${err?.message ?? "unknown error"}`);
      }
    }
  };

  const handleBlueprintAction = async (action: BlueprintAction) => {
    if (!blueprintPreview) return;
    await runBlueprintAction(blueprintPreview.bp, action);
  };

  const handleBlueprintFormSubmit = async (
    action: BlueprintFormAction,
    values: Record<string, unknown>,
  ) => {
    if (!blueprintForm) return;
    await runBlueprintAction(blueprintForm.bp, action, values);
  };

  return (
    <div className="space-y-4">
      <Breadcrumbs items={[{ label: "Infrastructure" }, { label: t("nav.stackHubBlueprints") }]} />
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold">{t("nav.stackHubBlueprints")}</h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {t("templates.tab.blueprints.desc")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center rounded-md border border-[var(--color-border)] p-0.5">
            <button
              type="button"
              onClick={() => setSource("local")}
              className={`flex items-center gap-1.5 rounded-[5px] px-2.5 h-7 text-xs font-medium transition-colors ${
                source === "local"
                  ? "bg-[var(--color-primary)] text-[var(--color-primary-foreground)]"
                  : "text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)]/50"
              }`}
            >
              <HardDrive className="h-3.5 w-3.5" /> Local
            </button>
            <button
              type="button"
              onClick={() => setSource("hub")}
              className={`flex items-center gap-1.5 rounded-[5px] px-2.5 h-7 text-xs font-medium transition-colors ${
                source === "hub"
                  ? "bg-[var(--color-primary)] text-[var(--color-primary-foreground)]"
                  : "text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)]/50"
              }`}
            >
              <Cloud className="h-3.5 w-3.5" /> Cloud Hub Community
            </button>
          </div>
          {source === "hub" && (
            <Button
              size="sm"
              variant="outline"
              className="h-8 px-2"
              onClick={() => hub.refetch()}
              disabled={hub.isFetching}
              title={`Refresh from ${HUB_URL}`}
            >
              {hub.isFetching ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
            </Button>
          )}
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
            <Input
              value={bpSearch}
              onChange={(e) => setBpSearch(e.target.value)}
              placeholder="Search blueprints…"
              className="pl-7 h-8 w-56"
            />
          </div>
        </div>
      </div>

      {source === "hub" && (
        <div className="text-xs text-[var(--color-muted-foreground)]">
          {hub.isLoading
            ? `Loading blueprints from ${HUB_URL}…`
            : hub.isError
              ? `Stack Hub unreachable (${HUB_URL}): ${(hub.error as Error).message}. Switch to Local to use the bundled blueprints.`
              : "Community registry · https://hub.opensible.com"}
        </div>
      )}

      <StackBlueprintsPanel
        search={bpSearch}
        onSelect={openBlueprint}
        groups={source === "hub" ? hub.data ?? [] : undefined}
      />


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
