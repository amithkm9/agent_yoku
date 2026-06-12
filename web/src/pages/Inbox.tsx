import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Action, api, Signal, User } from "../lib/api";
import { AppHeader, SparkleIcon } from "../components/AppChrome";

// Server sends the label from the Detector spec; this is only the fallback
// for rows written before labels were denormalized.
function detectorLabel(s: Signal): string {
  return s.detector_label || s.detector.replace(/_/g, " ");
}

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

function initials(name: string | null): string {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase() || "?";
}

function gapAge(signal: Signal): string {
  const days = Number(signal.evidence.gap_age_days ?? NaN);
  if (Number.isFinite(days)) {
    if (days <= 0) return "since today";
    return days === 1 ? "for 1 day" : `for ${days} days`;
  }
  return "";
}

export function Inbox() {
  const nav = useNavigate();
  const [user, setUser] = useState<User | null>(null);
  const [signals, setSignals] = useState<Signal[] | null>(null); // null = loading
  const [totals, setTotals] = useState<{ open: number; matured: number; suppressed: number }>({
    open: 0,
    matured: 0,
    suppressed: 0,
  });
  const [filter, setFilter] = useState<string>("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingActions, setPendingActions] = useState<Action[]>([]);

  const refresh = useCallback(async () => {
    try {
      const r = await api.listInbox();
      setSignals(r.signals);
      setTotals({
        open: r.total_open,
        matured: r.total_matured,
        suppressed: r.total_suppressed,
      });
    } catch (e) {
      setSignals([]);
      setError(e instanceof Error ? e.message : String(e));
    }
    try {
      const a = await api.listActions();
      setPendingActions(a.actions.filter((x) => x.status === "proposed"));
    } catch {
      setPendingActions([]); // non-admins simply see no action queue
    }
  }, []);

  useEffect(() => {
    document.title = "Agent Yoku — Proactive";
    api.me().then(setUser).catch(() => nav("/login"));
    void refresh();
  }, [nav, refresh]);

  const detectorCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const s of signals ?? []) counts[s.detector] = (counts[s.detector] || 0) + 1;
    return counts;
  }, [signals]);

  const visible = useMemo(
    () => (signals ?? []).filter((s) => filter === "all" || s.detector === filter),
    [signals, filter]
  );

  async function verdict(signal: Signal, action: "confirm" | "dismiss") {
    setBusy(signal.signal_id);
    setError(null);
    try {
      if (action === "confirm") {
        const updated = await api.confirmSignal(signal.signal_id);
        setSignals((list) =>
          (list ?? []).map((s) => (s.signal_id === signal.signal_id ? updated : s))
        );
      } else {
        await api.dismissSignal(signal.signal_id);
        setSignals((list) => (list ?? []).filter((s) => s.signal_id !== signal.signal_id));
        setTotals((t) => ({
          ...t,
          open: Math.max(0, t.open - 1),
          matured: Math.max(0, t.matured - 1),
        }));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      await refresh(); // fall back to server truth
    } finally {
      setBusy(null);
    }
  }

  async function actOnAction(a: Action, approve: boolean) {
    setBusy(a.action_id);
    setError(null);
    try {
      if (approve) await api.approveAction(a.action_id);
      else await api.rejectAction(a.action_id);
      setPendingActions((list) => list.filter((x) => x.action_id !== a.action_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (!user) return null;

  return (
    <div className="settings-shell">
      <AppHeader user={user} subtitle="Proactive" />

      <main className="settings-main inbox-main">
        {error && (
          <div className="banner error" onClick={() => setError(null)}>
            {error}
          </div>
        )}

        {pendingActions.length > 0 && (
          <section className="actions-pending">
            <h3>Pending actions</h3>
            <p className="muted">
              Write-backs yoku proposed. Approving executes against the real system —
              every outcome is audit-logged.
            </p>
            <ul className="signal-list">
              {pendingActions.map((a) => (
                <li key={a.action_id} className="signal-row">
                  <div className="signal-body">
                    <div className="signal-top">
                      <span className="signal-chip signal-chip--action">
                        {a.action_type.replace(/_/g, " ")}
                      </span>
                      <code className="signal-key">{a.target_key || "(new)"}</code>
                      <span className="muted">via {a.source}</span>
                    </div>
                    <div className="signal-meta muted">
                      {Object.entries(a.payload)
                        .map(([k, v]) => `${k}: ${String(v).slice(0, 80)}`)
                        .join(" · ")}
                    </div>
                  </div>
                  <div className="signal-actions">
                    <button
                      className="signal-btn confirm"
                      disabled={busy === a.action_id || !user.is_admin}
                      title={user.is_admin ? "Execute this action" : "Admins only"}
                      onClick={() => void actOnAction(a, true)}
                    >
                      Approve
                    </button>
                    <button
                      className="signal-btn dismiss"
                      disabled={busy === a.action_id || !user.is_admin}
                      onClick={() => void actOnAction(a, false)}
                    >
                      Reject
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="inbox-intro">
          <h2>Gaps worth a look</h2>
          <p className="muted">
            Cross-source drift yoku noticed after the last sync. Confirm real gaps, dismiss
            noise — every click teaches yoku what deserves attention. Dismissed items never
            come back.
          </p>
        </div>

        {signals !== null && signals.length > 0 && (
          <div className="inbox-filters">
            <button
              className={`filter-chip${filter === "all" ? " active" : ""}`}
              onClick={() => setFilter("all")}
            >
              All <span className="filter-count">{signals.length}</span>
            </button>
            {Object.entries(detectorCounts).map(([det, n]) => (
              <button
                key={det}
                className={`filter-chip${filter === det ? " active" : ""}`}
                onClick={() => setFilter(det)}
              >
                {(signals ?? []).find((s) => s.detector === det)?.detector_label || det}{" "}
                <span className="filter-count">{n}</span>
              </button>
            ))}
          </div>
        )}

        {signals === null ? (
          <div className="inbox-empty">
            <div className="loading-dots" aria-label="Loading">
              <span />
              <span />
              <span />
            </div>
            <p className="muted">Checking for gaps…</p>
          </div>
        ) : visible.length === 0 ? (
          <div className="inbox-empty">
            <SparkleIcon className="inbox-empty-spark" />
            <h3>All clear</h3>
            <p className="muted">
              {signals.length === 0
                ? "Nothing needs attention — tickets, PRs, and conversations line up."
                : "No gaps of this kind right now."}
            </p>
          </div>
        ) : (
          <ul className="signal-list">
            {visible.map((s) => {
              const href = itemHref(s, user.jira_base_url);
              return (
                <li key={s.signal_id} className="signal-row">
                  <span className="signal-avatar" title={s.person_name || "Unassigned"}>
                    {initials(s.person_name)}
                  </span>
                  <div className="signal-body">
                    <div className="signal-top">
                      <span className={`signal-chip signal-chip--${s.detector}`}>
                        {detectorLabel(s)}
                      </span>
                      {href ? (
                        <a className="signal-key" href={href} target="_blank" rel="noreferrer">
                          {shortKey(s.item_key)}
                        </a>
                      ) : (
                        <code className="signal-key">{shortKey(s.item_key)}</code>
                      )}
                      {s.label === "confirmed" && (
                        <span className="signal-chip signal-chip--confirmed">confirmed</span>
                      )}
                      {s.verdict?.real === true && (
                        <span
                          className="signal-chip signal-chip--judged"
                          title={s.verdict.item?.reason || "Judged a real gap"}
                        >
                          ✓ judged real
                        </span>
                      )}
                      {s.status === "shadow" && (
                        <span
                          className="signal-chip signal-chip--shadow"
                          title="Drafted but not sent — shadow mode"
                        >
                          ✉ drafted
                        </span>
                      )}
                      {s.status === "sent" && (
                        <span className="signal-chip signal-chip--sent" title="DM sent">
                          ✉ sent
                        </span>
                      )}
                    </div>
                    <div className="signal-title">{s.title || "(no title)"}</div>
                    {s.proposed_message && (
                      <div className="signal-draft">
                        <span className="signal-draft-label">
                          {s.status === "sent" ? "yoku said:" : "yoku would say:"}
                        </span>
                        “{s.proposed_message}”
                      </div>
                    )}
                    <div className="signal-meta muted">
                      {s.person_name || "Unassigned"}
                      {s.evidence.repo ? ` · ${String(s.evidence.repo)}` : ""}
                      {s.evidence.status ? ` · ${String(s.evidence.status)}` : ""}
                      {gapAge(s) ? ` · gap open ${gapAge(s)}` : ""}
                    </div>
                  </div>
                  <div className="signal-actions">
                    <button
                      className="signal-btn confirm"
                      disabled={busy === s.signal_id || s.label === "confirmed"}
                      onClick={() => void verdict(s, "confirm")}
                      title="Real gap — worth acting on"
                    >
                      {s.label === "confirmed" ? "Confirmed" : "Confirm"}
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

        {signals !== null && signals.length > 0 && (
          <p className="muted inbox-footnote">
            Showing {visible.length} of {totals.matured} matured gaps ({totals.open} open in
            total{totals.suppressed > 0
              ? `; ${totals.suppressed} suppressed as normal workflow or judged not real`
              : ""}
            ). Gaps younger than each detector's maturation window stay hidden until they
            persist.
          </p>
        )}
      </main>
    </div>
  );
}
