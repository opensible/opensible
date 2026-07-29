import { useEffect } from "react";
import { useForm } from "@tanstack/react-form";
import { useMutation } from "@tanstack/react-query";
import { X, FolderPlus, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useProjects } from "@/lib/project";
import { toast } from "sonner";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: (id: string) => void;
};

export function NewProjectDialog({ open, onOpenChange, onCreated }: Props) {
  const { createProject } = useProjects();

  const mutation = useMutation({
    mutationFn: (input: { name: string; description: string }) => createProject(input),
    onSuccess: (p) => {
      toast.success("Project created");
      onOpenChange(false);
      if (p?.id) onCreated?.(p.id);
    },
    onError: (err: Error) => toast.error(err.message || "Failed to create project"),
  });

  const form = useForm({
    defaultValues: { name: "", description: "" },
    onSubmit: async ({ value }) => {
      await mutation.mutateAsync({ name: value.name.trim(), description: value.description.trim() });
    },
  });

  useEffect(() => {
    if (!open) { form.reset(); mutation.reset(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;
  const submitting = mutation.isPending;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40" onClick={() => !submitting && onOpenChange(false)}>
      <div
        className="w-full max-w-md rounded-2xl bg-[var(--color-card)] border border-[var(--color-border)] shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-border)]">
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-[var(--color-primary)]/10 flex items-center justify-center">
              <FolderPlus className="h-4 w-4 text-[var(--color-primary)]" />
            </div>
            <div>
              <div className="text-sm font-semibold">Create new project</div>
              <div className="text-xs text-[var(--color-muted-foreground)]">A workspace for stacks, playbooks &amp; secrets.</div>
            </div>
          </div>
          <button className="h-8 w-8 inline-flex items-center justify-center rounded-md hover:bg-[var(--color-muted)]" onClick={() => onOpenChange(false)} aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <form
          onSubmit={(e) => { e.preventDefault(); e.stopPropagation(); form.handleSubmit(); }}
          className="px-5 py-4 space-y-4"
        >
          <form.Field
            name="name"
            validators={{ onChange: ({ value }) => !value.trim() ? "Project name is required" : undefined }}
          >
            {(field) => (
              <div>
                <label className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1.5">Name</label>
                <Input
                  autoFocus placeholder="e.g. ByteDC Production"
                  value={field.state.value}
                  onBlur={field.handleBlur}
                  onChange={(e) => field.handleChange(e.target.value)}
                  disabled={submitting}
                />
                {field.state.meta.errors[0] && (
                  <div className="text-xs text-[var(--color-destructive)] mt-1">{String(field.state.meta.errors[0])}</div>
                )}
              </div>
            )}
          </form.Field>
          <form.Field name="description">
            {(field) => (
              <div>
                <label className="block text-xs font-medium text-[var(--color-muted-foreground)] mb-1.5">
                  Description <span className="text-[var(--color-muted-foreground)]/70">(optional)</span>
                </label>
                <Input
                  placeholder="Short description"
                  value={field.state.value}
                  onChange={(e) => field.handleChange(e.target.value)}
                  disabled={submitting}
                />
              </div>
            )}
          </form.Field>
          {mutation.error && <div className="text-xs text-[var(--color-destructive)]">{(mutation.error as Error).message}</div>}
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>Cancel</Button>
            <form.Subscribe selector={(s) => [s.canSubmit, s.isSubmitting]}>
              {([canSubmit, isSubmitting]) => (
                <Button type="submit" disabled={!canSubmit || isSubmitting || submitting}>
                  {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
                  Create project
                </Button>
              )}
            </form.Subscribe>
          </div>
        </form>
      </div>
    </div>
  );
}
