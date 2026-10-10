import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import AdminDashboard from "./admin/AdminDashboard.jsx";
import { adminApi, adminErrorMessage, isAdminSessionValid } from "./services/adminApi.js";
import { accountApi, accountErrorMessage, isAccountSessionValid } from "./services/accountApi.js";
import { UserLogin, UserSignup } from "./pages/AccountAuth.jsx";
import WorkspaceHeader from "./components/WorkspaceHeader.jsx";
import Dashboard from "./pages/Dashboard";
import Planning from "./pages/Planning";
import Schedule from "./pages/Schedule";
import Coordinator from "./pages/Coordinator";
import Monitoring from "./pages/Monitoring";
import Profile from "./pages/Profile.jsx";
import { workspaceHome } from "./components/navigation.js";
import "./App.css";
import "./pages.css";

const agents = [
  { icon: "coordinator", letter: "C", title: "Coordinator Agent", activity: "Managing project workflow", status: "Working", statusClass: "working" },
  { icon: "planning", letter: "P", title: "Planning Agent", activity: "Optimizing project schedule", status: "Working", statusClass: "working" },
  { icon: "risk", letter: "R", title: "Risk Agent", activity: "Analyzing project risks", status: "Analyzing", statusClass: "analyzing" },
  { icon: "report", letter: "M", title: "Monitor & Report Agent", activity: "Ready for reporting", status: "Ready", statusClass: "ready" },
];

const features = [
  { number: "01", color: "blue", title: "Smart Planning", description: "Automatically create project plans, tasks, milestones, dependencies, and schedules from natural language requirements.", path: "/coordinator", link: "Open Coordinator →" },
  { number: "02", color: "purple", title: "Risk Detection", description: "Detect potential project risks and provide intelligent recommendations for mitigation and decision-making." },
  { number: "03", color: "green", title: "Knowledge Retrieval", description: "Retrieve relevant information from project documents, knowledge bases, and historical project data." },
  { number: "04", color: "orange", title: "Monitoring & Reports", description: "Monitor project health and automatically generate progress summaries, insights, and management reports.", path: "/schedule", link: "View Schedule →" },
];

const agentCards = [
  { number: "01", icon: "coordinator", label: "COORDINATION", title: "Decision & Coordinator Agent", description: "Understands user requests, makes decisions, and coordinates activities between the specialized AI agents.", tech: "LLM • A2A • API" },
  { number: "02", icon: "planning", label: "PLANNING", title: "Planning & Scheduling Agent", description: "Creates project plans, schedules tasks, manages dependencies, and helps optimize resource allocation.", tech: "LLM • NLP • Scheduling" },
  { number: "03", icon: "risk", label: "RISK & KNOWLEDGE", title: "Risk & Knowledge Retrieval Agent", description: "Searches project knowledge and identifies risks using information retrieval and intelligent analysis.", tech: "RAG • NLP • IR" },
  { number: "04", icon: "report", label: "MONITORING", title: "Monitor & Report Agent", description: "Tracks project progress and transforms project information into useful reports and management insights.", tech: "LLM • Analytics • Reports" },
];

const workflow = [
  { title: "User Request", description: "The project manager provides a request using natural language." },
  { title: "Coordinator", description: "The coordinator analyzes the request and selects the required agents." },
  { title: "Agent Collaboration", description: "Specialized agents communicate and perform their assigned tasks." },
  { title: "Result", description: "APM combines the results and provides an intelligent response to the user." },
];

