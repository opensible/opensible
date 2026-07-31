import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { User as UserIcon, KeyRound, Save, Shield } from "lucide-react";
import { toast } from "sonner";
import { Breadcrumbs } from "@/components/app-shell/Breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { api, ApiError, saveUser, getStoredUser } from "@/lib/api";
import { fetchMe } from "@/lib/auth";

export const Route = createFileRoute("/profile")({ component: ProfilePage });

type Me = {
  id: string;
  username: string;
  email?: string | null;
  roles?: string[];
  role_details?: { id: string; name: string; description?: string }[];
  permissions?: string[];
  is_active?: boolean;
  created_at?: string;
  last_login?: string | null;
};

function ProfilePage() {
  const qc = useQueryClient();
  const meQ = useQuery({
    queryKey: ["auth-me"],
    queryFn: async () => {
      const u = await fetchMe();
      if (u) saveUser(u);
      return u as Me | null;
    },
  });
  const stored = getStoredUser<any>() as any;
  const me = (meQ.data ?? stored ?? null) as (Me & { user_id?: string }) | null;
  const meId = me ? String((me as any).id ?? (me as any).user_id ?? "") : "";

  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [profileErr, setProfileErr] = useState<string | null>(null);

  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [pwErr, setPwErr] = useState<string | null>(null);

  useEffect(() => {
    if (me) {
      setUsername(me.username || "");
      setEmail(me.email || "");
    }
  }, [meId]);

  const saveProfile = useMutation({
    mutationFn: async () => {
      if (!meId) throw new Error("Not signed in");
      return api("PUT", `/api/users/${meId}`, {
        username: username.trim(),
        email: email.trim() || null,
      });
    },
    onSuccess: async () => {
      toast.success("Profile updated");
      const fresh = await fetchMe();
      if (fresh) saveUser(fresh);
      qc.invalidateQueries({ queryKey: ["auth-me"] });
    },
    onError: (e: any) => {
      const msg = e instanceof ApiError ? e.message : String(e);
      setProfileErr(msg);
      toast.error(msg);
    },
  });

  const changePassword = useMutation({
    mutationFn: async () => {
      if (!meId) throw new Error("Not signed in");
      return api("PUT", `/api/users/${meId}`, {
        current_password: currentPw,
        password: newPw,
      });
    },
    onSuccess: () => {
      toast.success("Password changed");
      setCurrentPw(""); setNewPw(""); setConfirmPw("");
    },
    onError: (e: any) => {
      const msg = e instanceof ApiError ? e.message : String(e);
      setPwErr(msg);
      toast.error(msg);
    },
  });

  function submitPassword() {
    setPwErr(null);
    if (!currentPw) return setPwErr("Enter your current password");
    if (newPw.length < 6) return setPwErr("New password must be at least 6 characters");
    if (newPw !== confirmPw) return setPwErr("Passwords do not match");
    changePassword.mutate();
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <Breadcrumbs items={[{ label: "Profile Settings" }]} />
      <div>
        <h1 className="text-2xl font-bold">Profile Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">Manage your account information and password.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><UserIcon className="h-5 w-5" /> Account information</CardTitle>
          <CardDescription>Update your username and email address.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-4">
            <div className="h-16 w-16 rounded-full bg-[var(--color-primary)] text-white flex items-center justify-center text-2xl font-semibold uppercase">
              {(me?.username || "?").charAt(0)}
            </div>
            <div className="min-w-0">
              <div className="font-semibold text-lg">{me?.username || "—"}</div>
              <div className="text-sm text-muted-foreground">{me?.email || "No email set"}</div>
              <div className="flex flex-wrap gap-1 mt-1">
                {(me?.role_details ?? []).map(r => (
                  <Badge key={r.id} variant="default"><Shield className="h-3 w-3 mr-1" />{r.name}</Badge>
                ))}
                {(!me?.role_details || me.role_details.length === 0) && (me?.roles ?? []).map(r => (
                  <Badge key={r} variant="default">{r}</Badge>
                ))}
              </div>
            </div>
          </div>

          <Field label="Username *">
            <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="username" />
          </Field>
          <Field label="Email">
            <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" type="email" />
          </Field>

          {profileErr && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">{profileErr}</div>}

          <div className="grid grid-cols-2 gap-3 pt-2 text-xs text-muted-foreground">
            <div><span className="uppercase tracking-wide">Created:</span> {me?.created_at ? new Date(me.created_at).toLocaleString() : "—"}</div>
            <div><span className="uppercase tracking-wide">Last login:</span> {me?.last_login ? new Date(me.last_login).toLocaleString() : "—"}</div>
          </div>

          <div className="flex justify-end">
            <Button
              onClick={() => { setProfileErr(null); saveProfile.mutate(); }}
              disabled={saveProfile.isPending || !username.trim() }
            >
              <Save className="h-4 w-4 mr-1" />
              {saveProfile.isPending ? "Saving…" : "Save changes"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><KeyRound className="h-5 w-5" /> Change password</CardTitle>
          <CardDescription>Use a strong password of at least 6 characters.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label="Current password *">
            <Input type="password" value={currentPw} onChange={(e) => setCurrentPw(e.target.value)} autoComplete="current-password" />
          </Field>
          <Field label="New password *">
            <Input type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} autoComplete="new-password" />
          </Field>
          <Field label="Confirm new password *">
            <Input type="password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} autoComplete="new-password" />
          </Field>

          {pwErr && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">{pwErr}</div>}

          <div className="flex justify-end">
            <Button
              onClick={submitPassword}
              disabled={changePassword.isPending }
            >
              <KeyRound className="h-4 w-4 mr-1" />
              {changePassword.isPending ? "Updating…" : "Update password"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs font-medium text-muted-foreground mb-1">{label}</div>
      {children}
    </label>
  );
}
