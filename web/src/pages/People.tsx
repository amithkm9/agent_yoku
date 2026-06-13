import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, PersonMemory, PersonSummary, User } from "../lib/api";
import { AppHeader, SparkleIcon } from "../components/AppChrome";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase() || "?";
}

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function shortKey(key: string | null): string {
  return (key || "").replace(/^(jira|github|slack)\//, "");
}

const EPISODE_META: Record<string, { icon: string; verb: string }> = {
  sent: { icon: "✉", verb: "yoku nudged" },
  reply: { icon: "💬", verb: "replied" },
  followup: { icon: "🔔", verb: "yoku followed up" },
  confirm: { icon: "✓", verb: "confirmed a gap" },
  dismissal: { icon: "✕", verb: "dismissed a gap" },
};

function detectorLabel(d: string | null): string {
  return (d || "").replace(/_/g, " ");
}

export function People() {
  const nav = useNavigate();
  const { id } = useParams<{ id: string }>();
  const [user, setUser] = useState<User | null>(null);
  const [people, setPeople] = useState<PersonSummary[] | null>(null);
  const [person, setPerson] = useState<PersonMemory | null>(null);
  const [loadingPerson, setLoadingPerson] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Agent Yoku — People";
    api.me().then(setUser).catch(() => nav("/login"));
    api
      .listPeople()
      .then((r) => setPeople(r.people))
      .catch((e) => {
        setPeople([]);
        setError(e instanceof Error ? e.message : String(e));
      });
  }, [nav]);

  // Auto-select the first person when none is in the URL.
  useEffect(() => {
    if (!id && people && people.length > 0) {
      nav(`/people/${people[0].user_id}`, { replace: true });
    }
  }, [id, people, nav]);

  useEffect(() => {
    if (!id) {
      setPerson(null);
      return;
    }
    setLoadingPerson(true);
    api
      .getPerson(id)
      .then(setPerson)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoadingPerson(false));
  }, [id]);

  if (!user) return null;

  return (
    <div className="settings-shell">
      <AppHeader user={user} subtitle="People" />
      <main className="settings-main people-main">
        {error && (
          <div className="banner error" role="alert">
            <span>{error}</span>
            <button className="banner-close" aria-label="Dismiss error" onClick={() => setError(null)}>
              ×
            </button>
          </div>
        )}

        <div className="people-intro">
          <h2>What yoku knows</h2>
          <p className="muted">
            Everyone yoku has learned about — the beliefs it formed, what they promised, and the
            history behind it.
          </p>
        </div>

        {people === null ? (
          <div className="people-empty">
            <div className="loading-dots" aria-label="Loading">
              <i />
              <i />
              <i />
            </div>
          </div>
        ) : people.length === 0 ? (
          <div className="people-empty">
            <SparkleIcon className="people-empty-spark" />
            <h3>No memory yet</h3>
            <p className="muted">
              As yoku flags gaps and people reply, what it learns about them shows up here.
            </p>
          </div>
        ) : (
          <div className="people-grid">
            <aside className="people-list" aria-label="People">
              {people.map((p) => (
                <button
                  key={p.user_id}
                  className={`person-row${p.user_id === id ? " active" : ""}`}
                  onClick={() => nav(`/people/${p.user_id}`)}
                  aria-current={p.user_id === id}
                >
                  <span className="person-avatar">{initials(p.name)}</span>
                  <span className="person-row-body">
                    <span className="person-row-name">{p.name}</span>
                    <span className="person-row-meta">
                      {p.belief_count} belief{p.belief_count === 1 ? "" : "s"}
                      {p.open_commitments > 0 && ` · ${p.open_commitments} owed`}
                      {p.last_interaction_at && ` · ${timeAgo(p.last_interaction_at)}`}
                    </span>
                  </span>
                </button>
              ))}
            </aside>

            <section className="person-detail">
              {loadingPerson || !person ? (
                <div className="people-empty">
                  <div className="loading-dots" aria-label="Loading">
                    <i />
                    <i />
                    <i />
                  </div>
                </div>
              ) : (
                <>
                  <header className="person-head">
                    <span className="person-avatar lg">{initials(person.name)}</span>
                    <div>
                      <h3>{person.name}</h3>
                      {person.email && <div className="muted">{person.email}</div>}
                    </div>
                  </header>

                  <div className="person-section">
                    <div className="person-section-title">🧠 What yoku has learned</div>
                    {person.beliefs.length === 0 ? (
                      <p className="muted person-none">Nothing yet — no patterns have emerged.</p>
                    ) : (
                      <ul className="belief-list">
                        {person.beliefs.map((b, i) => (
                          <li key={i} className="belief-item">
                            <div className="belief-claim">“{b.claim}”</div>
                            <div className="belief-foot">
                              <span className="belief-bar" aria-hidden="true">
                                <span style={{ width: `${Math.round(b.confidence * 100)}%` }} />
                              </span>
                              <span className="belief-conf">
                                {Math.round(b.confidence * 100)}% sure
                              </span>
                              <span className="belief-pattern">{detectorLabel(b.pattern)}</span>
                              <span className="muted">· {b.evidence_count} signals</span>
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <div className="person-section">
                    <div className="person-section-title">🤝 On the hook for</div>
                    {person.commitments.length === 0 ? (
                      <p className="muted person-none">No open commitments.</p>
                    ) : (
                      <ul className="commitment-list">
                        {person.commitments.map((c, i) => (
                          <li key={i} className="commitment-item">
                            <span className="commitment-text">{c.text}</span>
                            <span className="commitment-meta muted">
                              <code className="person-key">{shortKey(c.item_key)}</code>
                              {c.due && ` · due ${c.due}`}
                              {c.followups ? ` · chased ${c.followups}×` : ""}
                            </span>
                            <span className={`commitment-status status-${c.status}`}>{c.status}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <div className="person-section">
                    <div className="person-section-title">🕑 Timeline</div>
                    {person.episodes.length === 0 ? (
                      <p className="muted person-none">No interactions recorded.</p>
                    ) : (
                      <ul className="timeline">
                        {person.episodes.map((e, i) => {
                          const meta = EPISODE_META[e.kind] || { icon: "•", verb: e.kind };
                          return (
                            <li key={i} className="timeline-item">
                              <span className={`timeline-icon kind-${e.kind}`}>{meta.icon}</span>
                              <div className="timeline-body">
                                <div className="timeline-text">{e.text}</div>
                                <div className="timeline-meta muted">
                                  {meta.verb}
                                  {e.item_key && (
                                    <>
                                      {" · "}
                                      <code className="person-key">{shortKey(e.item_key)}</code>
                                    </>
                                  )}
                                  {e.ts && ` · ${timeAgo(e.ts)}`}
                                </div>
                              </div>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </div>
                </>
              )}
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
