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
import { AppBrand, SendIcon, SettingsIcon, SparkleIcon } from "../components/AppChrome";

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
  question: string;
  answer: string;
  toolCalls: ChatResponse["tool_calls"];
  status?: string; // live progress while streaming (cleared once the answer lands)
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
// every claim is one click from its source. JIRA_BASE matches the default
// Asato Atlassian site; change here if a tenant uses a different host.
const JIRA_BASE = "https://asato-ai.atlassian.net";
const CITATION_RE = /\bAS-\d+\b|\b[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+#\d+\b/g;

function citationHref(token: string): string | null {
  if (/^AS-\d+$/.test(token)) return `${JIRA_BASE}/browse/${token}`;
  const pr = token.match(/^([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)#(\d+)$/);
  if (pr) return `https://github.com/${pr[1]}/pull/${pr[2]}`;
  return null;
}

function linkifyCitations(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let tokenIndex = 0;

  for (const match of text.matchAll(CITATION_RE)) {
    const token = match[0];
    const href = citationHref(token);
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

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*.+?\*\*|`.+?`|\*[^*\n]+\*)/g;
  let lastIndex = 0;
  let tokenIndex = 0;

  for (const match of text.matchAll(pattern)) {
    const [token] = match;
    const start = match.index ?? 0;

    if (start > lastIndex) {
      nodes.push(
        ...linkifyCitations(text.slice(lastIndex, start), `${keyPrefix}-pre-${tokenIndex}`)
      );
    }

    if (token.startsWith("**")) {
      nodes.push(
        <strong key={`${keyPrefix}-strong-${tokenIndex}`}>
          {linkifyCitations(token.slice(2, -2), `${keyPrefix}-strong-${tokenIndex}`)}
        </strong>
      );
    } else if (token.startsWith("`")) {
      nodes.push(
        <code key={`${keyPrefix}-code-${tokenIndex}`}>{token.slice(1, -1)}</code>
      );
    } else {
      nodes.push(
        <em key={`${keyPrefix}-em-${tokenIndex}`}>
          {linkifyCitations(token.slice(1, -1), `${keyPrefix}-em-${tokenIndex}`)}
        </em>
      );
    }

    lastIndex = start + token.length;
    tokenIndex += 1;
  }

  if (lastIndex < text.length) {
    nodes.push(...linkifyCitations(text.slice(lastIndex), `${keyPrefix}-tail`));
  }

  return nodes;
}

function AnswerContent({ text }: { text: string }) {
  const blocks = parseAnswerBlocks(text);

  if (blocks.length === 0) {
    return <p>{text}</p>;
  }

  return (
    <div className="rich-text">
      {blocks.map((block, index) => {
        if (block.type === "paragraph") {
          return <p key={index}>{renderInline(block.text, `p-${index}`)}</p>;
        }

        if (block.type === "ol") {
          return (
            <ol key={index}>
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>
                  {renderInline(item, `ol-${index}-${itemIndex}`)}
                </li>
              ))}
            </ol>
          );
        }

        return (
          <ul key={index}>
            {block.items.map((item, itemIndex) => (
              <li key={itemIndex}>
                {renderInline(item, `ul-${index}-${itemIndex}`)}
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

// Quick-start prompts tailored to what agent_yoku actually knows about:
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
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch(() => nav("/login"));
    void refresh();
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
    setActiveSession(id);
    try {
      const detail = await api.getSession(id);
      setHistory(messagesToTurns(detail.messages));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function newSession() {
    const r = await api.createSession();
    await refresh();
    selectSession(r.session_id);
    setHistory([]);
  }

  async function deleteSession(id: string) {
    if (!confirm("Delete this session?")) return;
    await api.deleteSession(id);
    if (id === activeSession) {
      setActiveSession(null);
      setHistory([]);
    }
    await refresh();
  }

  async function send(text?: string) {
    const q = (text ?? draft).trim();
    if (!q) return;
    let sid = activeSession;
    if (!sid) {
      const r = await api.createSession();
      sid = r.session_id;
      setActiveSession(sid);
    }
    setDraft("");
    setBusy(true);
    setError(null);
    const pending: ChatTurn = { question: q, answer: "…", toolCalls: [], status: "thinking…" };
    setHistory((h) => [...h, pending]);

    const patchLast = (patch: Partial<ChatTurn>) =>
      setHistory((h) => h.map((t, i) => (i === h.length - 1 ? { ...t, ...patch } : t)));

    try {
      await api.postChatStream(sid, q, (event, data) => {
        if (event === "tool") {
          patchLast({ status: `running ${String(data.name ?? "tool")}…` });
        } else if (event === "answer") {
          patchLast({
            answer: String(data.answer ?? ""),
            toolCalls: (data.tool_calls ?? []) as ChatTurn["toolCalls"],
            status: undefined,
          });
        } else if (event === "error") {
          throw new Error(String(data.detail ?? "stream error"));
        }
      });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setHistory((h) => h.slice(0, -1));
    } finally {
      setBusy(false);
    }
  }

  function logout() {
    setToken(null);
    nav("/login");
  }

  return (
    <div className="chat-shell">
      <aside className="sidebar">
        <div className="sidebar-top">
          <AppBrand subtitle="Home" />
        </div>
        <button className="primary block" onClick={() => void newSession()}>
          + New chat
        </button>
        {freshness.length > 0 && (
          <div className="freshness">
            {freshness.map((f) => (
              <span
                key={f.source}
                className={`fresh-badge${f.last_sync_status === "error" ? " stale" : ""}`}
                title={f.last_synced_at ? `last synced ${f.last_synced_at}` : "never synced"}
              >
                {f.source === "jira" ? "JIRA" : f.source === "github" ? "GitHub" : "Slack"}{" "}
                {f.synced_ago === "never" ? "not synced" : `· ${f.synced_ago}`}
              </span>
            ))}
          </div>
        )}
        <div className="section-title">Recents</div>
        <ul className="session-list">
          {sessions.map((s) => (
            <li
              key={s.session_id}
              className={s.session_id === activeSession ? "active" : ""}
              onClick={() => void selectSession(s.session_id)}
            >
              <div className="title">{s.title || "Untitled chat"}</div>
              <div className="meta">
                {s.turn_count} turn{s.turn_count === 1 ? "" : "s"} ·{" "}
                {new Date(s.last_active_at).toLocaleString()}
              </div>
              <button
                className="del"
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
            <li className="empty">No sessions yet</li>
          )}
        </ul>

        {user && (
          <div className="sidebar-bottom">
            <div className="account-card">
              <div className="user-name">{user.name || user.email}</div>
              <div className="account-email">{user.email}</div>
              <div className="user-tenant">tenant: {user.tenant_id}</div>
              <div className="account-actions">
                <Link to="/settings" className="account-button" aria-label="Open settings">
                  <SettingsIcon />
                  <span>Settings</span>
                </Link>
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
          <div className="banner error" onClick={() => setError(null)}>
            {error}
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

              {counts && (
                <div className="hero-counts">
                  <div className="hero-count-item">
                    <span className="hero-count-icon hero-count-icon--jira" aria-hidden="true">
                      <svg viewBox="0 0 24 24" fill="none">
                        <path d="M4 9.2 11.2 2H22l-7.2 7.2H4Z" fill="currentColor" />
                        <path d="M8.2 13.4 15.4 6.2h4.2L12.4 13.4H8.2Z" fill="currentColor" opacity="0.75" />
                        <path d="M2 11.6h10.8L20 18.8H9.2L2 11.6Z" fill="currentColor" opacity="0.55" />
                      </svg>
                    </span>
                    <strong>{counts.jira_tickets.toLocaleString()}</strong>
                    <span>JIRA tickets</span>
                  </div>
                  <div className="hero-count-item">
                    <span className="hero-count-icon hero-count-icon--github" aria-hidden="true">
                      <svg viewBox="0 0 24 24" fill="none">
                        <path d="M12 3.2a8.8 8.8 0 0 0-2.78 17.15c.44.08.6-.19.6-.43v-1.52c-2.43.53-2.94-1.03-2.94-1.03-.4-1-.97-1.28-.97-1.28-.79-.55.06-.54.06-.54.88.06 1.35.91 1.35.91.78 1.33 2.05.94 2.55.72.08-.57.31-.94.56-1.16-1.94-.22-3.98-.97-3.98-4.3 0-.95.34-1.73.9-2.34-.1-.22-.39-1.11.08-2.32 0 0 .73-.24 2.4.89a8.3 8.3 0 0 1 4.38 0c1.67-1.13 2.4-.89 2.4-.89.47 1.21.18 2.1.09 2.32.56.61.9 1.39.9 2.34 0 3.34-2.05 4.07-4 4.29.31.26.59.78.59 1.58v2.34c0 .24.16.52.6.43A8.8 8.8 0 0 0 12 3.2Z" fill="currentColor" />
                      </svg>
                    </span>
                    <strong>{counts.github_prs.toLocaleString()}</strong>
                    <span>pull requests</span>
                  </div>
                  <div className="hero-count-item">
                    <span className="hero-count-icon hero-count-icon--slack" aria-hidden="true">
                      <svg viewBox="0 0 24 24" fill="none">
                        <path d="M9 3a2 2 0 1 0 0 4h2V3H9Z" fill="currentColor" opacity="0.9"/>
                        <path d="M3 9a2 2 0 1 0 4 0V7H3v2Z" fill="currentColor" opacity="0.7"/>
                        <path d="M15 21a2 2 0 1 0 0-4h-2v4h2Z" fill="currentColor" opacity="0.9"/>
                        <path d="M21 15a2 2 0 1 0-4 0v2h4v-2Z" fill="currentColor" opacity="0.7"/>
                        <path d="M3 15a2 2 0 1 0 4 0v-2H3v2Z" fill="currentColor" opacity="0.6"/>
                        <path d="M9 21a2 2 0 1 0 0-4H7v4h2Z" fill="currentColor" opacity="0.8"/>
                        <path d="M21 9a2 2 0 1 0-4 0v2h4V9Z" fill="currentColor" opacity="0.6"/>
                        <path d="M15 3a2 2 0 1 0 0 4h2V3h-2Z" fill="currentColor" opacity="0.8"/>
                      </svg>
                    </span>
                    <strong>{counts.slack_messages.toLocaleString()}</strong>
                    <span>Slack messages</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : (
          <>
            <div className="messages">
              {history.map((turn, i) => (
            <div key={i} className="turn">
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
                  <div className="turn-status">{turn.status}</div>
                ) : (
                  <AnswerContent text={turn.answer} />
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
              <button className="primary send-button" type="submit" disabled={busy || !draft.trim()}>
                <SendIcon />
                <span>Send</span>
              </button>
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
      question: human ? extractText(human.content) : "",
      answer: finalAi ? extractText(finalAi.content) : "",
      toolCalls,
    });
  }
  return turns;
}
