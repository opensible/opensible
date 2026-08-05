import { useMemo, useState } from "react";
import { Star } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { BLUEPRINT_GROUPS, type Blueprint, type BlueprintGroup } from "@/lib/blueprints";
import opensibleLogoUrl from "@/assets/opensible-logo.png";
const opensibleLogo = { url: opensibleLogoUrl };

function BlueprintLogo({
  blueprint,
  group,
  size = "sm",
}: {
  blueprint: Blueprint;
  group: BlueprintGroup;
  size?: "sm" | "lg";
}) {
  const initial = blueprint.logo || group.logo || opensibleLogo.url;
  const [src, setSrc] = useState(initial);
  const isLg = size === "lg";
  return (
    <div
      className={`rounded-2xl flex items-center justify-center shrink-0 overflow-hidden bg-[var(--color-muted)] ${
        isLg ? "h-16 w-16" : "h-10 w-10 rounded-md"
      }`}
    >
      <img
        src={src}
        alt={blueprint.name}
        className={`object-contain ${isLg ? "h-10 w-10" : "h-6 w-6"}`}
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
    <div className="space-y-8">
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
          <div key={group.id} className="space-y-3">
            <div className="flex items-center gap-2">
              <img
                src={group.logo}
                alt={group.name}
                className="h-5 w-5 object-contain"
              />
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
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                {group.blueprints.map((bp) => (
                  <Card
                    key={bp.id}
                    className="group relative overflow-hidden border-[var(--color-border)] bg-[var(--color-card)] transition-all duration-300 hover:shadow-lg hover:border-[var(--color-primary)]"
                  >
                    <CardContent className="p-4">
                      <div className="flex items-start justify-between mb-4">
                        <div className="relative">
                          <div className="absolute -inset-2.5 rounded-full bg-[var(--color-primary)]/10 scale-0 group-hover:scale-100 transition-transform duration-300" />
                          <div className="relative">
                            <BlueprintLogo
                              blueprint={bp}
                              group={group}
                              size="lg"
                            />
                          </div>
                        </div>
                        {typeof bp.stars === "number" && (
                          <div className="flex items-center gap-1 px-2 py-0.5 rounded-full border border-[var(--color-border)] bg-[var(--color-muted)] text-[var(--color-muted-foreground)]">
                            <Star className="h-3 w-3 text-[var(--color-warning)]" />
                            <span className="text-[11px] font-semibold">{bp.stars}</span>
                          </div>
                        )}
                      </div>

                      <h3 className="text-base font-bold text-[var(--color-foreground)] group-hover:text-[var(--color-primary)] transition-colors mb-1.5">
                        {bp.name}
                      </h3>
                      <p className="text-xs text-[var(--color-muted-foreground)] leading-relaxed mb-4 line-clamp-2">
                        {bp.description}
                      </p>

                      <div className="flex flex-wrap gap-1.5 mb-4">
                        {(bp.tags || []).slice(0, 4).map((x) => (
                          <Badge
                            key={x}
                            variant="default"
                            className="text-[9px] uppercase tracking-wider font-bold px-1.5 py-0.5"
                          >
                            {x}
                          </Badge>
                        ))}
                      </div>

                      <div className="flex items-center justify-between pt-4 border-t border-[var(--color-border)]">
                        <div className="flex flex-col gap-1">
                          <span className="text-[9px] uppercase font-bold text-[var(--color-muted-foreground)]">
                            {bp.author ? `by ${bp.author}` : "community"}
                          </span>
                          {bp.available ? (
                            <Badge variant="success" className="text-[10px] w-fit">Available</Badge>
                          ) : (
                            <Badge variant="default" className="text-[10px] opacity-60 w-fit">Coming soon</Badge>
                          )}
                        </div>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 text-[11px] font-semibold rounded-lg"
                          disabled={!bp.available && !bp.source && !onSelect}
                          onClick={() => onSelect?.(bp, group)}
                        >
                          {bp.available ? "Use" : bp.source ? "View" : "Details"}
                        </Button>
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
