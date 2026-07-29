import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useForm } from "@tanstack/react-form";
import { useMutation } from "@tanstack/react-query";
import { BookOpen, Globe, Github, Linkedin, Youtube } from "lucide-react";
import logoSvg from "@/assets/opensible-logo.png";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { login } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { toast } from "sonner";

export const Route = createFileRoute("/login")({ component: LoginPage });

function LoginPage() {
  const t = useT();
  const navigate = useNavigate();

  const mutation = useMutation({
    mutationFn: ({ u, p }: { u: string; p: string }) => login(u, p),
    onSuccess: () => navigate({ to: "/cloud/summary", replace: true }),
    onError: () => toast.error(t("auth.login.error")),
  });

  const form = useForm({
    defaultValues: { username: "", password: "" },
    onSubmit: async ({ value }) => {
      await mutation.mutateAsync({ u: value.username, p: value.password });
    },
  });

  return (
    <div className="min-h-screen grid grid-cols-1 lg:grid-cols-2">
      {/* Left — Branding */}
      <div className="relative hidden lg:flex flex-col justify-between bg-[var(--color-sidebar)] text-[var(--color-sidebar-foreground)] p-12">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg">
            <img src={logoSvg} className="h-10 w-10" alt="OpenSible" />
          </div>
          <span className="text-lg font-semibold tracking-tight">{t("app.name")}</span>
        </div>

        <div className="space-y-6">
          <h1 className="text-3xl font-bold leading-tight">
            {t("auth.login.tagline")}
          </h1>
          <p className="text-lg text-[var(--color-sidebar-muted)]">
            {t("auth.login.subtitle")}
          </p>

        </div>

        <div className="text-xs text-[var(--color-sidebar-muted)]">
          <span>© {new Date().getFullYear()} {t("app.name")}. {t("auth.login.rightsReserved")}</span>
        </div>
      </div>

      {/* Right — Login Form */}
      <div className="flex flex-col items-center justify-center p-6 bg-[var(--color-background)]">
        <div className="w-full max-w-sm space-y-6 flex-1 flex flex-col justify-center">
          {/* Mobile brand */}
          <div className="flex items-center justify-center gap-3 lg:hidden">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg">
            <img src={logoSvg} className="h-10 w-10" alt="OpenSible" />
          </div>
            <span className="text-lg font-semibold tracking-tight">{t("app.name")}</span>
          </div>

          <div className="space-y-2 text-center">
            <h2 className="text-2xl font-semibold tracking-tight">{t("auth.login.title")}</h2>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {t("auth.login.credentialsPrompt")}
            </p>
          </div>

          <form
            onSubmit={(e) => { e.preventDefault(); e.stopPropagation(); form.handleSubmit(); }}
            className="space-y-4"
          >
            <form.Field name="username" validators={{ onChange: ({ value }) => !value ? t("common.required") : undefined }}>
              {(field) => (
                <div className="space-y-1">
                  <label className="text-sm font-medium">{t("auth.login.username")}</label>
                  <Input value={field.state.value} onChange={e => field.handleChange(e.target.value)} required autoFocus />
                </div>
              )}
            </form.Field>
            <form.Field name="password" validators={{ onChange: ({ value }) => !value ? t("common.required") : undefined }}>
              {(field) => (
                <div className="space-y-1">
                  <label className="text-sm font-medium">{t("auth.login.password")}</label>
                  <Input type="password" value={field.state.value} onChange={e => field.handleChange(e.target.value)} required />
                </div>
              )}
            </form.Field>
            <form.Subscribe selector={(s) => [s.canSubmit, s.isSubmitting]}>
              {([canSubmit, isSubmitting]) => (
                <Button type="submit" className="w-full" disabled={!canSubmit || isSubmitting || mutation.isPending}>
                  {mutation.isPending ? t("common.loading") : t("auth.login.submit")}
                </Button>
              )}
            </form.Subscribe>
          </form>

          <div className="flex items-center justify-end gap-3 text-[var(--color-muted-foreground)]">
            <a href="https://www.linkedin.com/showcase/opensible" target="_blank" rel="noopener noreferrer" className="hover:text-[var(--color-foreground)] transition-colors" aria-label="LinkedIn"><Linkedin className="h-4 w-4" /></a>
            <a href="https://github.com/opensible" target="_blank" rel="noopener noreferrer" className="hover:text-[var(--color-foreground)] transition-colors" aria-label="GitHub"><Github className="h-4 w-4" /></a>
            <a href="https://www.youtube.com/@opensible" target="_blank" rel="noopener noreferrer" className="hover:text-[var(--color-foreground)] transition-colors" aria-label="YouTube"><Youtube className="h-4 w-4" /></a>
            <a href="https://docs.opensible.com" target="_blank" rel="noopener noreferrer" className="hover:text-[var(--color-foreground)] transition-colors" aria-label="Documentation"><BookOpen className="h-4 w-4" /></a>
          </div>
        </div>
      </div>
    </div>
  );
}
