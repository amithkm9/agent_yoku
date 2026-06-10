import { FormEvent, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  api,
  ConnectorStatus,
  GithubConfigIn,
  JiraConfigIn,
  SlackConfigIn,
  User,
} from "../lib/api";
import { AppHeader } from "../components/AppChrome";

type EditingName = "jira" | "github" | "slack" | null;

export function Settings() {
  const nav = useNavigate();
  const [user, setUser] = useState<User | null>(null);
  const [connectors, setConnectors] = useState<ConnectorStatus[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditingName>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.listConnectors();
      setConnectors(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    document.title = "Agent Yoku — Settings";
    api.me().then(setUser).catch(() => nav("/login"));
    refresh();
  }, [nav, refresh]);

  const activeConnector =
    editing == null ? null : connectors?.find((connector) => connector.name === editing) || null;

  async function onDelete(name: string) {
    if (!confirm(`Disconnect ${name}? This won't delete already-ingested data.`)) return;
    setBusy(`delete:${name}`);
    setError(null);
    try {
      await api.deleteConnector(name);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function onSync(name: string) {
    setBusy(`sync:${name}`);
    setError(null);
    try {
      await api.syncConnector(name);
      // Sync runs in the background — poll once after a beat so the UI reflects
      // it kicked off; a fuller polling loop is overkill for the MVP.
      setTimeout(refresh, 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (!user) return null;

  if (!user.is_admin) {
    return (
      <div className="settings-shell">
        <AppHeader user={user} subtitle="Tenant settings" />
        <main className="settings-main">
          <p className="muted">
            Only tenant admins can manage connectors. Ask your tenant admin to set this up.
          </p>
        </main>
      </div>
    );
  }

  return (
    <div className="settings-shell">
      <AppHeader user={user} subtitle="Tenant settings" />
      <main className="settings-main">
        <section className="connector-hero">
          <div>
            <h2>Tenant data connectors</h2>
            <p className="muted">
              Connect the systems your team already works in so yoku can answer
              questions from live JIRA and GitHub data. Tokens are encrypted at rest
              and kept inside this tenant only.
            </p>
          </div>
          <ol className="hero-steps">
            <li>Connect JIRA and GitHub with read-only credentials.</li>
            <li>Run an initial sync to ingest tickets, PRs, and users.</li>
            <li>Return to chat and ask questions against tenant data.</li>
          </ol>
        </section>

        {error && (
          <div className="banner error" onClick={() => setError(null)}>
            {error}
          </div>
        )}

        <section className="settings-workspace">
          <div className="connector-list-column">
            {connectors === null && <p className="muted">Loading…</p>}
            {connectors?.map((c) => (
              <ConnectorCard
                key={c.name}
                status={c}
                busy={busy}
                selected={editing === c.name}
                onConnect={() => setEditing(c.name as EditingName)}
                onDelete={() => onDelete(c.name)}
                onSync={() => onSync(c.name)}
              />
            ))}
          </div>

          <aside className="connector-panel">
            {activeConnector == null ? (
              <ConnectorPanelIntro />
            ) : activeConnector.name === "jira" ? (
              <JiraEditor
                initial={activeConnector}
                onCancel={() => setEditing(null)}
                onSaved={async () => { setEditing(null); await refresh(); }}
              />
            ) : activeConnector.name === "github" ? (
              <GithubEditor
                initial={activeConnector}
                onCancel={() => setEditing(null)}
                onSaved={async () => { setEditing(null); await refresh(); }}
              />
            ) : activeConnector.name === "slack" ? (
              <SlackEditor
                initial={activeConnector}
                onCancel={() => setEditing(null)}
                onSaved={async () => { setEditing(null); await refresh(); }}
              />
            ) : null}
          </aside>
        </section>
      </main>
    </div>
  );
}

function ConnectorCard({
  status,
  busy,
  selected,
  onConnect,
  onDelete,
  onSync,
}: {
  status: ConnectorStatus;
  busy: string | null;
  selected: boolean;
  onConnect: () => void;
  onDelete: () => void;
  onSync: () => void;
}) {
  const syncing = busy === `sync:${status.name}`;
  const deleting = busy === `delete:${status.name}`;
  return (
    <section className={`connector-card${selected ? " selected" : ""}`}>
      <header>
        <div className="connector-header-copy">
          <ConnectorLogo name={status.name} />
          <p className="connector-source">{status.guide.source}</p>
          <h3>{status.guide.display_name}</h3>
        </div>
        <Badge status={status} />
      </header>
      <p className="connector-description">{status.guide.description}</p>
      {status.guide.sync_summary && (
        <p className="connector-sync-summary">{status.guide.sync_summary}</p>
      )}

      {status.configured ? (
        <>
          <div className="connector-state">
            {status.last_synced_at ? (
              <span>Last synced {new Date(status.last_synced_at).toLocaleString()}</span>
            ) : (
              <span>Connected, but initial sync has not run yet.</span>
            )}
            <span>Edit on the right to update credentials.</span>
          </div>
          {status.last_sync_error && <p className="connector-error-copy">{status.last_sync_error}</p>}
          <div className="connector-actions">
            <button onClick={onSync} disabled={syncing}>
              {syncing ? "Sync starting…" : "Sync now"}
            </button>
            <button onClick={onConnect}>Edit</button>
            <button className="danger" onClick={onDelete} disabled={deleting}>
              Disconnect
            </button>
          </div>
        </>
      ) : (
        <>
          <ul className="connector-checklist">
            {status.guide.setup_steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ul>
          <div className="connector-actions">
            <button className="primary" onClick={onConnect}>
              Connect
            </button>
          </div>
        </>
      )}
    </section>
  );
}

function ConnectorPanelIntro() {
  return (
    <section className="editor connector-panel-empty">
      <h3>Choose a connector</h3>
      <p className="editor-copy">
        Select <strong>Connect</strong> or <strong>Edit</strong> on the left to open a
        configuration workspace here. This keeps setup anchored while preserving
        status and sync history beside it.
      </p>
      <ul className="connector-checklist">
        <li>Use JIRA to index tickets, assignees, and project activity.</li>
        <li>Use GitHub to index pull requests, contributors, and repo activity.</li>
        <li>After saving, run a sync once and return to Home to query the data.</li>
      </ul>
    </section>
  );
}

function Badge({ status }: { status: ConnectorStatus }) {
  if (!status.configured) return <span className="badge muted">Not connected</span>;
  if (status.last_sync_status === "error")
    return <span className="badge danger">Last sync failed</span>;
  if (status.last_synced_at)
    return <span className="badge ok">Synced</span>;
  return <span className="badge muted">Connected · never synced</span>;
}

function ConnectorLogo({ name }: { name: string }) {
  if (name === "jira") {
    return (
      <span className="connector-logo jira" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none">
          <path d="M4 9.2 11.2 2H22l-7.2 7.2H4Z" fill="currentColor" />
          <path d="M8.2 13.4 15.4 6.2h4.2L12.4 13.4H8.2Z" fill="currentColor" opacity="0.75" />
          <path d="M2 11.6h10.8L20 18.8H9.2L2 11.6Z" fill="currentColor" opacity="0.55" />
        </svg>
      </span>
    );
  }

  if (name === "slack") {
    return (
      <span className="connector-logo slack" aria-hidden="true">
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
    );
  }

  return (
    <span className="connector-logo github" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none">
        <path
          d="M12 3.2a8.8 8.8 0 0 0-2.78 17.15c.44.08.6-.19.6-.43v-1.52c-2.43.53-2.94-1.03-2.94-1.03-.4-1-.97-1.28-.97-1.28-.79-.55.06-.54.06-.54.88.06 1.35.91 1.35.91.78 1.33 2.05.94 2.55.72.08-.57.31-.94.56-1.16-1.94-.22-3.98-.97-3.98-4.3 0-.95.34-1.73.9-2.34-.1-.22-.39-1.11.08-2.32 0 0 .73-.24 2.4.89a8.3 8.3 0 0 1 4.38 0c1.67-1.13 2.4-.89 2.4-.89.47 1.21.18 2.1.09 2.32.56.61.9 1.39.9 2.34 0 3.34-2.05 4.07-4 4.29.31.26.59.78.59 1.58v2.34c0 .24.16.52.6.43A8.8 8.8 0 0 0 12 3.2Z"
          fill="currentColor"
        />
      </svg>
    </span>
  );
}

function JiraEditor({
  initial,
  onCancel,
  onSaved,
}: {
  initial: ConnectorStatus | undefined;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const cfg = (initial?.config || {}) as Partial<JiraConfigIn>;
  const [form, setForm] = useState<JiraConfigIn>({
    base_url: (cfg.base_url as string) || "https://your-org.atlassian.net",
    email: (cfg.email as string) || "",
    project: (cfg.project as string) || "",
    token: "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      // Drop blank token so backend keeps the previously stored one.
      const payload: JiraConfigIn = { ...form };
      if (!payload.token) delete payload.token;
      await api.saveJiraConfig(payload);
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="editor" onSubmit={save}>
      <div className="editor-title-row">
        <ConnectorLogo name="jira" />
        <div>
          <h3>{initial?.configured ? "Edit JIRA" : "Connect JIRA"}</h3>
          <p className="editor-kicker">Atlassian JIRA</p>
        </div>
      </div>
      <p className="editor-copy">
        JIRA powers ticket search, assignment lookups, and team activity summaries for
        this tenant.
      </p>
      <label className="field">
        <span>Base URL</span>
        <input
          value={form.base_url}
          onChange={(e) => setForm({ ...form, base_url: e.target.value })}
          required
        />
        <small>{initial?.guide.field_help.base_url || defaultFieldHelp.base_url}</small>
      </label>
      <label className="field">
        <span>Email</span>
        <input
          type="email"
          value={form.email}
          onChange={(e) => setForm({ ...form, email: e.target.value })}
          required
        />
        <small>{initial?.guide.field_help.email || defaultFieldHelp.email}</small>
      </label>
      <label className="field">
        <span>API token</span>
        <input
          type="password"
          value={form.token}
          onChange={(e) => setForm({ ...form, token: e.target.value })}
          placeholder={initial?.configured ? "leave blank to keep existing" : ""}
          required={!initial?.configured}
        />
        <small>{initial?.guide.field_help.token || defaultFieldHelp.token}</small>
      </label>
      <label className="field">
        <span>Project key</span>
        <input
          value={form.project}
          onChange={(e) => setForm({ ...form, project: e.target.value })}
          placeholder="e.g. AS"
          required
        />
        <small>{initial?.guide.field_help.project || defaultFieldHelp.project}</small>
      </label>
      {err && <div className="auth-error">{err}</div>}
      <div className="connector-actions">
        <button type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

function GithubEditor({
  initial,
  onCancel,
  onSaved,
}: {
  initial: ConnectorStatus | undefined;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const cfg = (initial?.config || {}) as Partial<GithubConfigIn>;
  const [form, setForm] = useState<GithubConfigIn>({
    api_base: (cfg.api_base as string) || "https://api.github.com",
    org: (cfg.org as string) || "",
    pr_lookback_days: (cfg.pr_lookback_days as number) || 365,
    token: "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const payload: GithubConfigIn = { ...form };
      if (!payload.token) delete payload.token;
      await api.saveGithubConfig(payload);
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="editor" onSubmit={save}>
      <div className="editor-title-row">
        <ConnectorLogo name="github" />
        <div>
          <h3>{initial?.configured ? "Edit GitHub" : "Connect GitHub"}</h3>
          <p className="editor-kicker">GitHub</p>
        </div>
      </div>
      <p className="editor-copy">
        GitHub powers pull request search, contributor lookups, and engineering activity
        questions for this tenant.
      </p>
      <label className="field">
        <span>API base</span>
        <input
          value={form.api_base}
          onChange={(e) => setForm({ ...form, api_base: e.target.value })}
          required
        />
        <small>{initial?.guide.field_help.api_base || defaultFieldHelp.api_base}</small>
      </label>
      <label className="field">
        <span>Organisation</span>
        <input
          value={form.org}
          onChange={(e) => setForm({ ...form, org: e.target.value })}
          placeholder="e.g. AsatoCorp"
          required
        />
        <small>{initial?.guide.field_help.org || defaultFieldHelp.org}</small>
      </label>
      <label className="field">
        <span>Personal access token</span>
        <input
          type="password"
          value={form.token}
          onChange={(e) => setForm({ ...form, token: e.target.value })}
          placeholder={initial?.configured ? "leave blank to keep existing" : ""}
          required={!initial?.configured}
        />
        <small>{initial?.guide.field_help.token || defaultFieldHelp.token}</small>
      </label>
      <label className="field">
        <span>PR lookback days</span>
        <input
          type="number"
          min={1}
          max={3650}
          value={form.pr_lookback_days}
          onChange={(e) =>
            setForm({ ...form, pr_lookback_days: Number(e.target.value) || 365 })
          }
          required
        />
        <small>
          {initial?.guide.field_help.pr_lookback_days ||
            defaultFieldHelp.pr_lookback_days}
        </small>
      </label>
      {err && <div className="auth-error">{err}</div>}
      <div className="connector-actions">
        <button type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

function SlackEditor({
  initial,
  onCancel,
  onSaved,
}: {
  initial: ConnectorStatus | undefined;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const cfg = (initial?.config || {}) as Partial<SlackConfigIn>;
  const [form, setForm] = useState<SlackConfigIn>({
    workspace: (cfg.workspace as string) || "",
    bot_token: "",
    lookback_days: (cfg.lookback_days as number) || 90,
    channel_types: (cfg.channel_types as string) || "public_channel",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const payload: SlackConfigIn = { ...form };
      if (!payload.bot_token) delete payload.bot_token;
      await api.saveSlackConfig(payload);
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="editor" onSubmit={save}>
      <div className="editor-title-row">
        <ConnectorLogo name="slack" />
        <div>
          <h3>{initial?.configured ? "Edit Slack" : "Connect Slack"}</h3>
          <p className="editor-kicker">Slack</p>
        </div>
      </div>
      <p className="editor-copy">
        Slack powers message search, conversation context, and cross-source linking
        between discussions and tickets for this tenant.
      </p>
      <label className="field">
        <span>Workspace slug</span>
        <input
          value={form.workspace}
          onChange={(e) => setForm({ ...form, workspace: e.target.value })}
          placeholder="e.g. acme"
          required
        />
        <small>{initial?.guide.field_help.workspace || "Your Slack workspace slug (the part before .slack.com)."}</small>
      </label>
      <label className="field">
        <span>Bot token</span>
        <input
          type="password"
          value={form.bot_token}
          onChange={(e) => setForm({ ...form, bot_token: e.target.value })}
          placeholder={initial?.configured ? "leave blank to keep existing" : "xoxb-…"}
          required={!initial?.configured}
        />
        <small>{initial?.guide.field_help.bot_token || "Bot User OAuth Token starting with xoxb-. Stored encrypted."}</small>
      </label>
      <label className="field">
        <span>Lookback days</span>
        <input
          type="number"
          min={1}
          max={3650}
          value={form.lookback_days}
          onChange={(e) => setForm({ ...form, lookback_days: Number(e.target.value) || 90 })}
          required
        />
        <small>{initial?.guide.field_help.lookback_days || "How many days of message history to ingest."}</small>
      </label>
      <label className="field">
        <span>Channel types</span>
        <input
          value={form.channel_types}
          onChange={(e) => setForm({ ...form, channel_types: e.target.value })}
        />
        <small>{initial?.guide.field_help.channel_types || "Comma-separated: public_channel, private_channel."}</small>
      </label>
      {err && <div className="auth-error">{err}</div>}
      <div className="connector-actions">
        <button type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

const defaultFieldHelp: Record<string, string> = {
  base_url: "Your Atlassian site URL.",
  email: "The account email tied to the token.",
  token: "Stored encrypted and never returned to the UI.",
  project: "The JIRA project key yoku should index.",
  api_base: "Use the public GitHub API unless you run GitHub Enterprise.",
  org: "Just the org slug from the URL (e.g. AsatoCorp), not the full link.",
  pr_lookback_days: "How many days of pull request history to ingest on sync.",
};
