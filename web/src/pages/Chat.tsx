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
import { AppBrand, SendIcon, SettingsIcon } from "../components/AppChrome";

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
    refresh();
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
    refresh();
  }

  async function send() {
    if (!draft.trim()) return;
    let sid = activeSession;
    if (!sid) {
      const r = await api.createSession();
      sid = r.session_id;
      setActiveSession(sid);
    }
    const q = draft.trim();
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
      refresh();
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
        <button className="primary block" onClick={newSession}>
          + New session
        </button>
        {counts && (
          <div className="counts">
            <div>
              <span>JIRA</span>
              <strong>{counts.jira_tickets.toLocaleString()}</strong>
            </div>
            <div>
              <span>PRs</span>
              <strong>{counts.github_prs.toLocaleString()}</strong>
            </div>
          </div>
        )}
        {freshness.length > 0 && (
          <div className="freshness">
            {freshness.map((f) => (
              <span
                key={f.source}
                className={`fresh-badge${f.last_sync_status === "error" ? " stale" : ""}`}
                title={f.last_synced_at ? `last synced ${f.last_synced_at}` : "never synced"}
              >
                {f.source === "jira" ? "JIRA" : "GitHub"}{" "}
                {f.synced_ago === "never" ? "not synced" : `· ${f.synced_ago}`}
              </span>
            ))}
          </div>
        )}
        <div className="section-title">Sessions</div>
        <ul className="session-list">
          {sessions.map((s) => (
            <li
              key={s.session_id}
              className={s.session_id === activeSession ? "active" : ""}
              onClick={() => selectSession(s.session_id)}
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
                  deleteSession(s.session_id);
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
        <div className="messages">
          {history.length === 0 && (
            <div className="empty">Ask anything about Asato JIRA tickets + GitHub PRs.</div>
          )}
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
