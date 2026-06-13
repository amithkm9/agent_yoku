import { useEffect, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { api, setToken, User } from "../lib/api";
import { getTheme, setTheme, Theme } from "../lib/theme";

/** Light ↔ Dark toggle: shows the current mode; clicking flips to the other.
 *  Usable in any chrome (the header and the chat sidebar both render it). */
export function ThemeToggle() {
  const [theme, setThemeState] = useState<Theme>(getTheme());
  const toggle = () => {
    const next: Theme = theme === "light" ? "dark" : "light";
    setThemeState(next);
    setTheme(next);
  };
  const label = theme === "light" ? "Light" : "Dark";
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={toggle}
      title={`${label} mode — click for ${theme === "light" ? "dark" : "light"}`}
      aria-label={`${label} mode. Click to switch to ${theme === "light" ? "dark" : "light"}.`}
    >
      {theme === "light" ? <SunIcon /> : <MoonIcon />}
      <span>{label}</span>
    </button>
  );
}

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
  const nav = useNavigate();

  useEffect(() => {
    api
      .listInbox()
      .then((r) => setGaps(r.total_matured))
      .catch(() => setGaps(null));
  }, []);

  function logout() {
    setToken(null);
    nav("/login");
  }

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
        <NavLink
          to="/trends"
          className={({ isActive }) => `nav-pill${isActive ? " active" : ""}`}
        >
          <TrendsIcon />
          <span>Trends</span>
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
        <div className="who-identity">
          <div className="user-name">{user.name || user.email}</div>
          <div className="user-tenant">workspace: {user.tenant_id}</div>
        </div>
        <ThemeToggle />
        <button type="button" className="header-logout" onClick={logout}>
          Log out
        </button>
      </div>
    </header>
  );
}

export function TrendsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="nav-icon" aria-hidden="true">
      <path
        d="M4 19V11M9.5 19V5M15 19v-6M20.5 19V8M2.5 21h19"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
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

function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="nav-icon" aria-hidden="true">
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M12 2.5v2.2M12 19.3v2.2M4.6 4.6l1.6 1.6M17.8 17.8l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.6 19.4l1.6-1.6M17.8 6.2l1.6-1.6"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="nav-icon" aria-hidden="true">
      <path
        d="M20 14.2A8 8 0 1 1 9.8 4 6.4 6.4 0 0 0 20 14.2Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

