import { useEffect, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { api, User } from "../lib/api";

export function AppBrand({ subtitle = "Home" }: { subtitle?: string }) {
  return (
    <Link to="/" className="brand-lockup" aria-label="Agent Yoku home">
      <span className="brand-mark" aria-hidden="true">
        <span>AY</span>
      </span>
      <span className="brand-copy">
        <span className="brand-title">Agent Yoku</span>
        <span className="brand-subtitle">{subtitle}</span>
      </span>
    </Link>
  );
}

/** Shared top bar for full-width pages (Proactive, Settings): brand, nav
 *  pills with active states, live gap-count badge, signed-in identity. */
export function AppHeader({ user, subtitle }: { user: User; subtitle: string }) {
  const [gaps, setGaps] = useState<number | null>(null);

  useEffect(() => {
    api
      .listInbox()
      .then((r) => setGaps(r.total_matured))
      .catch(() => setGaps(null));
  }, []);

  return (
    <header className="settings-header">
      <AppBrand subtitle={subtitle} />
      <nav>
        <NavLink to="/" end className={({ isActive }) => `nav-pill${isActive ? " active" : ""}`}>
          <ChatIcon />
          <span>Chat</span>
        </NavLink>
        <NavLink
          to="/inbox"
          className={({ isActive }) => `nav-pill${isActive ? " active" : ""}`}
        >
          <SparkleIcon className="nav-icon" />
          <span>Proactive</span>
          {gaps != null && gaps > 0 && <span className="count-badge">{gaps}</span>}
        </NavLink>
        {user.is_admin && (
          <NavLink
            to="/settings"
            className={({ isActive }) => `nav-pill${isActive ? " active" : ""}`}
          >
            <SettingsIcon />
            <span>Settings</span>
          </NavLink>
        )}
      </nav>
      <div className="who">
        <div className="user-name">{user.name || user.email}</div>
        <div className="user-tenant">tenant: {user.tenant_id}</div>
      </div>
    </header>
  );
}

export function ChatIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="nav-icon" aria-hidden="true">
      <path
        d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7a2.5 2.5 0 0 1-2.5 2.5H9l-4 4v-4H6.5A2.5 2.5 0 0 1 4 13.5v-7Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function SettingsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="nav-icon" aria-hidden="true">
      <path
        d="m12 3 1.3 2.3 2.6.6-.3 2.6 1.8 1.9-1.8 1.9.3 2.6-2.6.6L12 18l-1.3-2.3-2.6-.6.3-2.6-1.8-1.9 1.8-1.9-.3-2.6 2.6-.6L12 3Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="12" r="2.6" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

export function SparkleIcon({ className = "hero-spark" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="M12 2.5v19M2.5 12h19M5.4 5.4l13.2 13.2M18.6 5.4 5.4 18.6"
        stroke="currentColor"
        strokeWidth="2.1"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="nav-icon" aria-hidden="true">
      <path
        d="M21 3 10 14"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="m21 3-7 18-4-7-7-4 18-7Z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