function LandingPage() {
  return (
    <div className="app">
      <header className="navbar">
        <div className="brand">
          <div className="brand-logo">AP</div>
          <div className="brand-text"><h1>APM</h1><span>Agentic Project Management</span></div>
        </div>
        <nav className="nav-links">
          <Link to="/">Home</Link>
          <Link to="/monitoring">Monitoring</Link>
          <a href="#features">Features</a>
          <a href="#agents">AI Agents</a>
          <a href="#about">About</a>
        </nav>
        <div className="nav-actions">
          <Link to="/login" className="nav-button">Sign in</Link>
          <Link to="/signup" className="nav-button">Sign up</Link>

          <Link to="/dashboard" className="nav-button">Get Started</Link>
        </div>
      </header>

      <main>
        <section className="hero" id="home">
          <div className="hero-content">
            <div className="badge"><span className="badge-dot" />AI-POWERED PROJECT MANAGEMENT</div>
            <h2>Manage Projects<span>Smarter with AI Agents.</span></h2>
            <p className="hero-description">APM is an intelligent multi-agent project management system designed to help teams plan projects, identify risks, retrieve knowledge, monitor progress, and generate intelligent reports.</p>
            <div className="hero-actions">
              <Link to="/dashboard" className="primary-button">Get Started</Link>
              <a href="#agents" className="secondary-button">Explore AI Agents <span>→</span></a>
            </div>
            <div className="hero-stats">
              <div className="stat"><strong>4</strong><span>AI Agents</span></div>
              <div className="stat-divider" />
              <div className="stat"><strong>24/7</strong><span>Monitoring</span></div>
              <div className="stat-divider" />
              <div className="stat"><strong>AI</strong><span>Powered</span></div>
            </div>
          </div>
          <div className="hero-visual">
            <div className="glow" />
            <div className="dashboard-card">
              <div className="dashboard-header">
                <div><span className="dashboard-label">PROJECT OVERVIEW</span><h3>Website Development</h3></div>
                <span className="active-status"><span />Active</span>
              </div>
              <div className="progress-area">
                <div className="progress-header"><span>Project Progress</span><strong>72%</strong></div>
                <div className="progress-track"><div className="progress-fill" /></div>
              </div>
              <div className="dashboard-metrics">
                <div className="metric"><span>Tasks</span><strong>24</strong><small>+8 this week</small></div>
                <div className="metric"><span>Risks</span><strong className="warning">3</strong><small>Needs attention</small></div>
                <div className="metric"><span>Team</span><strong>12</strong><small>Members</small></div>
              </div>
              <div className="agent-monitor">
                <div className="monitor-title"><span>AI Agent Activity</span><span className="live">LIVE</span></div>
                {agents.map((agent) => (
                  <div className="agent-row" key={agent.icon}>
                    <div className={`agent-icon ${agent.icon}`}>{agent.letter}</div>
                    <div className="agent-info"><strong>{agent.title}</strong><span>{agent.activity}</span></div>
                    <span className={`agent-status ${agent.statusClass}`}>{agent.status}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="features-section" id="features">
          <div className="section-header">
            <div className="badge">CORE CAPABILITIES</div>
            <h2>Everything your project needs,<span>powered by intelligence.</span></h2>
            <p>APM combines artificial intelligence, information retrieval, natural language processing, and multi-agent collaboration to support your project from planning to completion.</p>
          </div>
          <div className="feature-grid">
            {features.map((feature) => (
              <div className="feature-card" key={feature.number}>
                <div className={`feature-number ${feature.color}`}>{feature.number}</div>
                <h3>{feature.title}</h3><p>{feature.description}</p>
                {feature.path ? <Link to={feature.path} className="feature-link">{feature.link}</Link> : <span className="feature-link">Learn more →</span>}
              </div>
            ))}
          </div>
        </section>

        <section className="agents-section" id="agents">
          <div className="section-header dark">
            <div className="badge dark-badge">MULTI-AGENT SYSTEM</div>
            <h2>Meet your<span>AI project team.</span></h2>
            <p>Specialized intelligent agents collaborate through defined communication protocols to solve complex project management tasks.</p>
          </div>
          <div className="agents-grid">
            {agentCards.map((agent) => (
              <div className="agent-card" key={agent.number}>
                <div className={`large-agent-icon ${agent.icon}`}>{agent.number}</div>
                <span className="agent-label">{agent.label}</span>
                <h3>{agent.title}</h3><p>{agent.description}</p>
                <div className="agent-tech">{agent.tech}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="workflow-section">
          <div className="section-header">
            <div className="badge">HOW APM WORKS</div>
            <h2>One request.<span>Multiple intelligent agents.</span></h2>
            <p>APM coordinates specialized agents to transform your request into actionable project intelligence.</p>
          </div>
          <div className="workflow">
            {workflow.map((step, index) => (
              <div className="workflow-step" key={step.title}>
                <div className="workflow-number">{String(index + 1).padStart(2, "0")}</div>
                <h3>{step.title}</h3><p>{step.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="cta-section" id="about">
          <div className="cta-content">
            <div className="badge cta-badge">AGENTIC AI</div>
            <h2>Build better projects<span>with intelligent automation.</span></h2>
            <p>Let your AI project team handle complexity while your human team focuses on delivering successful projects.</p>
            <Link to="/dashboard" className="primary-button cta-button">Launch APM <span>→</span></Link>
          </div>
        </section>
      </main>

      <footer className="footer">
        <div className="footer-brand"><div className="brand-logo">AP</div><div><strong>APM</strong><span>Agentic Project Management System</span></div></div>
        <p>© 2026 APM. Built with React, FastAPI &amp; Agentic AI.</p>
      </footer>
    </div>
  );
}


function AccountLoginPage({ onLoginSuccess }) {
  const navigate = useNavigate();
  const location = useLocation();
  return <UserLogin registrationNotice={location.state?.registrationNotice === true} onLoginSuccess={(data) => {
    onLoginSuccess(data);
    navigate(data.role === "admin" ? "/admin/dashboard" : "/dashboard", { replace: true });
  }} onBack={() => navigate("/")} />;
}

function AccountSignupPage() {
  const navigate = useNavigate();
  return <UserSignup onSignupSuccess={() => navigate("/login", {
    replace: true, state: { registrationNotice: true },
  })} onBack={() => navigate("/")} />;
}

function AccountDashboardPage({ session, onLogout }) {
  const navigate = useNavigate();
  const [attempt, setAttempt] = useState(0);
  const [verification, setVerification] = useState(null);
  const validSession = isAccountSessionValid(session) && session.role === "user";

  useEffect(() => {
    if (!session) return;
    if (!isAccountSessionValid(session) || session.role !== "user") {
      onLogout(session);
      return;
    }
    const controller = new AbortController();
    let active = true;
    accountApi.verifySession(session, { signal: controller.signal }).then(() => {
      if (active && !controller.signal.aborted && isAccountSessionValid(session)) {
        setVerification({ session, attempt, verified: true });
      }
    }).catch((error) => {
      if (!active || controller.signal.aborted || error?.name === "AbortError") return;
      if (error.status === 401 || error.status === 403) onLogout(session);
      else setVerification({ session, attempt, verified: false, error: accountErrorMessage(error) });
    });
    return () => {
      active = false;
      controller.abort();
    };
  }, [session, attempt, onLogout]);

  if (!validSession) return <Navigate to="/login" replace />;
  const current = verification?.session === session && verification.attempt === attempt;
  const signOut = () => { onLogout(session); navigate("/login", { replace: true }); };
  if (!current || !verification.verified) {
    return (
      <main className="admin-verification">
        <h1>Account session</h1>
        {current ? <p role="alert">{verification.error}</p> : <p role="status">Verifying account access...</p>}
        {current && <button type="button" onClick={() => setAttempt(value => value + 1)}>Retry verification</button>}
        <button type="button" onClick={signOut}>Sign out</button>
      </main>
    );
  }
  return (
    <div className="account-workspace">
      <header className="account-session-bar">
        <div className="account-session-identity">
          <p><span className="badge">USER ACCOUNT</span> Signed in as <strong>{session.username}</strong></p>
          <p className="account-session-id">User ID: <code>{session.user_id}</code></p>
        </div>
        <nav aria-label="Signed-in account">
          <button type="button" className="secondary-button" onClick={signOut}>Sign out</button>
        </nav>
      </header>
      <Dashboard />
    </div>
  );
}

function AdminDashboardPage({ session, onLogout }) {
  const navigate = useNavigate();
  const [attempt, setAttempt] = useState(0);
  const [verification, setVerification] = useState(null);
  const validSession = isAdminSessionValid(session);

  useEffect(() => {
    if (!session) return;
    if (!isAdminSessionValid(session)) {
      onLogout(session);
      return;
    }
    const controller = new AbortController();
    let active = true;
    adminApi.verifySession(session, { signal: controller.signal }).then(() => {
      if (active && !controller.signal.aborted && isAdminSessionValid(session)) {
        setVerification({ session, attempt, verified: true });
      }
    }).catch((error) => {
      if (!active || controller.signal.aborted || error?.name === "AbortError") return;
      if (error.status === 401) onLogout(session);
      else setVerification({ session, attempt, verified: false, error: adminErrorMessage(error) });
    });
    return () => {
      active = false;
      controller.abort();
    };
  }, [session, attempt, onLogout]);

  if (!validSession) return <Navigate to="/login" replace />;
  const current = verification?.session === session && verification.attempt === attempt;
  if (!current || !verification.verified) {
    return (
      <main className="admin-verification">
        <h1>Administrator session</h1>
        {current ? <p role="alert">{verification.error}</p> : <p role="status">Verifying administrator access...</p>}
        {current && <button type="button" onClick={() => setAttempt(value => value + 1)}>Retry verification</button>}
        <button type="button" onClick={() => { onLogout(session); navigate("/login", { replace: true }); }}>Sign out</button>
      </main>
    );
  }
  return <AdminDashboard session={session} onLogout={() => { onLogout(session); navigate("/login", { replace: true }); }} />;
}

function ProjectWorkspacePage({ session, onLogout, Page }) {
  const [attempt, setAttempt] = useState(0);
  const [verification, setVerification] = useState(null);
  const validSession = session?.role === "admin" ? isAdminSessionValid(session) : isAccountSessionValid(session);

  useEffect(() => {
    if (!session) return;
    if (!validSession) {
      onLogout(session);
      return;
    }
    const controller = new AbortController();
    let active = true;
    const client = session.role === "admin" ? adminApi : accountApi;
    client.verifySession(session, { signal: controller.signal }).then(() => {
      if (active && !controller.signal.aborted) setVerification({ session, attempt, verified: true });
    }).catch(error => {
      if (!active || controller.signal.aborted || error?.name === "AbortError") return;
      if (error.status === 401) onLogout(session);
      else setVerification({ session, attempt, verified: false, error: session.role === "admin" ? adminErrorMessage(error) : accountErrorMessage(error) });
    });
    return () => { active = false; controller.abort(); };
  }, [session, validSession, attempt, onLogout]);

  if (!validSession) return <Navigate to="/login" replace />;
  const current = verification?.session === session && verification.attempt === attempt;
  if (!current || !verification.verified) {
    return (
      <main className="admin-verification">
        <h1>Project access</h1>
        {current ? <p role="alert">{verification.error}</p> : <p role="status">Verifying project access...</p>}
        {current && <button type="button" onClick={() => setAttempt(value => value + 1)}>Retry verification</button>}
        <button type="button" onClick={() => onLogout(session)}>Sign out</button>
      </main>
    );
  }
  return <Page session={session} onUnauthorized={onLogout} />;
}

function useSessionExpiry(session, clearSession) {
  useEffect(() => {
    if (!session) return;
    let timer;
    function checkExpiry() {
      const remaining = session.expires_at * 1000 - Date.now();
      if (remaining <= 0) clearSession(session);
      else timer = setTimeout(checkExpiry, Math.min(remaining, 2147483647));
    }
    checkExpiry();
    // Background timers can be throttled; recheck before revealing private UI.
    const checkOnVisibility = () => {
      clearTimeout(timer);
      checkExpiry();
    };
    document.addEventListener("visibilitychange", checkOnVisibility);
    window.addEventListener("focus", checkOnVisibility);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", checkOnVisibility);
      window.removeEventListener("focus", checkOnVisibility);
    };
  }, [session, clearSession]);
}

function App() {
  const [session, setSession] = useState(null);
  const [accountSession, setAccountSession] = useState(null);
  const clearSession = useCallback((expectedSession) => {
    setSession(current => !expectedSession || current === expectedSession ? null : current);
  }, []);
  const clearAccountSession = useCallback((expectedSession) => {
    setAccountSession(current => !expectedSession || current === expectedSession ? null : current);
  }, []);
  const clearProjectSession = useCallback((expectedSession) => {
    clearSession(expectedSession);
    clearAccountSession(expectedSession);
  }, [clearSession, clearAccountSession]);

  const acceptAccountSession = useCallback((data) => {
    if (data.role === "admin") {
      setAccountSession(null);
      setSession(data);
    } else {
      setSession(null);
      setAccountSession(data);
    }
  }, []);
  useSessionExpiry(session, clearSession);
  useSessionExpiry(accountSession, clearAccountSession);
  const signedInHome = isAdminSessionValid(session)
    ? workspaceHome("admin")
    : isAccountSessionValid(accountSession) ? workspaceHome("user") : null;

  return (
    <BrowserRouter>
      {(isAccountSessionValid(accountSession) || isAdminSessionValid(session)) && (
        <WorkspaceHeader role={session?.role ?? accountSession?.role} />
      )}
      <Routes>
        <Route path="/" element={signedInHome ? <Navigate to={signedInHome} replace /> : <LandingPage />} />
        <Route path="/coordinator" element={<ProjectWorkspacePage session={accountSession ?? session} onLogout={clearProjectSession} Page={Coordinator} />} />
        <Route path="/login" element={signedInHome ? <Navigate to={signedInHome} replace /> : <AccountLoginPage onLoginSuccess={acceptAccountSession} />} />
        <Route path="/signup" element={signedInHome ? <Navigate to={signedInHome} replace /> : <AccountSignupPage />} />
        <Route path="/profile" element={<ProjectWorkspacePage session={accountSession ?? session} onLogout={clearProjectSession} Page={Profile} />} />
        <Route path="/admin/signup" element={<Navigate to="/login" replace />} />
        <Route path="/admin/login" element={<Navigate to="/login" replace />} />
        <Route path="/admin/dashboard" element={<AdminDashboardPage session={session} onLogout={clearSession} />} />
        <Route path="/dashboard" element={isAdminSessionValid(session)
          ? <Navigate to="/admin/dashboard" replace />
          : <AccountDashboardPage session={accountSession} onLogout={clearAccountSession} />} />
        <Route path="/planning" element={<ProjectWorkspacePage session={accountSession ?? session} onLogout={clearProjectSession} Page={Planning} />} />
        <Route path="/schedule" element={<ProjectWorkspacePage session={accountSession ?? session} onLogout={clearProjectSession} Page={Schedule} />} />
        <Route path="/schedule/:projectId" element={<ProjectWorkspacePage session={accountSession ?? session} onLogout={clearProjectSession} Page={Schedule} />} />
        <Route path="/monitoring" element={<ProjectWorkspacePage session={accountSession ?? session} onLogout={clearProjectSession} Page={Monitoring} />} />
        <Route path="/monitoring/:projectId" element={<ProjectWorkspacePage session={accountSession ?? session} onLogout={clearProjectSession} Page={Monitoring} />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
