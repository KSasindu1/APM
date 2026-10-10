import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { checkPlanningHealth } from "../services/planningApi";

function Dashboard() {
  const [backendStatus, setBackendStatus] =
    useState("checking");

  useEffect(() => {
    let mounted = true;

    async function checkHealth() {
      try {
        await checkPlanningHealth();

        if (mounted) {
          setBackendStatus("online");
        }
      } catch {
        if (mounted) {
          setBackendStatus("offline");
        }
      }
    }

    checkHealth();

    return () => {
      mounted = false;
    };
  }, []);

  return (
    <div className="page dashboard-page">

      <div className="page-header">

        <span className="badge">
          APM DASHBOARD
        </span>

        <h2>
          Project command center
        </h2>

        <p>
          Create intelligent schedules, review
          project timelines, and monitor your
          AI-powered project workflow.
        </p>

      </div>

      <div className="dashboard-status-card">

        <div>
          <span className="dashboard-status-label">
            PLANNING SERVICE
          </span>

          <h3>
            Backend connection
          </h3>
        </div>

        <div
          className={`backend-status ${backendStatus}`}
        >
          <span></span>

          {backendStatus === "checking" &&
            "Checking..."}

          {backendStatus === "online" &&
            "Online"}

          {backendStatus === "offline" &&
            "Offline"}
        </div>

      </div>

      <div className="dashboard-grid">

        <Link
          to="/coordinator"
          className="dashboard-action-card"
        >

          <div className="dashboard-action-icon blue">
            AI
          </div>

          <h3>
            Project Coordinator
          </h3>

          <p>
            Describe your project, extract and
            review the details, then continue to
            Planning with your inputs prefilled.
          </p>

          <span>
            Open Coordinator →
          </span>

        </Link>

        <Link
          to="/schedule"
          className="dashboard-action-card"
        >

          <div className="dashboard-action-icon purple">
            S
          </div>

          <h3>
            View Schedule
          </h3>

          <p>
            Look up an existing project and
            review its timeline, critical path
            and task details.
          </p>

          <span>
            Open Schedule →
          </span>

        </Link>

        <Link to="/monitoring" className="dashboard-action-card">
          <div className="dashboard-action-icon blue">M</div>
          <h3>Monitor Projects</h3>
          <p>
            Record actual progress, review delays and risks,
            and generate reports for your own projects.
          </p>
          <span>Open Monitoring →</span>
        </Link>

      </div>

      <div className="dashboard-info-grid">

        <div className="dashboard-info-card">

          <span className="info-number">
            01
          </span>

          <h3>
            Planning Agent
          </h3>

          <p>
            Generates project tasks and schedules
            from natural-language requirements.
          </p>

        </div>

        <div className="dashboard-info-card">

          <span className="info-number">
            02
          </span>

          <h3>
            Risk Analysis
          </h3>

          <p>
            Highlights risky tasks and identifies
            potential schedule problems.
          </p>

        </div>

        <div className="dashboard-info-card">

          <span className="info-number">
            03
          </span>

          <h3>
            Critical Path
          </h3>

          <p>
            Shows the tasks that have the greatest
            impact on the project deadline.
          </p>

        </div>

      </div>

    </div>
  );
}

export default Dashboard;
