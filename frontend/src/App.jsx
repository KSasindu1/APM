import { useState } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  Link,
  Navigate,
  useNavigate,
} from "react-router-dom";

import AdminLogin from "./admin/AdminLogin.jsx";
import AdminDashboard from "./admin/AdminDashboard.jsx";
import Dashboard from "./pages/Dashboard";
import Planning from "./pages/Planning";
import Schedule from "./pages/Schedule";

import "./App.css";
import "./pages.css";

// ============================================================
// Landing Page
// ============================================================

function LandingPage() {
  return (
    <div className="app">

      {/* ======================================================
          NAVBAR
          ====================================================== */}

      <header className="navbar">
        <div className="brand">
          <div className="brand-logo">AP</div>

          <div className="brand-text">
            <h1>APM</h1>
            <span>Agentic Project Management</span>
          </div>
        </div>

        <nav className="nav-links">
          <Link to="/">Home</Link>

          <a href="#features">
            Features
          </a>

          <a href="#agents">
            AI Agents
          </a>

          <a href="#about">
            About
          </a>
        </nav>

        <div className="nav-actions">
          <Link to="/admin/login" className="nav-button">Admin Login</Link>
          <Link to="/dashboard" className="nav-button">Get Started</Link>
        </div>
      </header>

      <main>

        {/* ====================================================
            HERO
            ==================================================== */}

        <section
          className="hero"
          id="home"
        >
          <div className="hero-content">

            <div className="badge">
              <span className="badge-dot"></span>
              AI-POWERED PROJECT MANAGEMENT
            </div>

            <h2>
              Manage Projects
              <span>
                Smarter with AI Agents.
              </span>
            </h2>

            <p className="hero-description">
              APM is an intelligent multi-agent project
              management system designed to help teams
              plan projects, identify risks, retrieve
              knowledge, monitor progress, and generate
              intelligent reports.
            </p>

            <div className="hero-actions">

              <Link
                to="/dashboard"
                className="primary-button"
              >
                Get Started
              </Link>

              <a
                href="#agents"
                className="secondary-button"
              >
                Explore AI Agents
                <span>→</span>
              </a>

            </div>

            <div className="hero-stats">

              <div className="stat">
                <strong>4</strong>
                <span>AI Agents</span>
              </div>

```
      <div className="brand-text">
        <h1>APM</h1>
        <span>Agentic Project Management</span>
      </div>
    </div>

    <nav className="nav-links">
      <a href="#home">Home</a>
      <a href="#features">Features</a>
      <a href="#agents">AI Agents</a>
      <a href="#about">About</a>
    </nav>

    <button
      className="nav-button"
      onClick={() => setCurrentPage("login")}
    >
      Admin Login
    </button>
  </header>

  {/* ==================== HERO ==================== */}
  <main>
    <section className="hero" id="home">
      <div className="hero-content">
        <div className="badge">
          <span className="badge-dot"></span>
          AI-POWERED PROJECT MANAGEMENT
        </div>

        <h2>
          Manage Projects
          <span>Smarter with AI Agents.</span>
        </h2>

              <div className="stat">
                <strong>AI</strong>
                <span>Powered</span>
              </div>

            </div>

          </div>

          {/* ==================================================
              DASHBOARD PREVIEW
              ================================================== */}

          <div className="hero-visual">

            <div className="glow"></div>

            <div className="dashboard-card">

              <div className="dashboard-header">

                <div>
                  <span className="dashboard-label">
                    PROJECT OVERVIEW
                  </span>

                  <h3>
                    Website Development
                  </h3>
                </div>

                <span className="active-status">
                  <span></span>
                  Active
                </span>

              </div>

              <div className="progress-area">

                <div className="progress-header">
                  <span>
                    Project Progress
                  </span>

                  <strong>
                    72%
                  </strong>
                </div>

                <div className="progress-track">
                  <div className="progress-fill"></div>
                </div>

              </div>

              <div className="dashboard-metrics">

                <div className="metric">
                  <span>Tasks</span>
                  <strong>24</strong>
                  <small>
                    +8 this week
                  </small>
                </div>

                <div className="metric">
                  <span>Risks</span>
                  <strong className="warning">
                    3
                  </strong>
                  <small>
                    Needs attention
                  </small>
                </div>

                <div className="metric">
                  <span>Team</span>
                  <strong>12</strong>
                  <small>
                    Members
                  </small>
                </div>

              </div>

              <div className="agent-monitor">

                <div className="monitor-title">
                  <span>
                    AI Agent Activity
                  </span>

                  <span className="live">
                    LIVE
                  </span>
                </div>

                <div className="agent-row">

                  <div className="agent-icon coordinator">
                    C
                  </div>

                  <div className="agent-info">

                    <strong>
                      Coordinator Agent
                    </strong>

                    <span>
                      Managing project workflow
                    </span>

                  </div>

                  <span className="agent-status working">
                    Working
                  </span>

                </div>

                <div className="agent-row">

                  <div className="agent-icon planning">
                    P
                  </div>

                  <div className="agent-info">

                    <strong>
                      Planning Agent
                    </strong>

                    <span>
                      Optimizing project schedule
                    </span>

                  </div>

                  <span className="agent-status working">
                    Working
                  </span>

                </div>

                <div className="agent-row">

                  <div className="agent-icon risk">
                    R
                  </div>

                  <div className="agent-info">

                    <strong>
                      Risk Agent
                    </strong>

                    <span>
                      Analyzing project risks
                    </span>

                  </div>

                  <span className="agent-status analyzing">
                    Analyzing
                  </span>

                </div>

                <div className="agent-row">

                  <div className="agent-icon report">
                    M
                  </div>

                  <div className="agent-info">

                    <strong>
                      Monitor & Report Agent
                    </strong>

                    <span>
                      Ready for reporting
                    </span>

                  </div>

                  <span className="agent-status ready">
                    Ready
                  </span>

                </div>

              </div>

            </div>

          </div>

        </section>

        {/* ====================================================
            FEATURES
            ==================================================== */}

        <section
          className="features-section"
          id="features"
        >
          <div className="section-header">

            <div className="badge">
              CORE CAPABILITIES
            </div>

            <h2>
              Everything your project needs,
              <span>
                powered by intelligence.
              </span>
            </h2>

            <p>
              APM combines artificial intelligence,
              information retrieval, natural language
              processing, and multi-agent collaboration
              to support your project from planning to
              completion.
            </p>

          </div>

          <div className="feature-grid">

            <div className="feature-card">

              <div className="feature-number blue">
                01
              </div>

              <h3>
                Smart Planning
              </h3>

              <p>
                Automatically create project plans,
                tasks, milestones, dependencies, and
                schedules from natural language
                requirements.
              </p>

              <Link
                to="/planning"
                className="feature-link"
              >
                Open Planning →
              </Link>

            </div>

            <div className="feature-card">

              <div className="feature-number purple">
                02
              </div>

              <h3>
                Risk Detection
              </h3>

              <p>
                Detect potential project risks and
                provide intelligent recommendations
                for mitigation and decision-making.
              </p>

              <span className="feature-link">
                Learn more →
              </span>

            </div>

            <div className="feature-card">

              <div className="feature-number green">
                03
              </div>

              <h3>
                Knowledge Retrieval
              </h3>

              <p>
                Retrieve relevant information from
                project documents, knowledge bases,
                and historical project data.
              </p>

              <span className="feature-link">
                Learn more →
              </span>

            </div>
          </div>

            <div className="feature-card">

              <div className="feature-number orange">
                04
              </div>

              <h3>
                Monitoring & Reports
              </h3>

              <p>
                Monitor project health and automatically
                generate progress summaries, insights,
                and management reports.
              </p>

              <Link
                to="/schedule"
                className="feature-link"
              >
                View Schedule →
              </Link>

            </div>

          </div>
        </section>

        {/* ====================================================
            AI AGENTS
            ==================================================== */}

        <section
          className="agents-section"
          id="agents"
        >
          <div className="section-header dark">

            <div className="badge dark-badge">
              MULTI-AGENT SYSTEM
            </div>

            <h2>
              Meet your
              <span>
                AI project team.
              </span>
            </h2>

            <p>
              Specialized intelligent agents collaborate
              through defined communication protocols to
              solve complex project management tasks.
            </p>

          </div>

          <div className="agents-grid">

            <div className="agent-card">

              <div className="large-agent-icon coordinator">
                01
              </div>

              <span className="agent-label">
                COORDINATION
              </span>

              <h3>
                Decision & Coordinator Agent
              </h3>

              <p>
                Understands user requests, makes decisions,
                and coordinates activities between the
                specialized AI agents.
              </p>

              <div className="agent-tech">
                LLM • A2A • API
              </div>

            </div>

            <div className="agent-card">

              <div className="large-agent-icon planning">
                02
              </div>

              <span className="agent-label">
                PLANNING
              </span>

              <h3>
                Planning & Scheduling Agent
              </h3>

              <p>
                Creates project plans, schedules tasks,
                manages dependencies, and helps optimize
                resource allocation.
              </p>

              <div className="agent-tech">
                LLM • NLP • Scheduling
              </div>

            </div>
          </div>
        </div>
      </div>
    </section>

            <div className="agent-card">

              <div className="large-agent-icon risk">
                03
              </div>

              <span className="agent-label">
                RISK & KNOWLEDGE
              </span>

              <h3>
                Risk & Knowledge Retrieval Agent
              </h3>

              <p>
                Searches project knowledge and identifies
                risks using information retrieval and
                intelligent analysis.
              </p>

              <div className="agent-tech">
                RAG • NLP • IR
              </div>

            </div>

            <div className="agent-card">

              <div className="large-agent-icon report">
                04
              </div>

              <span className="agent-label">
                MONITORING
              </span>

              <h3>
                Monitor & Report Agent
              </h3>

              <p>
                Tracks project progress and transforms
                project information into useful reports
                and management insights.
              </p>

              <div className="agent-tech">
                LLM • Analytics • Reports
              </div>

            </div>

          </div>
        </section>

        {/* ====================================================
            WORKFLOW
            ==================================================== */}

        <section className="workflow-section">

          <div className="section-header">

            <div className="badge">
              HOW APM WORKS
            </div>

            <h2>
              One request.
              <span>
                Multiple intelligent agents.
              </span>
            </h2>

            <p>
              APM coordinates specialized agents to
              transform your request into actionable
              project intelligence.
            </p>

          </div>

          <div className="workflow">

            <div className="workflow-step">

              <div className="workflow-number">
                01
              </div>

              <h3>
                User Request
              </h3>

              <p>
                The project manager provides a request
                using natural language.
              </p>

            </div>

        <h2>
          One request.
          <span>Multiple intelligent agents.</span>
        </h2>

            <div className="workflow-step">

              <div className="workflow-number">
                02
              </div>

              <h3>
                Coordinator
              </h3>

              <p>
                The coordinator analyzes the request
                and selects the required agents.
              </p>

            </div>

        <div className="workflow-line"></div>

            <div className="workflow-step">

              <div className="workflow-number">
                03
              </div>

              <h3>
                Agent Collaboration
              </h3>

              <p>
                Specialized agents communicate and
                perform their assigned tasks.
              </p>

            </div>

    {/* ==================== CTA ==================== */}
    <section className="cta-section" id="about">
      <div className="cta-content">
        <div className="badge cta-badge">AGENTIC AI</div>

            <div className="workflow-step">

              <div className="workflow-number">
                04
              </div>

              <h3>
                Result
              </h3>

              <p>
                APM combines the results and provides
                an intelligent response to the user.
              </p>

            </div>

          </div>

        </section>

        {/* ====================================================
            CTA
            ==================================================== */}

        <section
          className="cta-section"
          id="about"
        >
          <div className="cta-content">

            <div className="badge cta-badge">
              AGENTIC AI
            </div>

            <h2>
              Build better projects
              <span>
                with intelligent automation.
              </span>
            </h2>

            <p>
              Let your AI project team handle complexity
              while your human team focuses on delivering
              successful projects.
            </p>

            <Link
              to="/dashboard"
              className="primary-button cta-button"
            >
              Launch APM
              <span>→</span>
            </Link>

          </div>
        </section>

      </main>

      {/* ======================================================
          FOOTER
          ====================================================== */}

      <footer className="footer">

        <div className="footer-brand">

          <div className="brand-logo">
            AP
          </div>

          <div>
            <strong>APM</strong>

            <span>
              Agentic Project Management System
            </span>
          </div>

        </div>

        <p>
          © 2026 APM. Built with React, FastAPI & Agentic AI.
        </p>

      </footer>

    </div>
  );
}

// ============================================================
// Application Routes
// ============================================================

function AdminLoginPage({ onLoginSuccess }) {
  const navigate = useNavigate();
  return (
    <AdminLogin
      onLoginSuccess={(data) => {
        onLoginSuccess(data);
        navigate("/admin/dashboard");
      }}
      onBack={() => navigate("/")}
    />
  );
}

function AdminDashboardPage({ session, onLogout }) {
  const navigate = useNavigate();
  if (!session) return <Navigate to="/admin/login" replace />;
  return (
    <AdminDashboard
      session={session}
      onLogout={() => {
        onLogout();
        navigate("/");
      }}
      onBack={() => navigate("/")}
    />
  );
}

function App() {
  const [session, setSession] = useState(null);
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/admin/login" element={<AdminLoginPage onLoginSuccess={setSession} />} />
        <Route path="/admin/dashboard" element={<AdminDashboardPage session={session} onLogout={() => setSession(null)} />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/planning" element={<Planning />} />
        <Route path="/schedule" element={<Schedule />} />
        <Route path="/schedule/:projectId" element={<Schedule />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
