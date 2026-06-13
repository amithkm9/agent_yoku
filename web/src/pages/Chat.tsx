import {
  KeyboardEvent,
  ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  api,
  ChatResponse,
  Counts,
  PersistedMessage,
  SessionSummary,
  SourceFreshness,
  setToken,
  User,
} from "../lib/api";
import {
  AppBrand,
  SendIcon,
  SettingsIcon,
  SparkleIcon,
  ThemeToggle,
  TrendsIcon,
} from "../components/AppChrome";

function extractText(content: unknown): string {
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === "string") return block;
        if (block && typeof block === "object") {
          const b = block as Record<string, unknown>;
          if (b.type === "text" && typeof b.text === "string") return b.text;
          if ("content" in b) return extractText(b.content);
        }
        return "";
      })
      .filter(Boolean)
      .join("\n\n");
  }
  return String(content);
}

interface ChatTurn {
  id: string; // stable key + lets a streaming callback target its own turn
  question: string;
  answer: string;
  toolCalls: ChatResponse["tool_calls"];
  status?: string; // live progress while streaming (cleared once the answer lands)
}

// Map raw tool names to human verbs so the streaming trace reads like a
// teammate working, not like an internal API log.
const TOOL_LABELS: Record<string, string> = {
  semantic_search: "Searching across sources",
  mongo_query: "Querying records",
  mongo_count: "Counting records",
  list_collections: "Scanning collections",
  describe_collection: "Reading the schema",
  describe_collections: "Reading the schema",
  resolve_user: "Resolving a person",
  who_knows: "Finding who knows this",
  get_memory: "Recalling what it knows",
  update_memory: "Updating its memory",
  recall_history: "Recalling history",
  propose_action: "Drafting an action",
};

function humanTool(name: string): string {
  return TOOL_LABELS[name] || `Running ${name}`;
}

type AnswerBlock =
  | { type: "paragraph"; text: string }
  | { type: "ul"; items: string[] }
  | { type: "ol"; items: string[] };

function parseAnswerBlocks(text: string): AnswerBlock[] {
  const normalized = text.replace(/\r\n/g, "\n").trim();
  if (!normalized) return [];

  const lines = normalized.split("\n");
  const blocks: AnswerBlock[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i].trim();
    if (!line) {
      i += 1;
      continue;
    }

    const unordered = line.match(/^[-*]\s+(.*)$/);
    if (unordered) {
      const items: string[] = [];
      while (i < lines.length) {
        const match = lines[i].trim().match(/^[-*]\s+(.*)$/);
        if (!match) break;
        items.push(match[1].trim());
        i += 1;
      }
      blocks.push({ type: "ul", items });
      continue;
    }

    const ordered = line.match(/^\d+\.\s+(.*)$/);
    if (ordered) {
      const items: string[] = [];
      while (i < lines.length) {
        const match = lines[i].trim().match(/^\d+\.\s+(.*)$/);
        if (!match) break;
        items.push(match[1].trim());
        i += 1;
      }
      blocks.push({ type: "ol", items });
      continue;
    }

    const paragraph: string[] = [];
    while (i < lines.length) {
      const current = lines[i].trim();
      if (
        !current ||
        /^[-*]\s+/.test(current) ||
        /^\d+\.\s+/.test(current)
      ) {
        break;
      }
      paragraph.push(current);
      i += 1;
    }
    blocks.push({ type: "paragraph", text: paragraph.join(" ") });
  }

  return blocks;
}

