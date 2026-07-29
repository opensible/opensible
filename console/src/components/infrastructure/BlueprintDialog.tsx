import { useState } from "react";
import { X, Star, ExternalLink, Save, Play, FileCode, Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { Blueprint, BlueprintGroup } from "@/lib/blueprints";
import opensibleLogoUrl from "@/assets/opensible-logo.png";
const opensibleLogo = { url: opensibleLogoUrl };

export type BlueprintAction = "create-job" | "run-once";

export function BlueprintDialog({
  blueprint,
  group,
  onClose,
  onAction,
}: {
  blueprint: Blueprint;
  group: BlueprintGroup;
  onClose: () => void;
  onAction: (action: BlueprintAction) => void;
}) {
  const canUse = !!(blueprint.available && blueprint.templateId);
  const initialLogo = blueprint.logo || group.logo || opensibleLogo.url;
  const [logoSrc, setLogoSrc] = useState(initialLogo);

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-[var(--color-background)] border border-[var(--color-border)] rounded-lg shadow-xl w-full max-w-2xl max-h-[92vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)] gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="h-9 w-9 rounded-md bg-[var(--color-accent)] flex items-center justify-center shrink-0 overflow-hidden">
              <img src={logoSrc} alt="" className="h-5 w-5 object-contain" onError={() => { if (logoSrc !== opensibleLogo.url) setLogoSrc(opensibleLogo.url); }} />
            </div>
            <div className="min-w-0">
              <div className="font-medium text-sm truncate">{blueprint.name}</div>
              <div className="text-[11px] text-[var(--color-muted-foreground)] truncate">
                {group.name}
                {blueprint.author ? ` · by ${blueprint.author}` : ""}
              </div>
            </div>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-[var(--color-accent)] rounded">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-4 overflow-auto flex-1 space-y-4">
          <p className="text-sm text-[var(--color-foreground)]">{blueprint.description}</p>

          <div className="flex flex-wrap items-center gap-1.5">
            {blueprint.available ? (
              <Badge variant="success" className="text-[10px]">Available</Badge>
            ) : (
              <Badge variant="default" className="text-[10px] opacity-70">Coming soon</Badge>
            )}
            {typeof blueprint.stars === "number" && (
              <span className="inline-flex items-center gap-0.5 text-[11px] text-[var(--color-muted-foreground)]">
                <Star className="h-3 w-3" /> {blueprint.stars}
              </span>
            )}
            {(blueprint.tags || []).map((t) => (
              <Badge key={t} variant="default" className="text-[10px]">{t}</Badge>
            ))}
          </div>

          {blueprint.path && (
            <div className="text-[11px] text-[var(--color-muted-foreground)] flex items-center gap-1.5">
              <FileCode className="h-3.5 w-3.5" />
              <code className="font-mono">{blueprint.path}</code>
            </div>
          )}

          {!canUse && (
            <div className="text-xs rounded-md border border-[var(--color-border)] bg-[var(--color-accent)]/40 p-3 flex gap-2">
              <Info className="h-4 w-4 shrink-0 mt-0.5" />
              <div>
                This blueprint is not yet wired to an executable template. You can view its source,
                or contribute a playbook under <code className="font-mono">IaC/blueprints/</code>.
              </div>
            </div>
          )}

          <div className="rounded-md border border-[var(--color-border)] p-3 space-y-2">
            <div className="text-xs font-medium">How this works</div>
            <ul className="text-xs text-[var(--color-muted-foreground)] space-y-1 list-disc pl-4">
              <li>
                <span className="font-medium text-[var(--color-foreground)]">Create Job</span> — opens
                the template with blueprint defaults so you can review variables and save it as a
                reusable job under Build &amp; Deployment.
              </li>
              <li>
                <span className="font-medium text-[var(--color-foreground)]">Run once</span> — same
                dialog, but intended for a one-shot execution. Fill vars and hit Run.
              </li>
            </ul>
          </div>
        </div>

        <div className="flex items-center justify-between gap-2 px-4 py-3 border-t border-[var(--color-border)]">
          <div className="flex items-center gap-2">
            {blueprint.source && (
              <Button variant="outline" size="sm" asChild>
                <a href={blueprint.source} target="_blank" rel="noreferrer">
                  View source <ExternalLink className="h-3 w-3 ml-1" />
                </a>
              </Button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!canUse}
              onClick={() => onAction("run-once")}
              title={canUse ? "Open template ready to run once" : "Not available"}
            >
              <Play className="h-3.5 w-3.5 mr-1.5" /> Run once
            </Button>
            <Button
              size="sm"
              disabled={!canUse}
              onClick={() => onAction("create-job")}
              title={canUse ? "Open template ready to save as a job" : "Not available"}
            >
              <Save className="h-3.5 w-3.5 mr-1.5" /> Create Job
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
