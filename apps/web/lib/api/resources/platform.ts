/** API resource functions (auth, workspaces, settings, demo, search, AI, diagnostics, audit). Paths follow apps/api/API.md. */
import { api, asList } from "../client";
import type * as T from "../types";
import { w, e, list, type List } from "./core";

/* ------------------------------------------------------------------ auth */

export const auth = {
  session: () => api.get<T.SessionInfo>("/auth/session"),
  login: (email: string, password: string) => api.post<T.SessionInfo>("/auth/login", { email, password }),
  signup: (email: string, name: string, password: string, bootstrapToken?: string) =>
    api.post<T.SessionInfo>("/auth/signup", {
      email,
      name,
      password,
      ...(bootstrapToken ? { bootstrap_token: bootstrapToken } : {}),
    }),
  logout: () => api.post<void>("/auth/logout"),
  me: () => api.get<T.User>("/auth/me"),
  updateMe: (body: { name?: string; current_password?: string; new_password?: string; revoke_tokens?: boolean }) =>
    api.patch<T.User>("/auth/me", body),
  tokens: () => list(api.get<List<T.ApiToken>>("/auth/tokens")),
  createToken: (body: { name: string; workspace_id?: string | null; read_only?: boolean; expires_in_days?: number | null }) =>
    api.post<T.ApiTokenCreated>("/auth/tokens", body),
  revokeToken: (id: string) => api.del(`/auth/tokens/${e(id)}`),
};

/* ------------------------------------------------------------------ workspaces */

export const workspaces = {
  list: () => list(api.get<List<T.Workspace>>("/workspaces")),
  get: (ws: string) => api.get<T.Workspace>(w(ws)),
  create: (body: { name: string; description?: string }) => api.post<T.Workspace>("/workspaces", body),
  update: (ws: string, body: { name?: string; description?: string }) => api.patch<T.Workspace>(w(ws), body),
  remove: (ws: string) => api.del(w(ws)),
  settings: (ws: string) => api.get<T.WorkspaceSettingsDoc>(`${w(ws)}/settings`),
  updateSettings: (ws: string, body: Partial<T.WorkspaceSettingsDoc>) =>
    api.patch<T.WorkspaceSettingsDoc>(`${w(ws)}/settings`, body),
  /** Resolves a period expression with the workspace calendar ("last month", "Q3", "YTD", "rolling 30 days"). */
  resolvePeriod: (ws: string, text: string, today?: string) =>
    api.get<T.CalendarResolution>(`${w(ws)}/semantic-models/calendar/resolve`, { text, today }),
  members: (ws: string) => list(api.get<List<T.Member>>(`${w(ws)}/members`)),
  addMember: (ws: string, body: { email: string; role: T.Role }) => api.post<T.Member>(`${w(ws)}/members`, body),
  updateMember: (ws: string, userId: string, role: T.Role) =>
    api.patch<T.Member>(`${w(ws)}/members/${e(userId)}`, { role }),
  removeMember: (ws: string, userId: string) => api.del(`${w(ws)}/members/${e(userId)}`),
  home: (ws: string) => api.get<T.HomeSummary>(`${w(ws)}/home`),
  jobs: (ws: string, params: { status?: string; limit?: number } = {}) =>
    list(api.get<List<T.Job>>(`${w(ws)}/jobs`, params)),
};

/* ------------------------------------------------------------------ demo */

export const demo = {
  status: () => api.get<T.DemoAvailability>("/demo/status"),
  /** Returns `202 {workspace_id, job, poll_url}`; poll with `waitForJob`. */
  load: (body: { workspace_id?: string | null; reset?: boolean; name?: string } = {}) =>
    api.post<T.JobAccepted & { workspace_id?: string | null }>("/demo/load", body),
};

/* ------------------------------------------------------------------ platform */

export const search = {
  query: (ws: string, q: string, params: { kinds?: string[]; limit?: number } = {}, signal?: AbortSignal) =>
    api
      .get<List<T.SearchHit>>(`${w(ws)}/search`, { q, kinds: params.kinds, limit: params.limit ?? 30 }, signal)
      .then(asList),
};

export const ai = {
  settings: (ws: string) => api.get<T.AISettings>(`${w(ws)}/ai/settings`),
  /** `api_key` is write-only; `""` clears the stored key. */
  updateSettings: (ws: string, body: Partial<T.AISettings> & { api_key?: string | null }) =>
    api.put<T.AISettings>(`${w(ws)}/ai/settings`, body),
  test: (ws: string) => api.post<{ ok: boolean; message: string; model?: string; latency_ms?: number }>(`${w(ws)}/ai/test`),
  usage: (ws: string, params: { days?: number } = {}) => api.get<T.AIUsageSummary>(`${w(ws)}/ai/usage`, params),
};

export const diagnostics = {
  info: () => api.get<T.DiagnosticsInfo>("/diagnostics"),
  health: () => api.get<{ status: string; version?: string }>("/health"),
  events: (ws: string, params: { category?: string; status?: string; limit?: number; since?: string } = {}) =>
    list(api.get<List<T.EventLogEntry>>(`${w(ws)}/diagnostics/events`, params)),
  summary: (ws: string, params: { hours?: number } = {}) =>
    api.get<{ categories?: T.DiagnosticsSummaryRow[]; [k: string]: unknown }>(`${w(ws)}/diagnostics/summary`, params),
  audit: (ws: string, params: { limit?: number; offset?: number; action?: string; resource_type?: string } = {}) =>
    api.get<T.Page<T.AuditEntry> | T.AuditEntry[]>(`${w(ws)}/audit`, params).then((d) =>
      Array.isArray(d) ? { items: d, total: d.length } : d,
    ),
};

/* ------------------------------------------------------------------ invites */

export type InviteStatus = "pending" | "accepted" | "revoked" | "expired";

export interface Invite {
  id: string;
  email: string;
  role: T.Role;
  status: InviteStatus;
  created_by: string | null;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
}

/** Returned once on creation: the raw token is never stored server-side. */
export interface InviteCreated {
  token: string;
  /** Web path to hand to the invitee: `/invite/{token}`. */
  accept_path: string;
  invite: Invite;
}

export interface InvitePreview {
  workspace_name: string;
  email: string;
  role: T.Role;
  expires_at: string;
  /** An account with this email exists: sign in as it, then accept with the session. */
  account_exists: boolean;
}

export const invites = {
  list: (ws: string) => list(api.get<List<Invite>>(`${w(ws)}/invites`)),
  create: (ws: string, body: { email: string; role: T.Role; expires_in_days?: number }) =>
    api.post<InviteCreated>(`${w(ws)}/invites`, body),
  revoke: (ws: string, id: string) => api.del(`${w(ws)}/invites/${e(id)}`),
  preview: (token: string) => api.get<InvitePreview>(`/auth/invites/${e(token)}`),
  accept: (body: { token: string; name?: string; password?: string }) => api.post<T.SessionInfo>("/auth/invites/accept", body),
};