// The agent cites its sources inline as `AS-1234` (JIRA) and
// `AsatoCorp/repo#123` (GitHub PR). Linkify those to the actual ticket / PR so
// every claim is one click from its source. The JIRA host comes from the
// tenant's connector config (via /me); without one, JIRA keys stay plain text.
const CITATION_RE = /\bAS-\d+\b|\b[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+#\d+\b/g;

function citationHref(token: string, jiraBase: string | null): string | null {
  if (/^AS-\d+$/.test(token)) return jiraBase ? `${jiraBase}/browse/${token}` : null;
  const pr = token.match(/^([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)#(\d+)$/);
  if (pr) return `https://github.com/${pr[1]}/pull/${pr[2]}`;
  return null;
}

function linkifyCitations(text: string, keyPrefix: string, jiraBase: string | null): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let tokenIndex = 0;

  for (const match of text.matchAll(CITATION_RE)) {
    const token = match[0];
    const href = citationHref(token, jiraBase);
    if (href === null) continue;
    const start = match.index ?? 0;

    if (start > lastIndex) nodes.push(text.slice(lastIndex, start));
    nodes.push(
      <a
        key={`${keyPrefix}-cite-${tokenIndex}`}
        className="citation"
        href={href}
        target="_blank"
        rel="noreferrer"
      >
        {token}
      </a>
    );
    lastIndex = start + token.length;
    tokenIndex += 1;
  }

  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

function renderInline(text: string, keyPrefix: string, jiraBase: string | null): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*.+?\*\*|`.+?`|\*[^*\n]+\*)/g;
  let lastIndex = 0;
  let tokenIndex = 0;

  for (const match of text.matchAll(pattern)) {
    const [token] = match;
    const start = match.index ?? 0;

    if (start > lastIndex) {
      nodes.push(
        ...linkifyCitations(text.slice(lastIndex, start), `${keyPrefix}-pre-${tokenIndex}`, jiraBase)
      );
    }

    if (token.startsWith("**")) {
      nodes.push(
        <strong key={`${keyPrefix}-strong-${tokenIndex}`}>
          {linkifyCitations(token.slice(2, -2), `${keyPrefix}-strong-${tokenIndex}`, jiraBase)}
        </strong>
      );
    } else if (token.startsWith("`")) {
      nodes.push(
        <code key={`${keyPrefix}-code-${tokenIndex}`}>{token.slice(1, -1)}</code>
      );
    } else {
      nodes.push(
        <em key={`${keyPrefix}-em-${tokenIndex}`}>
          {linkifyCitations(token.slice(1, -1), `${keyPrefix}-em-${tokenIndex}`, jiraBase)}
        </em>
      );
    }

    lastIndex = start + token.length;
    tokenIndex += 1;
  }

  if (lastIndex < text.length) {
    nodes.push(...linkifyCitations(text.slice(lastIndex), `${keyPrefix}-tail`, jiraBase));
  }

  return nodes;
}

function AnswerContent({ text, jiraBase }: { text: string; jiraBase: string | null }) {
  const blocks = parseAnswerBlocks(text);

  if (blocks.length === 0) {
    return <p>{text}</p>;
  }

  return (
    <div className="rich-text">
      {blocks.map((block, index) => {
        if (block.type === "paragraph") {
          return <p key={index}>{renderInline(block.text, `p-${index}`, jiraBase)}</p>;
        }

        if (block.type === "ol") {
          return (
            <ol key={index}>
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>
                  {renderInline(item, `ol-${index}-${itemIndex}`, jiraBase)}
                </li>
              ))}
            </ol>
          );
        }

        return (
          <ul key={index}>
            {block.items.map((item, itemIndex) => (
              <li key={itemIndex}>
                {renderInline(item, `ul-${index}-${itemIndex}`, jiraBase)}
              </li>
            ))}
          </ul>
        );
      })}
    </div>
  );
}

function timeGreeting(date = new Date()): string {
  const h = date.getHours();
  if (h < 12) return "Morning";
  if (h < 18) return "Afternoon";
  return "Evening";
}

function firstNameOf(user: User | null): string {
  const raw = (user?.name || user?.email || "there").trim();
  const first = raw.split(/[@\s.]+/)[0] || raw;
  return first.charAt(0).toUpperCase() + first.slice(1);
}

// "14:26" for today, "Jun 1" for anything older — recents don't need seconds.
function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (d.toDateString() === new Date().toDateString()) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

// Quick-start prompts tailored to what yoku actually knows about:
// the tenant's JIRA tickets + GitHub PRs.
interface Suggestion {
  label: string;
  prompt: string;
  icon: ReactNode;
}

const SUGGESTIONS: Suggestion[] = [
  {
    label: "My open PRs",
    prompt: "What are my open pull requests right now, and which are waiting on me?",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="6" cy="6" r="2.4" stroke="currentColor" strokeWidth="1.6" />
        <circle cx="6" cy="18" r="2.4" stroke="currentColor" strokeWidth="1.6" />
        <circle cx="18" cy="18" r="2.4" stroke="currentColor" strokeWidth="1.6" />
        <path
          d="M6 8.4v7.2M18 15.6V12a4 4 0 0 0-4-4h-3.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    label: "Stale tickets",
    prompt: "Which of my JIRA tickets have been idle the longest?",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="1.6" />
        <path d="M12 7.5V12l3 2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    label: "What shipped",
    prompt: "What shipped this week across our repos?",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M3 7.5 12 3l9 4.5-9 4.5-9-4.5Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
        <path d="M3 12.5 12 17l9-4.5M3 17 12 21.5 21 17" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    label: "Find an expert",
    prompt: "Who knows the most about our authentication code?",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="8" r="3.2" stroke="currentColor" strokeWidth="1.6" />
        <path d="M5.5 19a6.5 6.5 0 0 1 13 0" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    label: "Sprint status",
    prompt: "Give me a status summary of the current sprint.",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M4 20V11M10 20V5M16 20v-6M2 20h20" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    ),
  },
];

