import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  api,
  ConnectorStatus,
  GithubConfigIn,
  JiraConfigIn,
  User,
} from "../lib/api";

type EditingName = "jira" | "github" | null;

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
    api.me().then(setUser).catch(() => nav("/login"));
    refresh();
  }, [nav, refresh]);

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
        <SettingsHeader user={user} />
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
      <SettingsHeader user={user} />
      <main className="settings-main">
        <h2>Connectors</h2>
        <p className="muted">
          Plug your tenant's JIRA and GitHub into agent_yoku. Tokens are encrypted at
          rest and isolated to your tenant.
        </p>

        {error && (
          <div className="banner error" onClick={() => setError(null)}>
            {error}
          </div>
        )}

        {connectors === null && <p className="muted">Loading…</p>}
        {connectors?.map((c) => (
          <ConnectorCard
            key={c.name}
            status={c}
            busy={busy}
            onConnect={() => setEditing(c.name as EditingName)}
            onDelete={() => onDelete(c.name)}
            onSync={() => onSync(c.name)}
          />
        ))}

        {editing === "jira" && (
          <JiraEditor
            initial={connectors?.find((c) => c.name === "jira")}
            onCancel={() => setEditing(null)}
            onSaved={async () => {
              setEditing(null);
              await refresh();
            }}
          />
        )}
        {editing === "github" && (
          <GithubEditor
            initial={connectors?.find((c) => c.name === "github")}
            onCancel={() => setEditing(null)}
            onSaved={async () => {
              setEditing(null);
              await refresh();
            }}
          />
        )}
      </main>
    </div>
  );
}

function SettingsHeader({ user }: { user: User }) {
  return (
    <header className="settings-header">
      <div className="brand">agent_yoku</div>
      <nav>
        <Link to="/">Chat</Link>
        <Link to="/settings" className="active">
          Settings
        </Link>
      </nav>
      <div className="who">
        <div className="user-name">{user.name || user.email}</div>
        <div className="user-tenant">tenant: {user.tenant_id}</div>
      </div>
    </header>
  );
}

function ConnectorCard({
  status,
  busy,
  onConnect,
  onDelete,
  onSync,
}: {
  status: ConnectorStatus;
  busy: string | null;
  onConnect: () => void;
  onDelete: () => void;
  onSync: () => void;
}) {
  const syncing = busy === `sync:${status.name}`;
  const deleting = busy === `delete:${status.name}`;
  return (
    <section className="connector-card">
      <header>
        <h3>{titleCase(status.name)}</h3>
        <Badge status={status} />
      </header>

      {status.configured ? (
        <>
          <dl className="connector-meta">
            {Object.entries(status.config).map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{String(v)}</dd>
              </div>
            ))}
            {status.last_synced_at && (
              <div>
                <dt>last synced</dt>
                <dd>{new Date(status.last_synced_at).toLocaleString()}</dd>
              </div>
            )}
            {status.last_sync_error && (
              <div className="error-row">
                <dt>last error</dt>
                <dd>{status.last_sync_error}</dd>
              </div>
            )}
          </dl>
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
          <p className="muted">Not connected.</p>
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

function Badge({ status }: { status: ConnectorStatus }) {
  if (!status.configured) return <span className="badge muted">Not connected</span>;
  if (status.last_sync_status === "error")
    return <span className="badge danger">Last sync failed</span>;
  if (status.last_synced_at)
    return <span className="badge ok">Synced</span>;
  return <span className="badge muted">Connected · never synced</span>;
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
      <h3>{initial?.configured ? "Edit JIRA" : "Connect JIRA"}</h3>
      <label className="field">
        <span>Base URL</span>
        <input
          value={form.base_url}
          onChange={(e) => setForm({ ...form, base_url: e.target.value })}
          required
        />
      </label>
      <label className="field">
        <span>Email</span>
        <input
          type="email"
          value={form.email}
          onChange={(e) => setForm({ ...form, email: e.target.value })}
          required
        />
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
      </label>
      <label className="field">
        <span>Project key</span>
        <input
          value={form.project}
          onChange={(e) => setForm({ ...form, project: e.target.value })}
          placeholder="e.g. AS"
          required
        />
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
      <h3>{initial?.configured ? "Edit GitHub" : "Connect GitHub"}</h3>
      <label className="field">
        <span>API base</span>
        <input
          value={form.api_base}
          onChange={(e) => setForm({ ...form, api_base: e.target.value })}
          required
        />
      </label>
      <label className="field">
        <span>Organisation</span>
        <input
          value={form.org}
          onChange={(e) => setForm({ ...form, org: e.target.value })}
          placeholder="github org name"
          required
        />
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

function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
