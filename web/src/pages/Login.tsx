import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setTenant, setToken } from "../lib/api";

type Mode = "login" | "signup";

export function Login() {
  const nav = useNavigate();
  const [mode, setMode] = useState<Mode>("login");
  const [tenant, setTenantInput] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const normalizedTenant = tenant.trim().toLowerCase();
    if (!normalizedTenant) {
      setError("Tenant is required.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const result =
        mode === "login"
          ? await api.login(email, password, normalizedTenant)
          : await api.signup(email, password, name || email, normalizedTenant);
      setToken(result.access_token);
      setTenant(result.user.tenant_id);
      nav("/");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1 className="auth-title">agent_yoku</h1>
        <p className="auth-subtitle">
          {mode === "login" ? "Sign in to your tenant" : "Create an account"}
        </p>
        <p className="auth-subtitle">
          {mode === "signup"
            ? "Choose a new tenant name to create your workspace, or an existing one if you were invited."
            : "Enter the exact tenant name for your workspace."}
        </p>

        <form onSubmit={submit}>
          <label className="field">
            <span>Tenant</span>
            <input
              type="text"
              value={tenant}
              onChange={(e) => setTenantInput(e.target.value)}
              placeholder="acme"
              required
              autoCapitalize="none"
              autoCorrect="off"
            />
          </label>

          {mode === "signup" && (
            <label className="field">
              <span>Name</span>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Optional"
              />
            </label>
          )}

          <label className="field">
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>

          <label className="field">
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
            />
          </label>

          {error && <div className="auth-error">{error}</div>}

          <button type="submit" className="primary" disabled={loading}>
            {loading ? "…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>

        <p className="auth-toggle">
          {mode === "login" ? "No account? " : "Already have one? "}
          <button
            type="button"
            className="link"
            onClick={() => setMode(mode === "login" ? "signup" : "login")}
          >
            {mode === "login" ? "Sign up" : "Sign in"}
          </button>
        </p>
      </div>
    </div>
  );
}
