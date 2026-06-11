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
  jira_base_url: string | null;
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
  slack_messages: number;
  slack_users: number;
  unified_users: number;
  chat_sessions: number;
  chat_messages: number;
}

export interface SourceFreshness {
  source: string;
  count: number;
  last_synced_at: string | null;
  synced_ago: string | null;
  last_sync_status: string | null;
}

export interface Signal {
  signal_id: string;
  detector: string;
  kind: string;
  item_key: string;
  title: string | null;
  person_name: string | null;
  evidence: Record<string, unknown>;
  confidence: number;
  url: string | null;
  status: string;
  label: string | null;
  first_seen_at: string | null;
  matured_at: string | null;
  last_seen_at: string | null;
}

export interface InboxResponse {
  signals: Signal[];
  total_open: number;
  total_matured: number;
}

export interface TrendPoint {
  week: string; // ISO date of the week's Monday
  value: number;
  n: number | null;
}

export interface TrendsResponse {
  weeks: string[];
  series: Record<string, TrendPoint[]>;
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

// Turn a FastAPI error body into something fit for a form banner. Pydantic 422s
// arrive as {detail: [{msg, loc}, ...]}; plain HTTPExceptions as {detail: "..."}.
function errorMessage(status: number, statusText: string, text: string): string {
  try {
    const detail = (JSON.parse(text) as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const msg = detail
        .map((e) =>
          typeof (e as { msg?: unknown }).msg === "string"
            ? (e as { msg: string }).msg.replace(/^Value error,\s*/, "")
            : null
        )
        .filter(Boolean)
        .join("; ");
      if (msg) return msg;
    }
  } catch {
    // Body wasn't JSON — fall back to the raw text below.
  }
  return text || `${status} ${statusText}`;
}

async function request<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {}
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const r = await fetch(path, {
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
    throw new Error(errorMessage(r.status, r.statusText, text));
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

  // Streamed turn: invokes onEvent("tool"|"answer"|"error", data) as SSE arrives.
  postChatStream: async (
    session_id: string,
    query: string,
    onEvent: (event: string, data: Record<string, unknown>) => void
  ): Promise<void> => {
    const headers = new Headers({ "Content-Type": "application/json" });
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const r = await fetch("/api/chat/stream", {
      method: "POST",
      headers,
      body: JSON.stringify({ session_id, query }),
    });
    if (r.status === 401) {
      setToken(null);
      if (location.pathname !== "/login") location.href = "/login";
      return;
    }
    if (!r.ok || !r.body) {
      throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`);
    }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sep = buf.indexOf("\n\n");
      while (sep !== -1) {
        const block = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        let event = "message";
        let data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (data) onEvent(event, JSON.parse(data) as Record<string, unknown>);
        sep = buf.indexOf("\n\n");
      }
    }
  },

  counts: () => request<Counts>("/api/stats/counts"),
  freshness: () => request<SourceFreshness[]>("/api/stats/freshness"),

  listInbox: () => request<InboxResponse>("/api/inbox?limit=100"),
  trends: (weeks = 12) => request<TrendsResponse>(`/api/stats/trends?weeks=${weeks}`),
  confirmSignal: (id: string) =>
    request<Signal>(`/api/inbox/${id}/confirm`, { method: "POST" }),
  dismissSignal: (id: string) =>
    request<Signal>(`/api/inbox/${id}/dismiss`, { method: "POST" }),

  listConnectors: () => request<ConnectorStatus[]>("/api/connectors"),
  saveJiraConfig: (cfg: JiraConfigIn) =>
    request<ConnectorStatus>("/api/connectors/jira", { method: "PUT", json: cfg }),
  saveGithubConfig: (cfg: GithubConfigIn) =>
    request<ConnectorStatus>("/api/connectors/github", { method: "PUT", json: cfg }),
  saveSlackConfig: (cfg: SlackConfigIn) =>
    request<ConnectorStatus>("/api/connectors/slack", { method: "PUT", json: cfg }),
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

export interface SlackConfigIn {
  workspace: string;
  /** Blank/omitted on edit = keep existing stored token. Required on first connect. */
  bot_token?: string;
  lookback_days: number;
  channel_types: string;
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
