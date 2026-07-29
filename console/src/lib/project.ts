/**
 * Project context — mirrors old-web-frontend's window.currentProjectId.
 * Loads /api/projects, persists the selection in localStorage,
 * and is read by api() to attach the X-Project-Id header.
 */
import {
  createContext, createElement, useCallback, useContext, useEffect, useMemo,
  useState, type ReactNode,
} from "react";
import { api } from "./api";
import { queryClient } from "./query";

const KEY = "current_project_id";

export type Project = {
  id: string;
  name: string;
  description?: string;
  archived?: boolean;
  isArchived?: boolean;
  createdAt?: number;
  updatedAt?: number;
};

type Ctx = {
  projects: Project[];
  currentId: string | null;
  current: Project | null;
  setCurrent: (id: string | null) => Promise<void>;
  reload: () => Promise<void>;
  createProject: (input: { name: string; description?: string }) => Promise<Project>;
  loading: boolean;
};

const ProjectCtx = createContext<Ctx | null>(null);

export function getCurrentProjectId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(KEY);
}

export function setCurrentProjectId(id: string | null) {
  if (typeof window === "undefined") return;
  if (id) window.localStorage.setItem(KEY, id);
  else window.localStorage.removeItem(KEY);
}

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentId, setCurrentIdState] = useState<string | null>(() => getCurrentProjectId());
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api<{ projects?: Project[] } | Project[]>("GET", "/api/projects");
      const list = Array.isArray(r) ? r : (r.projects ?? []);
      setProjects(list);
      const sel = getCurrentProjectId();
      if (!sel || !list.find(p => p.id === sel)) {
        const first = list[0]?.id ?? null;
        setCurrentProjectId(first);
        setCurrentIdState(first);
      } else {
        setCurrentIdState(sel);
      }
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const setCurrent = useCallback(async (id: string | null) => {
    setCurrentProjectId(id);
    setCurrentIdState(id);
    if (id) { try { await api("POST", `/api/projects/${encodeURIComponent(id)}/switch`); } catch { /* ignore */ } }
    // Refetch all project-scoped data with the new X-Project-Id header.
    queryClient.invalidateQueries();
  }, []);

  const createProject = useCallback(async (input: { name: string; description?: string }) => {
    const r = await api<{ project?: Project } | Project>("POST", "/api/projects", input);
    const created = (r as any).project ?? (r as Project);
    await reload();
    if (created?.id) await setCurrent(created.id);
    return created;
  }, [reload, setCurrent]);

  const current = useMemo(
    () => projects.find(p => p.id === currentId) ?? null,
    [projects, currentId],
  );

  const value = useMemo<Ctx>(
    () => ({ projects, currentId, current, setCurrent, reload, createProject, loading }),
    [projects, currentId, current, setCurrent, reload, createProject, loading],
  );

  return createElement(ProjectCtx.Provider, { value }, children);
}

export function useProjects(): Ctx {
  const v = useContext(ProjectCtx);
  if (!v) throw new Error("useProjects must be used within ProjectProvider");
  return v;
}
