
import { useState } from "react";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8001";

function AdminLogin({ onLoginSuccess, onBack }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();

    setError("");
    setLoading(true);

    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          username: username.trim(),
          password,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Login failed. Please try again.");
      }

      if (!data.access_token || data.role !== "admin") {
        throw new Error("The server did not return a valid admin session.");
      }

      onLoginSuccess(data);
    } catch (err) {
      setError(
        err.message ||
          "Unable to connect to the server. Check that the backend is running."
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: "24px",
        background: "#f8fafc",
        fontFamily: '"Segoe UI", Arial, sans-serif',
      }}
    >
      <section
        style={{
          width: "100%",
          maxWidth: "420px",
          padding: "36px",
          border: "1px solid #e2e8f0",
          borderRadius: "20px",
          background: "#ffffff",
          boxShadow: "0 20px 50px rgba(15, 23, 42, 0.08)",
        }}
      >
        <div
          style={{
            display: "grid",
            placeItems: "center",
            width: "54px",
            height: "54px",
            marginBottom: "24px",
            borderRadius: "14px",
            background: "linear-gradient(135deg, #4f46e5, #7c3aed)",
            color: "#ffffff",
            fontWeight: 800,
            fontSize: "18px",
          }}
        >
          AP
        </div>

        <p
          style={{
            margin: "0 0 8px",
            color: "#5b4de8",
            fontSize: "12px",
            fontWeight: 800,
            letterSpacing: "1.2px",
          }}
        >
          PROJECTFLOW AI
        </p>

        <h1
          style={{
            margin: "0 0 10px",
            color: "#172033",
            fontSize: "28px",
            lineHeight: 1.25,
          }}
        >
          Administrator login
        </h1>

        <p
          style={{
            margin: "0 0 28px",
            color: "#64748b",
            fontSize: "14px",
            lineHeight: 1.6,
          }}
        >
          Sign in to manage project documents and access the admin dashboard.
        </p>

        <form onSubmit={handleSubmit}>
          <label
            htmlFor="admin-username"
            style={{
              display: "block",
              marginBottom: "8px",
              color: "#334155",
              fontSize: "13px",
              fontWeight: 600,
            }}
          >
            Username
          </label>

          <input
            id="admin-username"
            name="username"
            type="text"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
            maxLength={100}
            placeholder="Enter admin username"
            style={inputStyle}
          />

          <label
            htmlFor="admin-password"
            style={{
              display: "block",
              margin: "18px 0 8px",
              color: "#334155",
              fontSize: "13px",
              fontWeight: 600,
            }}
          >
            Password
          </label>

          <input
            id="admin-password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            maxLength={256}
            placeholder="Enter admin password"
            style={inputStyle}
          />

          {error && (
            <p
              role="alert"
              style={{
                marginTop: "16px",
                padding: "12px",
                borderRadius: "8px",
                background: "#fef2f2",
                color: "#b91c1c",
                fontSize: "13px",
                lineHeight: 1.5,
              }}
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: "100%",
              marginTop: "24px",
              padding: "14px",
              border: 0,
              borderRadius: "10px",
              background: loading
                ? "#a5b4fc"
                : "linear-gradient(135deg, #4f46e5, #7c3aed)",
              color: "#ffffff",
              fontSize: "14px",
              fontWeight: 700,
              cursor: loading ? "wait" : "pointer",
            }}
          >
            {loading ? "Signing in..." : "Sign in as administrator"}
          </button>
        </form>

        <button
          type="button"
          onClick={onBack}
          style={{
            width: "100%",
            marginTop: "16px",
            padding: "10px",
            border: 0,
            background: "transparent",
            color: "#64748b",
            fontSize: "13px",
            cursor: "pointer",
          }}
        >
          Back to APM home
        </button>
      </section>
    </main>
  );
}

const inputStyle = {
  width: "100%",
  padding: "12px 13px",
  border: "1px solid #cbd5e1",
  borderRadius: "9px",
  outlineColor: "#6366f1",
  background: "#ffffff",
  color: "#172033",
  fontSize: "14px",
};

export default AdminLogin;