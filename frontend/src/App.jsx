import { useState } from "react";
import { BrowserRouter, Routes, Route, Link, Navigate, useNavigate } from "react-router-dom";
import AdminLogin from "./admin/AdminLogin.jsx";
import AdminDashboard from "./admin/AdminDashboard.jsx";
import Dashboard from "./pages/Dashboard";
import Planning from "./pages/Planning";
import Schedule from "./pages/Schedule";
import Coordinator from "./pages/Coordinator";
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
          <a href="#features">Features</a>
          <a href="#agents">AI Agents</a>
          <a href="#about">About</a>
        </nav>
        <div className="nav-actions">
          <Link to="/admin/login" className="nav-button">Admin Login</Link>
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

function AdminLoginPage({ onLoginSuccess }) {
  const navigate = useNavigate();
  return <AdminLogin onLoginSuccess={(data) => { onLoginSuccess(data); navigate("/admin/dashboard"); }} onBack={() => navigate("/")} />;
}

function AdminDashboardPage({ session, onLogout }) {
  const navigate = useNavigate();
  if (!session) return <Navigate to="/admin/login" replace />;
  return <AdminDashboard session={session} onLogout={() => { onLogout(); navigate("/"); }} onBack={() => navigate("/")} />;
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
        <Route path="/coordinator" element={<Coordinator />} />
        <Route path="/schedule" element={<Schedule />} />
        <Route path="/schedule/:projectId" element={<Schedule />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
