import { api, setToken, saveUser, clearSession, getStoredUser } from "./api";

export type CurrentUser = {
  id?: string;
  username?: string;
  email?: string;
  display_name?: string;
  roles?: string[];
  permissions?: string[];
  [key: string]: unknown;
};

type LoginResponse = {
  access_token?: string;
  refresh_token?: string;
  token?: string;
  user?: CurrentUser;
  message?: string;
};

/**
 * Login mirrors old-web-frontend/src/auth/auth.js:
 * POST /api/auth/login { username, password } → { access_token, refresh_token, user }
 */
export async function login(username: string, password: string): Promise<CurrentUser> {
  const res = await api<LoginResponse>("POST", "/api/auth/login", { username, password });
  const token = res.access_token ?? res.token;
  if (!token) throw new Error(res.message || "Invalid response from server");
  setToken(token, res.refresh_token ?? null);

  let user = res.user ?? null;
  if (!user) {
    user = await fetchMe();
  }
  if (user) saveUser(user);
  return user ?? {};
}

export async function logout() {
  try { await api("POST", "/api/auth/logout"); } catch { /* ignore */ }
  clearSession();
}

export async function fetchMe(): Promise<CurrentUser | null> {
  try { return await api<CurrentUser>("GET", "/api/auth/me"); }
  catch { return null; }
}

export function getCachedUser(): CurrentUser | null {
  return getStoredUser<CurrentUser>();
}
