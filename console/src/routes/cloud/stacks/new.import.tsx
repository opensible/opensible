import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { GitBranch } from "lucide-react";

export const Route = createFileRoute("/cloud/stacks/new/import")({ component: ImportPage });

function ImportPage() {
  const navigate = useNavigate();
  const [pulling, setPulling] = useState(false);

  async function pull() {
    setPulling(true);
    try {
      await api("POST", `/api/projects/_current/sources/stacks/sync`, { direction: "pull" });
      toast.success("Pulled from Git");
      navigate({ to: "/cloud/stacks" });
    } catch (e: any) { toast.error(e?.message || "Pull failed"); }
    finally { setPulling(false); }
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: "Import from Git" }]} />
      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><GitBranch className="h-5 w-5" /> Import from Git</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-[var(--color-muted-foreground)]">
            Pull existing OpenTofu/Terraform stacks from the Git remote configured under Stack Project Settings.
          </p>
          <Button onClick={pull} disabled={pulling}>{pulling ? "Pulling…" : "Pull stacks"}</Button>
        </CardContent>
      </Card>
    </div>
  );
}
