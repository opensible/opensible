import { useMemo, useState } from "react";
import { Star } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { BLUEPRINT_GROUPS, type Blueprint, type BlueprintGroup } from "@/lib/blueprints";
import opensibleLogoUrl from "@/assets/opensible-logo.png";
const opensibleLogo = { url: opensibleLogoUrl };

function BlueprintLogo({ blueprint, group }: { blueprint: Blueprint; group: BlueprintGroup }) {
  const initial = blueprint.logo || group.logo || opensibleLogo.url;
  const [src, setSrc] = useState(initial);
  return (
    <div className="h-10 w-10 rounded-md bg-[var(--color-accent)] flex items-center justify-center shrink-0 overflow-hidden">
      <img
        src={src}
        alt={blueprint.name}
        className="h-6 w-6 object-contain"
        onError={() => {
          if (src !== opensibleLogo.url) setSrc(opensibleLogo.url);
        }}
      />
    </div>
  );
}

export function StackBlueprintsPanel({
  search = "",
  onSelect,
}: {
  search?: string;
  onSelect?: (blueprint: Blueprint, group: BlueprintGroup) => void;
} = {}) {
  const q = search;

  const groups = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return BLUEPRINT_GROUPS;
    return BLUEPRINT_GROUPS.map((g) => ({
      ...g,
      blueprints: g.blueprints.filter(
        (b) =>
          b.name.toLowerCase().includes(s) ||
          b.description.toLowerCase().includes(s) ||
          (b.tags || []).some((t) => t.toLowerCase().includes(s)) ||
          g.name.toLowerCase().includes(s)
      ),
    })).filter((g) => g.blueprints.length > 0);
  }, [q]);

  return (
    <div className="space-y-6">
      {groups.length === 0 && (
        <Card>
          <CardContent className="p-6 text-center text-sm text-[var(--color-muted-foreground)]">
            No blueprints match your search.
          </CardContent>
        </Card>
      )}



      {groups.map((group) => {
        const isList = group.id === "uninstall";
        return (
        <div key={group.id} className="space-y-2">
          <div className="flex items-center gap-2">
            <img src={group.logo} alt={group.name} className="h-4 w-4 object-contain" />
            <div className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted-foreground)]">
              {group.name}
            </div>
            <div className="text-xs text-[var(--color-muted-foreground)]">
              · {group.description}
            </div>
          </div>
          {isList ? (
            <div className="flex flex-col divide-y divide-[var(--color-border)] rounded-md border border-[var(--color-border)] bg-[var(--color-card)]">
              {group.blueprints.map((bp) => (
                <div
                  key={bp.id}
                  className="flex items-center gap-3 px-3 py-2 hover:bg-[var(--color-accent)]/40 transition-colors"
                >
                  <BlueprintLogo blueprint={bp} group={group} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 min-w-0">
                      <div className="font-medium text-sm truncate">{bp.name}</div>
                      {typeof bp.stars === "number" && (
                        <div className="flex items-center gap-0.5 text-[11px] text-[var(--color-muted-foreground)] shrink-0">
                          <Star className="h-3 w-3" /> {bp.stars}
                        </div>
                      )}
                      {bp.available ? (
                        <Badge variant="success" className="text-[10px] shrink-0">Available</Badge>
                      ) : (
                        <Badge variant="default" className="text-[10px] shrink-0 opacity-60">Coming soon</Badge>
                      )}
                    </div>
                    <div className="text-xs text-[var(--color-muted-foreground)] truncate mt-0.5">
                      {bp.description}
                    </div>
                    <div className="flex flex-wrap items-center gap-1 mt-1">
                      <span className="text-[11px] text-[var(--color-muted-foreground)] mr-1">
                        {bp.author ? `by ${bp.author}` : "community"}
                      </span>
                      {(bp.tags || []).map((x) => (
                        <Badge key={x} variant="default" className="text-[10px]">
                          {x}
                        </Badge>
                      ))}
                    </div>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-xs shrink-0"
                    disabled={!bp.available && !bp.source && !onSelect}
                    onClick={() => onSelect?.(bp, group)}
                  >
                    {bp.available ? "Use blueprint" : bp.source ? "View" : "Details"}
                  </Button>
                </div>
              ))}
            </div>
          ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {group.blueprints.map((bp) => (
              <Card
                key={bp.id}
                className="hover:border-[var(--color-primary)] transition-colors"
              >
                <CardContent className="p-4">
                  <div className="flex items-start gap-3">
                    <BlueprintLogo blueprint={bp} group={group} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <div className="font-medium text-sm truncate">{bp.name}</div>
                        {typeof bp.stars === "number" && (
                          <div className="flex items-center gap-0.5 text-[11px] text-[var(--color-muted-foreground)] shrink-0">
                            <Star className="h-3 w-3" /> {bp.stars}
                          </div>
                        )}
                      </div>
                      <div className="text-xs text-[var(--color-muted-foreground)] line-clamp-2 mt-0.5">
                        {bp.description}
                      </div>
                      <div className="flex flex-wrap gap-1 mt-2">
                        {(bp.tags || []).map((x) => (
                          <Badge key={x} variant="default" className="text-[10px]">
                            {x}
                          </Badge>
                        ))}
                      </div>
                      <div className="mt-3 flex items-center justify-between gap-2">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span className="text-[11px] text-[var(--color-muted-foreground)] truncate">
                            {bp.author ? `by ${bp.author}` : "community"}
                          </span>
                          {bp.available ? (
                            <Badge variant="success" className="text-[10px] shrink-0">Available</Badge>
                          ) : (
                            <Badge variant="default" className="text-[10px] shrink-0 opacity-60">Coming soon</Badge>
                          )}
                        </div>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 text-xs"
                          disabled={!bp.available && !bp.source && !onSelect}
                          onClick={() => onSelect?.(bp, group)}
                        >
                          {bp.available ? "Use blueprint" : bp.source ? "View" : "Details"}
                        </Button>

                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
          )}
        </div>
        );
      })}
    </div>
  );
}
