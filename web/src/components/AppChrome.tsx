import { Link } from "react-router-dom";

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

export function SparkleIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="hero-spark" aria-hidden="true">
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
