import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, TrendPoint, TrendsResponse, User } from "../lib/api";
import { AppHeader } from "../components/AppChrome";

const W = 360;
const H = 96;
const PAD = 4;

function shortWeek(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString([], {
    month: "short",
    day: "numeric",
  });
}

/** Fill a sparse series against the weeks axis (0 for missing count weeks). */
function filled(weeks: string[], points: TrendPoint[] | undefined): TrendPoint[] {
  const byWeek = new Map((points ?? []).map((p) => [p.week, p]));
  return weeks.map((w) => byWeek.get(w) ?? { week: w, value: 0, n: null });
}

function BarChart({ weeks, points }: { weeks: string[]; points: TrendPoint[] | undefined }) {
  const data = filled(weeks, points);
  const max = Math.max(1, ...data.map((p) => p.value));
  const step = (W - PAD * 2) / data.length;
  const barW = Math.max(4, step - 5);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="trend-svg" role="img">
      {data.map((p, i) => {
        const h = Math.max(p.value > 0 ? 3 : 1, (p.value / max) * (H - 18));
        const x = PAD + i * step + (step - barW) / 2;
        const last = i === data.length - 1;
        return (
          <rect
            key={p.week}
            x={x}
            y={H - 12 - h}
            width={barW}
            height={h}
            rx={2.5}
            className={`trend-bar${last ? " last" : ""}${p.value === 0 ? " zero" : ""}`}
          >
            <title>{`${shortWeek(p.week)}: ${p.value}`}</title>
          </rect>
        );
      })}
      <line x1={PAD} y1={H - 11.5} x2={W - PAD} y2={H - 11.5} className="trend-axis" />
    </svg>
  );
}

function LineChart({ weeks, points }: { weeks: string[]; points: TrendPoint[] | undefined }) {
  const data = (points ?? []).filter((p) => weeks.includes(p.week));
  if (data.length === 0) return <div className="trend-nodata">No data in this window</div>;
  const max = Math.max(1, ...data.map((p) => p.value));
  const step = (W - PAD * 2) / weeks.length;
  const x = (week: string) => PAD + weeks.indexOf(week) * step + step / 2;
  const y = (v: number) => H - 14 - (v / max) * (H - 26);
  const path = data.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.week)},${y(p.value)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="trend-svg" role="img">
      <path d={path} className="trend-line" />
      {data.map((p) => (
        <circle key={p.week} cx={x(p.week)} cy={y(p.value)} r={3} className="trend-dot">
          <title>{`${shortWeek(p.week)}: ${p.value} days (n=${p.n ?? "?"})`}</title>
        </circle>
      ))}
      <line x1={PAD} y1={H - 11.5} x2={W - PAD} y2={H - 11.5} className="trend-axis" />
    </svg>
  );
}

function TrendCard({
  title,
  hint,
  weeks,
  points,
  kind,
  format,
}: {
  title: string;
  hint: string;
  weeks: string[];
  points: TrendPoint[] | undefined;
  kind: "bar" | "line";
  format?: (v: number) => string;
}) {
  const data = filled(weeks, points);
  const latestIdx = data.length - 1;
  // Latest week is usually partial — headline the most recent non-zero value.
  let headIdx = latestIdx;
  while (headIdx > 0 && data[headIdx].value === 0) headIdx -= 1;
  const head = data[headIdx];
  const prev = data[headIdx - 1];
  const delta = prev && prev.value > 0 ? ((head.value - prev.value) / prev.value) * 100 : null;
  const fmt = format ?? ((v: number) => v.toLocaleString());

  return (
    <section className="trend-card">
      <header>
        <div>
          <h3>{title}</h3>
          <p className="muted">{hint}</p>
        </div>
        <div className="trend-headline">
          <strong>{fmt(head.value)}</strong>
          <span className="muted">wk of {shortWeek(head.week)}</span>
          {delta != null && Math.abs(delta) >= 1 && (
            <span className={`trend-delta ${delta >= 0 ? "up" : "down"}`}>
              {delta > 0 ? "+" : ""}
              {Math.round(delta)}%
            </span>
          )}
        </div>
      </header>
      {kind === "bar" ? (
        <BarChart weeks={weeks} points={points} />
      ) : (
        <LineChart weeks={weeks} points={points} />
      )}
      <footer className="trend-range muted">
        <span>{shortWeek(weeks[0])}</span>
        <span>{shortWeek(weeks[weeks.length - 1])}</span>
      </footer>
    </section>
  );
}

export function Trends() {
  const nav = useNavigate();
  const [user, setUser] = useState<User | null>(null);
  const [data, setData] = useState<TrendsResponse | null>(null);
  const [weeksBack, setWeeksBack] = useState(12);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Agent Yoku — Trends";
    api.me().then(setUser).catch(() => nav("/login"));
  }, [nav]);

  useEffect(() => {
    setData(null);
    api
      .trends(weeksBack)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [weeksBack]);

  const hasTicketsDone = useMemo(
    () => (data?.series.tickets_done ?? []).some((p) => p.value > 0),
    [data]
  );

  if (!user) return null;

  return (
    <div className="settings-shell">
      <AppHeader user={user} subtitle="Trends" />
      <main className="settings-main trends-main">
        {error && (
          <div className="banner error" onClick={() => setError(null)}>
            {error}
          </div>
        )}

        <div className="trends-intro">
          <div>
            <h2>Engineering trends</h2>
            <p className="muted">
              Weekly movement across tickets, PRs, and proactive gaps — recomputed after
              every sync. Ask the same questions in chat: “how has merge velocity changed?”
            </p>
          </div>
          <label className="trends-window">
            <span className="muted">Window</span>
            <select value={weeksBack} onChange={(e) => setWeeksBack(Number(e.target.value))}>
              <option value={12}>12 weeks</option>
              <option value={26}>26 weeks</option>
            </select>
          </label>
        </div>

        {data === null ? (
          <div className="inbox-empty">
            <div className="loading-dots" aria-label="Loading">
              <span />
              <span />
              <span />
            </div>
            <p className="muted">Crunching the weeks…</p>
          </div>
        ) : data.weeks.length === 0 ? (
          <div className="inbox-empty">
            <h3>No metrics yet</h3>
            <p className="muted">Trends appear after the first data sync completes.</p>
          </div>
        ) : (
          <div className="trends-grid">
            <TrendCard
              title="PRs merged"
              hint="Merge velocity per week"
              weeks={data.weeks}
              points={data.series.prs_merged}
              kind="bar"
            />
            <TrendCard
              title="PRs opened"
              hint="New pull requests per week"
              weeks={data.weeks}
              points={data.series.prs_opened}
              kind="bar"
            />
            <TrendCard
              title="PR cycle time (median)"
              hint="Days from open to merge — automation merges dominate the median"
              weeks={data.weeks}
              points={data.series.pr_cycle_time_days}
              kind="line"
              format={(v) => `${v}d`}
            />
            <TrendCard
              title="PR cycle time (p90)"
              hint="90% of PRs merge faster than this — the human-paced tail"
              weeks={data.weeks}
              points={data.series.pr_cycle_time_p90_days}
              kind="line"
              format={(v) => `${v}d`}
            />
            <TrendCard
              title="Tickets created"
              hint="New JIRA tickets per week"
              weeks={data.weeks}
              points={data.series.tickets_created}
              kind="bar"
            />
            {hasTicketsDone && (
              <TrendCard
                title="Tickets completed"
                hint="Status moved to a done state (from event capture onward)"
                weeks={data.weeks}
                points={data.series.tickets_done}
                kind="bar"
              />
            )}
          </div>
        )}
      </main>
    </div>
  );
}
