import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Signal, User } from "../lib/api";
import { AppBrand, SparkleIcon } from "../components/AppChrome";

// Human label per detector — what the gap *means*, not its internal name.
const DETECTOR_LABELS: Record<string, string> = {
  done_no_pr: "Done, no PR",
  merged_no_ticket: "Merged, no ticket",
};

function itemHref(signal: Signal, jiraBase: string | null): string | null {
  if (signal.url) return signal.url;
  const [source, key] = signal.item_key.split("/", 2);
  if (source === "jira" && jiraBase) return `${jiraBase}/browse/${key}`;
  if (source === "github") {
    const pr = signal.item_key.replace("github/", "").match(/^(.+)#(\d+)$/);
    if (pr) return `https://github.com/${pr[1]}/pull/${pr[2]}`;
  }
  return null;
}

function shortKey(itemKey: string): string {
  return itemKey.replace(/^(jira|github|slack)\//, "");
}

function daysAgo(iso: string | null): string {
  if (!iso) return "";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return "today";
  return days === 1 ? "1 day" : `${days} days`;
}

export function Inbox() {
  const nav = useNavigate();
  const [user, setUser] = useState<User | null>(null);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [totals, setTotals] = useState<{ open: number; matured: number }>({
    open: 0,
    matured: 0,
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await api.listInbox();
      setSignals(r.signals);
      setTotals({ open: r.total_open, matured: r.total_matured });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    api.me().then(setUser).catch(() => nav("/login"));
    void refresh();
  }, [nav, refresh]);

  async function verdict(signal: Signal, action: "confirm" | "dismiss") {
    setBusy(signal.signal_id);
    setError(null);
    try {
      if (action === "confirm") await api.confirmSignal(signal.signal_id);
      else await api.dismissSignal(signal.signal_id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (!user) return null;

  return (
    <div className="settings-shell">
      <header className="settings-header">
        <AppBrand subtitle="Proactive" />
        <nav>
          <Link to="/" className="nav-pill">
            <span>Home</span>
          </Link>
        </nav>
        <div className="who">
          <div className="user-name">{user.name || user.email}</div>
          <div className="user-tenant">tenant: {user.tenant_id}</div>
        </div>
      </header>

      <main className="settings-main">
        {error && (
          <div className="banner error" onClick={() => setError(null)}>
            {error}
          </div>
        )}

        <div className="inbox-intro">
          <h2>
            <SparkleIcon /> Gaps worth a look
          </h2>
          <p className="muted">
            Cross-source drift yoku noticed after the last sync — {totals.matured} matured of{" "}
            {totals.open} open. Confirm real gaps, dismiss noise: every click teaches yoku what
            deserves attention.
          </p>
        </div>

        {signals.length === 0 ? (
          <p className="muted">Nothing needs attention — the layers line up.</p>
        ) : (
          <ul className="signal-list">
            {signals.map((s) => {
              const href = itemHref(s, user.jira_base_url);
              return (
                <li key={s.signal_id} className="signal-row">
                  <div className="signal-body">
                    <div className="signal-top">
                      <span className={`signal-chip signal-chip--${s.detector}`}>
                        {DETECTOR_LABELS[s.detector] || s.detector}
                      </span>
                      {href ? (
                        <a className="citation" href={href} target="_blank" rel="noreferrer">
                          {shortKey(s.item_key)}
                        </a>
                      ) : (
                        <code>{shortKey(s.item_key)}</code>
                      )}
                      {s.label === "confirmed" && (
                        <span className="signal-chip signal-chip--confirmed">confirmed</span>
                      )}
                    </div>
                    <div className="signal-title">{s.title || "(no title)"}</div>
                    <div className="signal-meta muted">
                      {s.person_name ? `${s.person_name} · ` : ""}
                      {String(s.evidence.status ?? s.evidence.repo ?? "")}
                      {" · gap open "}
                      {daysAgo(s.matured_at) || "today"}
                    </div>
                  </div>
                  <div className="signal-actions">
                    <button
                      className="signal-btn confirm"
                      disabled={busy === s.signal_id || s.label === "confirmed"}
                      onClick={() => void verdict(s, "confirm")}
                      title="Real gap — worth acting on"
                    >
                      Confirm
                    </button>
                    <button
                      className="signal-btn dismiss"
                      disabled={busy === s.signal_id}
                      onClick={() => void verdict(s, "dismiss")}
                      title="Not a real gap — never show this item again"
                    >
                      Dismiss
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </main>
    </div>
  );
}
