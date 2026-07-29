import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  LayoutTemplate, RefreshCw, X, FileCode, Save, Copy, Play, Plus, Trash2, History, Workflow,
  Maximize2, Minimize2,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { YamlEditor } from "@/components/ui/yaml-editor";
import { api } from "@/lib/api";
import { TemplateInstanceHistoryDialog } from "./TemplateInstanceHistoryDialog";
import { WorkerSelect } from "./WorkerSelect";

type TemplateVariable = {
  name: string; label: string;
  type: "string" | "number" | "boolean" | "code" | "nodes";
  required?: boolean; default?: unknown; placeholder?: string; help?: string;
  language?: string; rows?: number;
};
type TemplateDetail = {
  id: string; name: string; description?: string; category?: string;
  icon?: string; tags?: string[]; variables?: TemplateVariable[];
  uses_roles?: string[];
};

type GroupsResp = { groups?: Record<string, { hosts?: string[] }> };
type NodeRow = { name: string; ip: string; ssh_user: string; ssh_port: string };

export function TemplateDialog({
  templateId,
  onClose,
  onQueued,
  instancePath,
  initialValues,
  initialTargets,
  initialEnvironment,
  initialFilename,
  initialYaml,
}: {
  templateId: string;
  onClose: () => void;
  onQueued?: (executionId: string) => void;
  instancePath?: string;
  initialValues?: Record<string, unknown>;
  initialTargets?: { groups?: string[]; hosts?: string[] };
  initialEnvironment?: string;
  initialFilename?: string;
  initialYaml?: string;
}) {
  const [values, setValues] = useState<Record<string, unknown>>(initialValues || {});
  const [groups, setGroups] = useState<string[]>(initialTargets?.groups || []);
  const [hosts, setHosts] = useState<string[]>(initialTargets?.hosts || []);
  const [environment, setEnvironment] = useState<string>(initialEnvironment || "default");
  const [yaml, setYaml] = useState<string>(initialYaml || "");
  const [filename, setFilename] = useState<string>(initialFilename || "");
  const [busy, setBusy] = useState<null | "render" | "save" | "run">(null);
  const [workerId, setWorkerId] = useState<string>("");

  const [runId, setRunId] = useState<string | null>(null);
  const [playbookFullscreen, setPlaybookFullscreen] = useState(false);
  const [historyPath, setHistoryPath] = useState<string | null>(null);

  // CI/CD (optional, disabled by default) — pipeline steps + git trigger only
  type CicdStep = { name: string; type: "shell" | "ansible"; command: string };
  const [cicdEnabled, setCicdEnabled] = useState(false);
  const [cicdName, setCicdName] = useState("");
  const [cicdSteps, setCicdSteps] = useState<CicdStep[]>([]);
  const [cicdGitEnabled, setCicdGitEnabled] = useState(false);
  const [cicdGitBranch, setCicdGitBranch] = useState("main");
  const [cicdPipelineId, setCicdPipelineId] = useState<string | null>(null);

  const detailQ = useQuery({
    queryKey: ["template", templateId],
    queryFn: async () => {
      const d = await api<TemplateDetail>("GET", `/api/templates/${templateId}`);
      if (!initialValues) {
        const init: Record<string, unknown> = {};
        (d.variables || []).forEach((v) => {
          init[v.name] = v.default ?? (v.type === "boolean" ? false : v.type === "nodes" ? [] : "");
        });
        setValues(init);
      }
      return d;
    },
  });
  const groupsQ = useQuery({
    queryKey: ["inventory-groups"],
    queryFn: () => api<GroupsResp>("GET", "/api/inventory/groups"),
  });

  const detail = detailQ.data;
  const groupsMap = groupsQ.data?.groups || {};
  const groupNames = Object.keys(groupsMap);
  const allHosts = useMemo(() => {
    const s = new Set<string>();
    Object.values(groupsMap).forEach((g) => (g.hosts || []).forEach((h) => s.add(h)));
    return Array.from(s);
  }, [groupsMap]);

  const toggle = (arr: string[], setter: (v: string[]) => void, v: string) => {
    setter(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  };

  const targets = { groups, hosts };

  const shouldRefreshRenderedYaml = (candidate: string) => templateId === "k8s-cluster" && (
    !candidate.includes("# OpenSible k8s template generation: 2026-07-cgroup-fix-v7") ||
    candidate.includes("Wait for first control-plane apiserver to be reachable") ||
    candidate.includes("Probe first control-plane /healthz") ||
    candidate.includes("https://{{ first_cp_ip }}:6443/healthz") ||
    candidate.includes("Generate fresh worker join command") ||
    candidate.includes("Generate fresh worker join token on first control-plane") ||
    candidate.includes("delegate_to: \"{{ first_cp_ip }}\"") ||
    candidate.includes("--kubeconfig=/etc/kubernetes/admin.conf") ||
    candidate.includes("kubeadm_control_plane_join_argv") ||
    candidate.includes("kubeadm_worker_join_argv") ||
    candidate.includes("Reset dirty kubeadm/container runtime state") ||
    candidate.includes("rm -rf /etc/kubernetes /var/lib/etcd /etc/cni/net.d /var/lib/cni /var/run/calico /root/.kube/config") ||
    candidate.includes("Build control-plane join argv (using first control-plane credentials)") ||
    candidate.includes("Build worker join argv (using first control-plane token)")
  );

  const doRender = async () => {
    setBusy("render");
    try {
      const res = await api<{ yaml: string; filename: string }>(
        "POST", `/api/templates/${templateId}/render`, { values, targets },
      );
      setYaml(res.yaml);
      setFilename((current) => current || res.filename);
    } catch (e) { toast.error((e as Error).message); }
    finally { setBusy(null); }
  };

  // When editing a saved instance, auto-populate the Rendered playbook
  // pane with the last-saved YAML. If none was persisted (legacy instances),
  // fall back to rendering from the current values so the editor is never
  // blank on open.
  const autoRenderedRef = useRef(false);
  useEffect(() => {
    if (autoRenderedRef.current) return;
    if (!instancePath) return;
    if (yaml && yaml.trim()) return;
    if (!detail) return;
    autoRenderedRef.current = true;
    doRender();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instancePath, detail]);

  const doSave = async () => {
    setBusy("save");
    try {
      const res = await api<{ ok: boolean; filename: string; written?: string[]; instance_path?: string }>(
        "POST", `/api/templates/${templateId}/save`,
        { values, targets, environment, filename: filename || undefined, instance_path: instancePath, yaml: yaml && !shouldRefreshRenderedYaml(yaml) ? yaml : undefined },
      );

      const extra = res.written && res.written.length > 1 ? ` (+${res.written.length - 1} sidecar files)` : "";
      toast.success(`Saved ${res.filename}${extra}`);
      if (cicdEnabled) await syncCicdPipeline(res.filename, false);
    } catch (e) { toast.error((e as Error).message); }
    finally { setBusy(null); }
  };

  const syncCicdPipeline = async (playbookFilename: string, triggerNow: boolean) => {
    try {
      const limitArg = [...groups, ...hosts].length ? ` --limit '${[...groups, ...hosts].join(":")}'` : "";
      const deployCmd = `ansible-playbook -i inventories/inventory.yml playbooks/${playbookFilename}${limitArg}`;
      const userSteps = cicdSteps
        .filter((s) => s.command.trim())
        .map((s) => ({
          name: s.name || (s.type === "ansible" ? "Ansible step" : "Shell step"),
          type: "shell",
          config: { command: s.type === "ansible" ? `ansible-playbook -i inventories/inventory.yml ${s.command}` : s.command },
        }));
      const stages = [
        { name: "Pipeline", steps: [...userSteps, { name: `Deploy: ${playbookFilename}`, type: "shell", config: { command: deployCmd } }] },
      ];
      const payload = {
        name: cicdName || `${detail?.name || templateId} [${environment}]`,
        description: `Auto-generated pipeline for template ${templateId}`,
        stages,
        git_branch: cicdGitEnabled ? cicdGitBranch : "",
        trigger_config: cicdGitEnabled ? { git: { branch: cicdGitBranch } } : {},
        source: { template_id: templateId, filename: playbookFilename, environment },
      };
      let pid = cicdPipelineId;
      if (pid) {
        await api("PUT", `/api/cicd/pipelines/${pid}`, payload);
      } else {
        const r = await api<{ pipeline_id: string }>("POST", "/api/cicd/pipelines", payload);
        pid = r?.pipeline_id || null;
        if (pid) setCicdPipelineId(pid);
      }
      toast.success("CI/CD pipeline synced");
      if (triggerNow && pid) {
        await api("POST", `/api/cicd/pipelines/${pid}/trigger`, { trigger_type: "on_save" });
        toast.success("Pipeline triggered");
      }
    } catch (e) {
      toast.error(`CI/CD: ${(e as Error).message}`);
    }
  };



  const doRun = async () => {
    setBusy("run");
    try {
      const saved = await api<{ ok: boolean; filename: string; playbook_id?: string }>(
        "POST", `/api/templates/${templateId}/save`,
        { values, targets, environment, filename: filename || undefined, instance_path: instancePath, yaml: yaml && !shouldRefreshRenderedYaml(yaml) ? yaml : undefined },
      );

      const pbId = saved.playbook_id || saved.filename.replace(/\.ya?ml$/i, "");
      const run = await api<{ executionId?: string; execution_id?: string }>(
        "POST", `/api/projects/_current/playbooks/${encodeURIComponent(pbId)}/run`,
        {
          inventory_files: ["inventory.yml"],
          ansible_config: "ansible.cfg",
          limit: [...groups, ...hosts].join(":") || undefined,
          play_name: `Template: ${detail?.name}${environment !== "default" ? ` [${environment}]` : ""}`,
          become: true,
          strategy: "linear",
          worker_id: workerId || undefined,
        },
      );
      const id = run.executionId || run.execution_id;
      if (id) {
        setRunId(String(id));
        toast.success(`Run queued (${id})`);
        onQueued?.(String(id));
        if (cicdEnabled) { try { await syncCicdPipeline(saved.filename, false); } catch { /* toast handled */ } }
        onClose();
        return;
      } else {
        toast.success("Run queued");
      }
      if (cicdEnabled) await syncCicdPipeline(saved.filename, false);
    } catch (e) { toast.error((e as Error).message); }
    finally { setBusy(null); }
  };

  const copyYaml = async () => {
    try { await navigator.clipboard.writeText(yaml); toast.success("YAML copied"); }
    catch { toast.error("Copy failed"); }
  };

  const updateNodes = (name: string, rows: NodeRow[]) => setValues({ ...values, [name]: rows });

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-[var(--color-background)] border border-[var(--color-border)] rounded-lg shadow-xl w-full max-w-6xl max-h-[92vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)] gap-3 flex-wrap">
          <div className="flex items-center gap-2 min-w-0">
            <LayoutTemplate className="h-4 w-4" />
            <div className="font-medium text-sm truncate">{detail?.name || "Template"}</div>
            {detail?.category && <Badge variant="default" className="text-[10px]">{detail.category}</Badge>}
            {instancePath && <Badge variant="success" className="text-[10px]">Editing saved instance</Badge>}
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-[var(--color-muted-foreground)]">Environment</label>
            <Input
              className="h-7 w-32 text-xs"
              value={environment}
              onChange={(e) => setEnvironment(e.target.value)}
              placeholder="default"
              disabled={!!instancePath}
              title={instancePath ? "Environment is fixed for saved instances (duplicate to change)" : "e.g. dev, sit, prod"}
            />
            {instancePath && (
              <Button variant="outline" size="sm" onClick={() => setHistoryPath(instancePath)}>
                <History className="h-4 w-4 mr-1.5" /> History
              </Button>
            )}
            <button onClick={onClose} className="p-1 hover:bg-[var(--color-accent)] rounded">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className={`grid grid-cols-1 ${playbookFullscreen ? "" : "lg:grid-cols-2"} gap-4 p-4 overflow-auto flex-1`}>
          <div className={`space-y-4 ${playbookFullscreen ? "hidden" : ""}`}>
            {detail?.description && (
              <p className="text-xs text-[var(--color-muted-foreground)]">{detail.description}</p>
            )}
            {detail?.uses_roles && detail.uses_roles.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                <span className="text-[var(--color-muted-foreground)]">Uses roles:</span>
                {detail.uses_roles.map((r) => (
                  <Link
                    key={r}
                    to="/infrastructure/playbooks-roles"
                    search={{ tab: "roles" }}
                    className="px-1.5 py-0.5 rounded border border-[var(--color-border)] font-mono hover:bg-[var(--color-accent)]"
                    title="Open Roles editor"
                  >{r}</Link>
                ))}
              </div>
            )}

            {(detail?.variables || []).map((v) => (
              <div key={v.name}>
                <label className="text-xs font-medium">
                  {v.label}{v.required && <span className="text-red-500"> *</span>}
                </label>
                {v.type === "boolean" ? (
                  <div className="mt-1">
                    <label className="inline-flex items-center gap-2 text-sm cursor-pointer">
                      <input
                        type="checkbox"
                        checked={!!values[v.name]}
                        onChange={(e) => setValues({ ...values, [v.name]: e.target.checked })}
                      />
                      Enable
                    </label>
                  </div>
                ) : v.type === "code" ? (
                  <div className="mt-1 border border-[var(--color-border)] rounded-md overflow-hidden">
                    <YamlEditor
                      value={String(values[v.name] ?? "")}
                      onChange={(nv) => setValues({ ...values, [v.name]: nv })}
                      height={(v.rows || 8) * 20}
                    />
                  </div>
                ) : v.type === "number" ? (
                  <Input
                    type="number"
                    className="mt-1"
                    value={String(values[v.name] ?? "")}
                    onChange={(e) =>
                      setValues({ ...values, [v.name]: e.target.value ? Number(e.target.value) : "" })
                    }
                  />
                ) : v.type === "nodes" ? (
                  <NodeTable
                    rows={(Array.isArray(values[v.name]) ? (values[v.name] as NodeRow[]) : []).map((r) => ({
                      name: String(r?.name ?? ""),
                      ip: String(r?.ip ?? ""),
                      ssh_user: String(r?.ssh_user ?? ""),
                      ssh_port: String(r?.ssh_port ?? ""),
                    }))}
                    onChange={(rows) => updateNodes(v.name, rows)}
                  />
                ) : (
                  <Input
                    className="mt-1"
                    placeholder={v.placeholder}
                    value={String(values[v.name] ?? "")}
                    onChange={(e) => setValues({ ...values, [v.name]: e.target.value })}
                  />
                )}
                {v.help && (
                  <div className="text-[11px] text-[var(--color-muted-foreground)] mt-1">{v.help}</div>
                )}
              </div>
            ))}

            <div>
              <div className="text-xs font-medium mb-1">Target groups (optional)</div>
              <div className="flex flex-wrap gap-1.5 p-2 border border-[var(--color-border)] rounded-md">
                {groupNames.length === 0 && (
                  <span className="text-xs text-[var(--color-muted-foreground)]">No groups.</span>
                )}
                {groupNames.map((g) => {
                  const on = groups.includes(g);
                  return (
                    <button
                      key={g}
                      onClick={() => toggle(groups, setGroups, g)}
                      className={`px-2 py-1 rounded text-xs border ${on ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]" : "border-[var(--color-border)] hover:bg-[var(--color-accent)]"}`}
                    >
                      {g}
                    </button>
                  );
                })}
              </div>
            </div>

            <div>
              <div className="text-xs font-medium mb-1">Target hosts (optional)</div>
              <div className="flex flex-wrap gap-1.5 p-2 border border-[var(--color-border)] rounded-md max-h-32 overflow-auto">
                {allHosts.length === 0 && (
                  <span className="text-xs text-[var(--color-muted-foreground)]">No hosts.</span>
                )}
                {allHosts.map((h) => {
                  const on = hosts.includes(h);
                  return (
                    <button
                      key={h}
                      onClick={() => toggle(hosts, setHosts, h)}
                      className={`px-2 py-1 rounded text-xs border font-mono ${on ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]" : "border-[var(--color-border)] hover:bg-[var(--color-accent)]"}`}
                    >
                      {h}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="border border-[var(--color-border)] rounded-md">
              <label className="flex items-center justify-between gap-2 px-3 py-2 cursor-pointer">
                <span className="text-xs font-medium flex items-center gap-1.5">
                  <Workflow className="h-3.5 w-3.5" /> CI/CD Pipeline
                  <Badge variant="default" className="text-[10px]">optional</Badge>
                  {cicdPipelineId && <Badge variant="success" className="text-[10px]">linked</Badge>}
                </span>
                <input
                  type="checkbox"
                  checked={cicdEnabled}
                  onChange={(e) => setCicdEnabled(e.target.checked)}
                />
              </label>
              {cicdEnabled && (
                <div className="p-3 pt-0 space-y-2 text-xs">
                  <div>
                    <label className="text-xs">Pipeline name</label>
                    <Input
                      className="mt-1 h-8 text-xs"
                      value={cicdName}
                      placeholder={`${detail?.name || templateId} [${environment}]`}
                      onChange={(e) => setCicdName(e.target.value)}
                    />
                  </div>
                  <div>
                    <div className="flex items-center justify-between">
                      <label className="text-xs">Pipeline steps (run before deploy)</label>
                      <button
                        type="button"
                        onClick={() => setCicdSteps([...cicdSteps, { name: "", type: "shell", command: "" }])}
                        className="text-[11px] text-[var(--color-primary)] hover:underline inline-flex items-center gap-1"
                      >
                        <Plus className="h-3 w-3" /> Add step
                      </button>
                    </div>
                    <div className="mt-1 space-y-2">
                      {cicdSteps.length === 0 && (
                        <p className="text-[11px] text-[var(--color-muted-foreground)]">
                          No extra steps. The deploy step for this template always runs last.
                        </p>
                      )}
                      {cicdSteps.map((s, i) => (
                        <div key={i} className="border border-[var(--color-border)] rounded p-2 space-y-1.5">
                          <div className="flex items-center gap-1.5">
                            <Input
                              className="h-7 text-xs flex-1"
                              value={s.name}
                              placeholder="Step name (e.g. Build image)"
                              onChange={(e) => {
                                const next = [...cicdSteps]; next[i] = { ...s, name: e.target.value }; setCicdSteps(next);
                              }}
                            />
                            <select
                              className="h-7 text-xs border border-[var(--color-border)] rounded bg-[var(--color-background)] px-1.5"
                              value={s.type}
                              onChange={(e) => {
                                const next = [...cicdSteps]; next[i] = { ...s, type: e.target.value as "shell" | "ansible" }; setCicdSteps(next);
                              }}
                            >
                              <option value="shell">shell</option>
                              <option value="ansible">ansible</option>
                            </select>
                            <button
                              type="button"
                              onClick={() => setCicdSteps(cicdSteps.filter((_, j) => j !== i))}
                              className="p-1 text-[var(--color-muted-foreground)] hover:text-[var(--color-destructive)]"
                              title="Remove step"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                          <textarea
                            className="w-full text-xs font-mono border border-[var(--color-border)] rounded bg-[var(--color-background)] p-1.5 min-h-[44px]"
                            value={s.command}
                            placeholder={s.type === "ansible" ? "playbooks/other.yml --limit web" : "e.g. docker build -t app:$SHA ."}
                            onChange={(e) => {
                              const next = [...cicdSteps]; next[i] = { ...s, command: e.target.value }; setCicdSteps(next);
                            }}
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="border-t border-[var(--color-border)] pt-2">
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={cicdGitEnabled}
                        onChange={(e) => setCicdGitEnabled(e.target.checked)}
                      />
                      <span className="text-xs font-medium">Trigger on Sync to Git</span>
                    </label>
                    {cicdGitEnabled && (
                      <div className="mt-2 space-y-1">
                        <label className="text-xs">Branch filter</label>
                        <Input
                          className="h-8 text-xs"
                          value={cicdGitBranch}
                          placeholder="main"
                          onChange={(e) => setCicdGitBranch(e.target.value)}
                        />
                        <p className="text-[11px] text-[var(--color-muted-foreground)]">
                          Point your Git provider webhook to <code>/api/cicd/pipelines/&lt;id&gt;/trigger</code>. Runs are queued when the branch matches.
                        </p>
                      </div>
                    )}
                  </div>
                  <p className="text-[11px] text-[var(--color-muted-foreground)]">
                    Otherwise, use the Job's <span className="font-medium">Schedule</span> or manual <span className="font-medium">Run</span> — no separate pipeline needed.
                  </p>
                </div>
              )}
            </div>
          </div>


          <div className={`flex flex-col ${playbookFullscreen ? "h-[calc(92vh-8.5rem)]" : "lg:sticky lg:top-0 lg:self-start lg:h-[calc(92vh-8.5rem)]"} min-h-[420px]`}>
            <div className="flex items-center justify-between mb-1 gap-2">
              <div className="text-xs font-medium flex items-center gap-1">
                <FileCode className="h-3.5 w-3.5" /> Rendered playbook
              </div>
              <div className="flex items-center gap-1">
                <Input
                  className="h-7 w-56 text-xs"
                  placeholder={detail ? `tmpl-${detail.id}.yml` : "filename.yml"}
                  value={filename}
                  onChange={(e) => setFilename(e.target.value)}
                />
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 px-2"
                  onClick={() => setPlaybookFullscreen((v) => !v)}
                  title={playbookFullscreen ? "Collapse to split view" : "Expand playbook to full width"}
                >
                  {playbookFullscreen ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
                </Button>
              </div>
            </div>
            <div className="flex-1 border border-[var(--color-border)] rounded-md overflow-hidden">
              {yaml ? (
                <YamlEditor value={yaml} onChange={setYaml} height="100%" />
              ) : (
                <div className="h-full flex items-center justify-center text-xs text-[var(--color-muted-foreground)] p-4 text-center">
                  Fill the variables on the left, then click{" "}
                  <span className="mx-1 font-medium">Render</span> to see the generated Ansible playbook.
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-[var(--color-border)] flex-wrap">
          <WorkerSelect value={workerId} onChange={setWorkerId} disabled={busy !== null} className="mr-auto" side="top" />
          <Button variant="outline" size="sm" onClick={doRender} disabled={busy !== null}>
            <RefreshCw className={`h-4 w-4 mr-1.5 ${busy === "render" ? "animate-spin" : ""}`} /> Render
          </Button>
          <Button variant="outline" size="sm" onClick={copyYaml} disabled={!yaml}>
            <Copy className="h-4 w-4 mr-1.5" /> Copy YAML
          </Button>
          <Button variant="outline" size="sm" onClick={doSave} disabled={busy !== null}>
            <Save className="h-4 w-4 mr-1.5" /> Save Job
          </Button>
          <Button size="sm" onClick={doRun} disabled={busy !== null}>
            <Play className="h-4 w-4 mr-1.5" /> {busy === "run" ? "Launching…" : "Save & Run"}
          </Button>
        </div>
        {runId && (
          <div className="px-4 py-2 border-t border-[var(--color-border)] text-xs text-[var(--color-muted-foreground)]">
            Run queued: <span className="font-mono">{runId}</span>
          </div>
        )}
      </div>
      {historyPath && (
        <TemplateInstanceHistoryDialog
          path={historyPath}
          onClose={() => setHistoryPath(null)}
          onRestore={(v, t) => {
            if (v && typeof v === "object") setValues(v as Record<string, unknown>);
            const tt = (t || {}) as { groups?: string[]; hosts?: string[] };
            if (Array.isArray(tt.groups)) setGroups(tt.groups);
            if (Array.isArray(tt.hosts)) setHosts(tt.hosts);
          }}
        />
      )}
    </div>
  );
}

function NodeTable({ rows, onChange }: { rows: NodeRow[]; onChange: (rows: NodeRow[]) => void }) {
  const update = (i: number, patch: Partial<NodeRow>) => {
    const next = rows.slice();
    next[i] = { ...next[i], ...patch } as NodeRow;
    onChange(next);
  };
  const add = () =>
    onChange([...rows, { name: `node-${rows.length + 1}`, ip: "", ssh_user: "", ssh_port: "" }]);
  const remove = (i: number) => onChange(rows.filter((_, idx) => idx !== i));

  return (
    <div className="mt-1 border border-[var(--color-border)] rounded-md overflow-hidden">
      <table className="w-full text-xs">
        <thead className="bg-[var(--color-accent)]/40">
          <tr>
            <th className="text-left px-2 py-1 font-medium">Name</th>
            <th className="text-left px-2 py-1 font-medium">IP address</th>
            <th className="text-left px-2 py-1 font-medium">SSH user</th>
            <th className="text-left px-2 py-1 font-medium w-20">Port</th>
            <th className="w-8"></th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={5} className="px-2 py-3 text-center text-[var(--color-muted-foreground)]">
                No nodes yet. Click <span className="font-medium">Add node</span> to define one.
              </td>
            </tr>
          )}
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-[var(--color-border)]">
              <td className="p-1">
                <Input
                  className="h-7 text-xs"
                  value={r.name}
                  onChange={(e) => update(i, { name: e.target.value })}
                  placeholder="k3s-server-1"
                />
              </td>
              <td className="p-1">
                <Input
                  className="h-7 text-xs font-mono"
                  value={r.ip}
                  onChange={(e) => update(i, { ip: e.target.value })}
                  placeholder="10.0.0.11"
                />
              </td>
              <td className="p-1">
                <Input
                  className="h-7 text-xs"
                  value={r.ssh_user}
                  onChange={(e) => update(i, { ssh_user: e.target.value })}
                  placeholder="(default)"
                />
              </td>
              <td className="p-1">
                <Input
                  className="h-7 text-xs"
                  value={r.ssh_port}
                  onChange={(e) => update(i, { ssh_port: e.target.value })}
                  placeholder="22"
                />
              </td>
              <td className="p-1 text-right">
                <button
                  onClick={() => remove(i)}
                  className="p-1 hover:bg-[var(--color-accent)] rounded"
                  title="Remove"
                >
                  <Trash2 className="h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="p-2 border-t border-[var(--color-border)]">
        <Button variant="outline" size="sm" onClick={add}>
          <Plus className="h-3.5 w-3.5 mr-1" /> Add node
        </Button>
      </div>
    </div>
  );
}
