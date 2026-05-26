// Tiny fetch wrapper that injects the JWT into every request.
// Token lives in localStorage; on 401 we drop it and force the user to log in.

const TOKEN_KEY = "qa_token";
const TENANT_KEY = "qa_tenant";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(t: string | null): void {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getTenant(): string | null {
  return localStorage.getItem(TENANT_KEY);
}

export function setTenant(t: string | null): void {
  if (t) localStorage.setItem(TENANT_KEY, t);
  else localStorage.removeItem(TENANT_KEY);
}

export interface User {
  id: string;
  email: string;
  name: string | null;
  tenant_id: string;
  is_admin: boolean;
}

export interface SessionSummary {
  session_id: string;
  title: string | null;
  created_at: string;
  last_active_at: string;
  turn_count: number;
}

export interface ToolCallSummary {
  name: string;
  args: Record<string, unknown>;
  result_preview: string | null;
}

export interface ChatResponse {
  session_id: string;
  turn_id: string;
  answer: string;
  tool_calls: ToolCallSummary[];
}

export interface Counts {
  jira_tickets: number;
  jira_users: number;
  github_prs: number;
  github_users: number;
  unified_users: number;
  chat_sessions: number;
  chat_messages: number;
}

export interface PersistedMessage {
  role: "human" | "ai" | "tool" | "system";
  content: unknown;
  tool_calls?: Array<{ id: string; name: string; args: Record<string, unknown> }>;
  tool_call_id?: string;
  name?: string;
  turn_seq: number;
  msg_idx: number;
}

async function request<T>(
  path: string,
  init: RequestInit & { json?: unknown; tenantQuery?: boolean } = {}
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let url = path;
  if (init.tenantQuery) {
    const tenant = getTenant();
    if (tenant) {
      url += (url.includes("?") ? "&" : "?") + "tenant=" + encodeURIComponent(tenant);
    }
  }

  const r = await fetch(url, {
    ...init,
    headers,
    body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
  });
  if (r.status === 401) {
    setToken(null);
    if (location.pathname !== "/login") location.href = "/login";
  }
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${r.status} ${r.statusText}: ${text}`);
  }
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

export const api = {
  login: (email: string, password: string, tenant: string) =>
    request<{ access_token: string; user: User }>(
      `/api/auth/login-json?tenant=${encodeURIComponent(tenant)}`,
      { method: "POST", json: { email, password } }
    ),
  signup: (email: string, password: string, name: string, tenant: string) =>
    request<{ access_token: string; user: User }>(
      `/api/auth/signup?tenant=${encodeURIComponent(tenant)}&name=${encodeURIComponent(name)}`,
      { method: "POST", json: { email, password } }
    ),
  me: () => request<User>("/api/auth/me"),

  listSessions: () => request<SessionSummary[]>("/api/sessions"),
  createSession: () =>
    request<{ session_id: string }>("/api/sessions", { method: "POST" }),
  getSession: (id: string) =>
    request<SessionSummary & { messages: PersistedMessage[] }>(`/api/sessions/${id}`),
  deleteSession: (id: string) =>
    request<void>(`/api/sessions/${id}`, { method: "DELETE" }),

  postChat: (session_id: string, query: string) =>
    request<ChatResponse>("/api/chat", { method: "POST", json: { session_id, query } }),

  counts: () => request<Counts>("/api/stats/counts"),

  listConnectors: () => request<ConnectorStatus[]>("/api/connectors"),
  saveJiraConfig: (cfg: JiraConfigIn) =>
    request<ConnectorStatus>("/api/connectors/jira", { method: "PUT", json: cfg }),
  saveGithubConfig: (cfg: GithubConfigIn) =>
    request<ConnectorStatus>("/api/connectors/github", { method: "PUT", json: cfg }),
  deleteConnector: (name: string) =>
    request<void>(`/api/connectors/${name}`, { method: "DELETE" }),
  syncConnector: (name: string) =>
    request<{ name: string; status: "started" }>(`/api/connectors/${name}/sync`, {
      method: "POST",
    }),
};

export interface JiraConfigIn {
  base_url: string;
  email: string;
  /** Blank/omitted on edit = keep existing stored token. Required on first connect. */
  token?: string;
  project: string;
}

export interface GithubConfigIn {
  api_base: string;
  /** Blank/omitted on edit = keep existing stored token. Required on first connect. */
  token?: string;
  org: string;
  pr_lookback_days: number;
}

export interface ConnectorGuide {
  source: string;
  display_name: string;
  description: string;
  sync_summary: string | null;
  setup_steps: string[];
  field_help: Record<string, string>;
}

export interface ConnectorStatus {
  name: string;
  guide: ConnectorGuide;
  configured: boolean;
  config: Record<string, unknown>;
  last_synced_at: string | null;
  last_sync_status: string | null;
  last_sync_error: string | null;
  updated_at: string | null;
}
