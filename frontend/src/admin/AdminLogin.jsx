import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { adminApi, adminErrorMessage } from "../services/adminApi.js";
import "./Admin.css";
import "../pages/AccountAuth.css";

function AdminLogin({ onLoginSuccess, onBack, registrationNotice }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const passwordInput = useRef(null);
  const request = useRef({ sequence: 0, controller: null, mounted: false });

  useEffect(() => {
    const current = request.current;
    const input = passwordInput.current;
    current.mounted = true;
    return () => {
      current.mounted = false;
      current.sequence += 1;
      current.controller?.abort();
      current.controller = null;
      if (input) input.value = "";
    };
  }, []);

  function cancelAndClear() {
    request.current.sequence += 1;
    request.current.controller?.abort();
    request.current.controller = null;
    setUsername("");
    setPassword("");
    setError("");
    setLoading(false);
  }

  async function handleBack() {
    cancelAndClear();
    try { await onBack?.(); } catch { /* Navigation must not leak callback rejections. */ }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (loading) return;
    const input = passwordInput.current;
    input.setCustomValidity(Array.from(password).length > 256 ? "Use at most 256 Unicode characters." : "");
    if (!event.currentTarget.reportValidity()) return;
    request.current.controller?.abort();
    const controller = new AbortController();
    const sequence = ++request.current.sequence;
    request.current.controller = controller;
    const isCurrent = () => request.current.mounted && request.current.sequence === sequence && !controller.signal.aborted;
    setError("");
    setLoading(true);

    try {
      const data = await adminApi.login({ username: username.trim(), password }, { signal: controller.signal });
      if (!isCurrent()) return;
      setUsername("");
      setPassword("");
      setLoading(false);
      request.current.sequence += 1;
      request.current.controller = null;
      await onLoginSuccess?.(data);
    } catch (err) {
      if (isCurrent() && err?.name !== "AbortError") {
        setPassword("");
        setError(adminErrorMessage(err, { login: true }));
      }
    } finally {
      if (isCurrent()) {
        request.current.controller = null;
        setLoading(false);
      }
    }
  }

  return (
    <main className="admin-login">
      <section className="admin-login__card">
        <div className="admin-login__brand-mark">
          AP
        </div>

        <p className="admin-eyebrow">
          PROJECTFLOW AI
        </p>

        <h1 className="admin-title admin-login__title">
          Administrator login
        </h1>

        <p className="admin-intro admin-login__intro">
          Sign in to manage project documents and access the admin dashboard.
        </p>

        {registrationNotice && <p className="account-auth__notice" role="status">Administrator account created. Sign in with your new credentials; registration does not start a session.</p>}

        <form onSubmit={handleSubmit} className="admin-login__form" aria-busy={loading} onInvalid={event => event.target.setAttribute("aria-invalid", "true")} onInput={event => event.target.removeAttribute("aria-invalid")}>
          <label
            htmlFor="admin-username"
            className="admin-label"
          >
            Username
          </label>

          <input
            id="admin-username"
            disabled={loading}
            name="username"
            type="text"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
            maxLength={200}
            placeholder="Enter admin username"
            className="admin-input"
          />

          <label
            htmlFor="admin-password"
            className="admin-label admin-login__password-label"
          >
            Password
          </label>

          <input
            id="admin-password"
            ref={passwordInput}
            disabled={loading}
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => { event.target.setCustomValidity(""); setPassword(event.target.value); }}
            required
            maxLength={512}
            placeholder="Enter admin password"
            className="admin-input"
          />

          {error && (
            <p
              role="alert"
              className="admin-message admin-message--error"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="admin-button admin-button--primary admin-login__submit"
          >
            {loading ? "Signing in..." : "Sign in as administrator"}
          </button>
          {loading && <p className="account-auth__hint" role="status">Checking administrator credentials…</p>}
        </form>

        <nav className="account-auth__links" aria-label="Account options">
          <p>Have a private invitation? <Link to="/admin/signup" onClick={cancelAndClear}>Create an administrator account</Link></p>
          <p><Link to="/login" onClick={cancelAndClear}>User sign-in</Link> · <Link to="/signup" onClick={cancelAndClear}>Create a user account</Link></p>
        </nav>

        <button
          type="button"
          onClick={handleBack}
          className="admin-button admin-button--quiet admin-login__back"
        >
          Back to APM home
        </button>
      </section>
    </main>
  );
}


export default AdminLogin;