export function Chat() {
  const nav = useNavigate();
  const [user, setUser] = useState<User | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [counts, setCounts] = useState<Counts | null>(null);
  const [freshness, setFreshness] = useState<SourceFreshness[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [gapCount, setGapCount] = useState<number>(0);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [syncing, setSyncing] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const streamRef = useRef<AbortController | null>(null);

  const abortStream = useCallback(() => {
    streamRef.current?.abort();
    streamRef.current = null;
  }, []);

  useEffect(() => {
    document.title = "Agent Yoku";
    api
      .me()
      .then(setUser)
      .catch(() => nav("/login"));
    void refresh();
    api
      .listInbox()
      .then((r) => setGapCount(r.total_matured))
      .catch(() => setGapCount(0));
    return () => abortStream(); // cancel any in-flight stream on unmount
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history]);

  const refresh = useCallback(async () => {
    try {
      const [s, c, f] = await Promise.all([
        api.listSessions(),
        api.counts(),
        api.freshness(),
      ]);
      setSessions(s);
      setCounts(c);
      setFreshness(f);
      if (!activeSession && s.length > 0) selectSession(s[0].session_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [activeSession]);

  async function selectSession(id: string) {
    abortStream(); // don't let a prior stream patch the session we're leaving
    setBusy(false);
    setActiveSession(id);
    try {
      const detail = await api.getSession(id);
      setHistory(messagesToTurns(detail.messages));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function newSession() {
    abortStream();
    setBusy(false);
    try {
      const r = await api.createSession();
      await refresh();
      selectSession(r.session_id);
      setHistory([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function triggerSync(source: string) {
    setSyncing((s) => new Set(s).add(source));
    const preSyncAt = freshness.find((x) => x.source === source)?.last_synced_at ?? null;
    try {
      await api.syncConnector(source);
      // Poll until last_synced_at changes (success) or status flips to error
      for (let i = 0; i < 60; i++) {
        await new Promise((res) => setTimeout(res, 2000));
        const f = await api.freshness();
        setFreshness(f);
        const updated = f.find((x) => x.source === source);
        const done =
          updated?.last_synced_at !== preSyncAt ||
          (updated?.last_sync_status === "error" && updated?.last_synced_at === null && preSyncAt !== null);
        if (done) break;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing((s) => { const n = new Set(s); n.delete(source); return n; });
    }
  }

  async function deleteSession(id: string) {
    if (!confirm("Delete this chat? This can't be undone.")) return;
    try {
      await api.deleteSession(id);
      if (id === activeSession) {
        abortStream();
        setActiveSession(null);
        setHistory([]);
      }
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function send(text?: string) {
    const q = (text ?? draft).trim();
    if (!q || busy) return;
    let sid = activeSession;
    if (!sid) {
      const r = await api.createSession();
      sid = r.session_id;
      setActiveSession(sid);
    }
    setDraft("");
    setBusy(true);
    setError(null);

    abortStream();
    const controller = new AbortController();
    streamRef.current = controller;
    const turnId =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `t-${Date.now()}`;
    const pending: ChatTurn = {
      id: turnId,
      question: q,
      answer: "…",
      toolCalls: [],
      status: "Thinking…",
    };
    setHistory((h) => [...h, pending]);

    // Patch this turn by id (not by index) so a slow stream never lands on
    // another turn after the user navigates or starts a new chat.
    const patchTurn = (patch: Partial<ChatTurn>) =>
      setHistory((h) => h.map((t) => (t.id === turnId ? { ...t, ...patch } : t)));
    const trace: string[] = [];

    try {
      await api.postChatStream(
        sid,
        q,
        (event, data) => {
          if (event === "tool") {
            const label = humanTool(String(data.name ?? "tool"));
            if (trace[trace.length - 1] !== label) trace.push(label);
            patchTurn({ status: `${label}…` });
          } else if (event === "answer") {
            patchTurn({
              answer: String(data.answer ?? ""),
              toolCalls: (data.tool_calls ?? []) as ChatTurn["toolCalls"],
              status: undefined,
            });
          } else if (event === "error") {
            throw new Error(String(data.detail ?? "stream error"));
          }
        },
        controller.signal
      );
      await refresh();
    } catch (e) {
      if (controller.signal.aborted) return; // cancelled — leave gracefully
      setError(e instanceof Error ? e.message : String(e));
      setHistory((h) => h.filter((t) => t.id !== turnId));
    } finally {
      // Don't clear busy if we were aborted by a newer action that owns it now.
      if (!controller.signal.aborted) setBusy(false);
      if (streamRef.current === controller) streamRef.current = null;
    }
  }

  function stop() {
    abortStream();
    setBusy(false);
    // Finalize whatever turn was streaming.
    setHistory((h) =>
      h.map((t) =>
        t.status
          ? { ...t, status: undefined, answer: t.answer === "…" ? "_(stopped)_" : t.answer }
          : t
      )
    );
  }

  function logout() {
    setToken(null);
    nav("/login");
  }

  const noData =
    counts != null &&
    counts.jira_tickets === 0 &&
    counts.github_prs === 0 &&
    counts.slack_messages === 0;

  return (
    <div className="chat-shell">
      <aside className="sidebar">
        <div className="sidebar-top">
          <AppBrand subtitle="Home" />
        </div>
        <button className="primary block" onClick={() => void newSession()}>
          + New chat
        </button>
        <nav className="side-nav">
          <Link to="/inbox" className="side-nav-item">
            <SparkleIcon className="nav-icon" />
            <span>Proactive</span>
            {gapCount > 0 && <span className="count-badge">{gapCount}</span>}
          </Link>
          <Link to="/trends" className="side-nav-item">
            <TrendsIcon />
            <span>Trends</span>
          </Link>
          {user?.is_admin && (
            <Link to="/settings" className="side-nav-item">
              <SettingsIcon />
              <span>Settings</span>
            </Link>
          )}
        </nav>
        <div className="section-title">Recents</div>
        <ul className="session-list">
          {sessions.map((s) => (
            <li
              key={s.session_id}
              className={s.session_id === activeSession ? "active" : ""}
              role="button"
              tabIndex={0}
              aria-current={s.session_id === activeSession}
              onClick={() => void selectSession(s.session_id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  void selectSession(s.session_id);
                }
              }}
            >
              <div className="title">{s.title || "Untitled chat"}</div>
              <div className="meta">
                {s.turn_count} turn{s.turn_count === 1 ? "" : "s"} · {formatWhen(s.last_active_at)}
              </div>
              <button
                className="del"
                aria-label={`Delete chat "${s.title || "Untitled chat"}"`}
                onClick={(e) => {
                  e.stopPropagation();
                  void deleteSession(s.session_id);
                }}
              >
                ×
              </button>
            </li>
          ))}
          {sessions.length === 0 && (
            <li className="empty">No chats yet — ask yoku anything to begin.</li>
          )}
        </ul>

        {user && (
          <div className="sidebar-bottom">
            <div className="account-card">
              {user.name && user.name !== user.email && (
                <div className="user-name">{user.name}</div>
              )}
              <div className="account-email">{user.email}</div>
              <div className="user-tenant">workspace: {user.tenant_id}</div>
              <div className="account-actions">
                <ThemeToggle />
                <button className="account-button" onClick={logout}>
                  <span>Log out</span>
                </button>
              </div>
            </div>
          </div>
        )}
      </aside>

      <main className="chat-main">
        {error && (
          <div className="banner error" role="alert">
            <span>{error}</span>
            <button
              type="button"
              className="banner-close"
              aria-label="Dismiss error"
              onClick={() => setError(null)}
            >
              ×
            </button>
          </div>
        )}

        {history.length === 0 ? (
          <div className="home-hero">
            <div className="hero-inner">
              <h1 className="hero-greeting">
                <SparkleIcon />
                <span>
                  {timeGreeting()}, {firstNameOf(user)}
                </span>
              </h1>

              <form
                className="hero-composer"
                onSubmit={(e) => {
                  e.preventDefault();
                  void send();
                }}
              >
                <textarea
                  className="hero-input"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="How can I help you today?"
                  disabled={busy}
                  rows={1}
                  autoFocus
                  onKeyDown={(e: KeyboardEvent<HTMLTextAreaElement>) => {
                    if (e.key !== "Enter" || e.shiftKey) return;
                    if (e.nativeEvent.isComposing) return;
                    e.preventDefault();
                    void send();
                  }}
                  aria-label="Message composer"
                />
                <div className="hero-composer-bar">
                  <span className="hero-sources">JIRA · GitHub · Slack</span>
                  <button
                    type="submit"
                    className="hero-send"
                    disabled={busy || !draft.trim()}
                    aria-label="Send message"
                  >
                    <SendIcon />
                  </button>
                </div>
              </form>

              {noData ? (
                <div className="first-run">
                  <h2>Let's connect your first source</h2>
                  <p>
                    yoku answers questions across JIRA, GitHub, and Slack — and watches for gaps
                    between them. Connect a source to bring in your team's work.
                  </p>
                  {user?.is_admin ? (
                    <Link to="/settings" className="primary first-run-cta">
                      Connect a source
                    </Link>
                  ) : (
                    <p className="first-run-note">
                      Ask your workspace admin to connect a source, then come back and ask anything.
                    </p>
                  )}
                </div>
              ) : (
                <div className="hero-chips">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s.label}
                      type="button"
                      className="hero-chip"
                      onClick={() => void send(s.prompt)}
                      disabled={busy}
                    >
                      {s.icon}
                      <span>{s.label}</span>
                    </button>
                  ))}
                </div>
              )}

              {counts && !noData && (
                <div className="hero-counts">
                  {(
                    [
                      {
                        key: "jira",
                        count: counts.jira_tickets,
                        label: "JIRA tickets",
                        icon: (
                          <svg viewBox="0 0 24 24" fill="none">
                            <path d="M4 9.2 11.2 2H22l-7.2 7.2H4Z" fill="currentColor" />
                            <path d="M8.2 13.4 15.4 6.2h4.2L12.4 13.4H8.2Z" fill="currentColor" opacity="0.75" />
                            <path d="M2 11.6h10.8L20 18.8H9.2L2 11.6Z" fill="currentColor" opacity="0.55" />
                          </svg>
                        ),
                      },
                      {
                        key: "github",
                        count: counts.github_prs,
                        label: "pull requests",
                        icon: (
                          <svg viewBox="0 0 24 24" fill="none">
                            <path d="M12 3.2a8.8 8.8 0 0 0-2.78 17.15c.44.08.6-.19.6-.43v-1.52c-2.43.53-2.94-1.03-2.94-1.03-.4-1-.97-1.28-.97-1.28-.79-.55.06-.54.06-.54.88.06 1.35.91 1.35.91.78 1.33 2.05.94 2.55.72.08-.57.31-.94.56-1.16-1.94-.22-3.98-.97-3.98-4.3 0-.95.34-1.73.9-2.34-.1-.22-.39-1.11.08-2.32 0 0 .73-.24 2.4.89a8.3 8.3 0 0 1 4.38 0c1.67-1.13 2.4-.89 2.4-.89.47 1.21.18 2.1.09 2.32.56.61.9 1.39.9 2.34 0 3.34-2.05 4.07-4 4.29.31.26.59.78.59 1.58v2.34c0 .24.16.52.6.43A8.8 8.8 0 0 0 12 3.2Z" fill="currentColor" />
                          </svg>
                        ),
                      },
                      {
                        key: "slack",
                        count: counts.slack_messages,
                        label: "Slack messages",
                        icon: (
                          <svg viewBox="0 0 24 24" fill="none">
                            <path
                              d="M9.6 4 7.8 20M16.4 4l-1.8 16M4.2 9.2h16M3.8 14.8h16"
                              stroke="currentColor"
                              strokeWidth="2"
                              strokeLinecap="round"
                            />
                          </svg>
                        ),
                      },
                    ] as const
                  ).map(({ key, count, label, icon }) => {
                    const f = freshness.find((x) => x.source === key);
                    return (
                      <div key={key} className={`hero-count-item hero-count-item--${key}`}>
                        <span className={`hero-count-icon hero-count-icon--${key}`} aria-hidden="true">
                          {icon}
                        </span>
                        <strong>{count.toLocaleString()}</strong>
                        <span>{label}</span>
                        <div className="hero-count-footer">
                          {f && (
                            <span className={`hero-count-freshness${f.last_sync_status === "error" ? " stale" : ""}`}>
                              {f.synced_ago === "never" ? "never synced" : f.synced_ago}
                            </span>
                          )}
                          <button
                            className={`hero-sync-btn${syncing.has(key) ? " spinning" : ""}`}
                            onClick={() => void triggerSync(key)}
                            disabled={syncing.has(key)}
                            aria-label={`Sync ${key}`}
                            title="Sync now"
                          >
                            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                              <path d="M4 12a8 8 0 0 1 14.93-4H16v2h6V4h-2v2.1A10 10 0 1 0 22 12h-2a8 8 0 0 1-16 0Z" fill="currentColor"/>
                            </svg>
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        ) : (
          <>
            <div className="messages">
              {history.map((turn) => (
            <div key={turn.id} className="turn">
              <div className="msg user">{turn.question}</div>
              {turn.toolCalls.length > 0 && (
                <details className="tool-trace">
                  <summary>
                    {turn.toolCalls.length} tool call
                    {turn.toolCalls.length === 1 ? "" : "s"}
                  </summary>
                  <ul>
                    {turn.toolCalls.map((tc, j) => (
                      <li key={j}>
                        <code>
                          {tc.name}({summarizeArgs(tc.args)})
                        </code>
                        {tc.result_preview && (
                          <pre>{tc.result_preview}</pre>
                        )}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <div className="msg ai">
                {turn.status ? (
                  <div className="turn-status" role="status" aria-live="polite">
                    <span className="status-dots" aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                    <span>{turn.status}</span>
                  </div>
                ) : (
                  <AnswerContent text={turn.answer} jiraBase={user?.jira_base_url ?? null} />
                )}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          <div className="composer-inner">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={busy ? "Agent thinking…" : "Ask about JIRA tickets or GitHub PRs…"}
              disabled={busy}
              onKeyDown={(e: KeyboardEvent<HTMLTextAreaElement>) => {
                if (e.key !== "Enter" || e.shiftKey) return;
                if (e.nativeEvent.isComposing) return;
                e.preventDefault();
                void send();
              }}
              rows={3}
              aria-label="Message composer"
            />
            <div className="composer-actions">
              {busy ? (
                <button
                  type="button"
                  className="send-button stop-button"
                  onClick={stop}
                  aria-label="Stop generating"
                >
                  <span className="stop-glyph" aria-hidden="true" />
                  <span>Stop</span>
                </button>
              ) : (
                <button className="primary send-button" type="submit" disabled={!draft.trim()}>
                  <SendIcon />
                  <span>Send</span>
                </button>
              )}
            </div>
          </div>
        </form>
          </>
        )}
      </main>
    </div>
  );
}

function summarizeArgs(args: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(args)) {
    const s = typeof v === "string" ? `'${v.slice(0, 40)}'` : JSON.stringify(v);
    parts.push(`${k}=${s}`);
  }
  return parts.join(", ").slice(0, 120);
}

function messagesToTurns(messages: PersistedMessage[]): ChatTurn[] {
  // Group by turn_seq, pull human as question + final ai as answer + any tool calls between.
  const byTurn = new Map<number, PersistedMessage[]>();
  for (const m of messages) {
    if (!byTurn.has(m.turn_seq)) byTurn.set(m.turn_seq, []);
    byTurn.get(m.turn_seq)!.push(m);
  }
  const turns: ChatTurn[] = [];
  for (const [, msgs] of [...byTurn.entries()].sort((a, b) => a[0] - b[0])) {
    const human = msgs.find((m) => m.role === "human");
    const finalAi = [...msgs].reverse().find((m) => m.role === "ai" && !m.tool_calls?.length);
    const aiWithCalls = msgs.filter((m) => m.role === "ai" && m.tool_calls?.length);
    const toolMessages = msgs.filter((m) => m.role === "tool");

    const toolCalls = aiWithCalls.flatMap((m) =>
      (m.tool_calls || []).map((c) => {
        const result = toolMessages.find((t) => t.tool_call_id === c.id);
        const preview =
          typeof result?.content === "string"
            ? result.content.slice(0, 400)
            : JSON.stringify(result?.content).slice(0, 400);
        return { name: c.name, args: c.args, result_preview: preview };
      })
    );

    turns.push({
      id: `turn-${msgs[0]?.turn_seq ?? turns.length}`,
      question: human ? extractText(human.content) : "",
      answer: finalAi ? extractText(finalAi.content) : "",
      toolCalls,
    });
  }
  return turns;
}
