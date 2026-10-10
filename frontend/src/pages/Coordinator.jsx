import { useState } from "react";
import { Link } from "react-router-dom";
import { interpretProjectInput, submitCoordinatorRequest } from "../services/coordinatorApi";
import "./Coordinator.css";

const initial = { project_name: "", objective: "", start_date: "", deadline: "", message: "Create a schedule and assess deadline feasibility.", use_llm: true };
const example = "Project: Inventory System. Build a web application to manage stock and sales. Start date: 2026-10-12. Deadline: 2026-12-12. We have 3 developers and 1 tester. Prioritize stock tracking and leave time for testing.";

export default function Coordinator() {
  const [mode, setMode] = useState("natural");
  const [text, setText] = useState("");
  const [form, setForm] = useState(initial);
  const [resources, setResources] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [interpretation, setInterpretation] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));

  async function interpret() {
    setBusy("interpret"); setError(""); setResult(null);
    try {
      const data = await interpretProjectInput(text);
      setInterpretation(data); setForm({ ...initial, ...data.draft });
      setResources(data.draft.resources || []); setTasks([]); setMode("form");
    } catch (exc) { setError(exc.message); }
    finally { setBusy(""); }
  }

  async function submit(event) {
    event.preventDefault(); setError(""); setResult(null);
    if (form.deadline < form.start_date) { setError("Deadline must be on or after the start date."); return; }
    setBusy("plan");
    try {
      setResult(await submitCoordinatorRequest({
        project_name: form.project_name.trim(), objective: form.objective.trim(),
        start_date: form.start_date, deadline: form.deadline, message: form.message.trim(),
        use_llm: form.use_llm, resources, tasks, risks: [],
      }));
    } catch (exc) { setError(exc.message); }
    finally { setBusy(""); }
  }

  function rows(items, setItems, type) {
    return items.map((item, index) => <div className="coord-row" key={item.resource_id || item.task_id}>
      <label>{type === "resource" ? "Role or resource" : "Task name"}<input required maxLength={200} value={item.name} onChange={(e) => setItems(items.map((row, i) => i === index ? { ...row, name: e.target.value } : row))} /></label>
      <label>{type === "resource" ? "Capacity" : "Days"}<input required type="number" min={1} max={type === "resource" ? 100 : 365} value={item.capacity ?? item.duration_days} onChange={(e) => setItems(items.map((row, i) => i === index ? { ...row, [type === "resource" ? "capacity" : "duration_days"]: e.target.value === "" ? "" : Number(e.target.value) } : row))} /></label>
      <button type="button" className="coord-remove" aria-label={`Remove ${type} ${index + 1}`} onClick={() => setItems(items.filter((_, i) => i !== index))}>×</button>
    </div>);
  }

  return <main className="coordinator-page">
    <header className="coord-header"><Link to="/dashboard">← Dashboard</Link><span className="coord-tag">APM / PROJECT COORDINATOR</span><h1>Let’s plan your next project.</h1><p>Share the goal. Set the boundaries. Get a schedule and a recommendation you can act on.</p></header>
    <div className="coord-layout">
      <section className="coord-window" aria-labelledby="input-title">
        <div className="coord-window-bar"><i /><span id="input-title">Project input window</span><small>01 / INPUT</small></div>
        <div className="coord-tabs" role="group" aria-label="Input method"><button type="button" aria-pressed={mode === "natural"} disabled={!!busy} onClick={() => setMode("natural")}>Describe in your words</button><button type="button" aria-pressed={mode === "form"} disabled={!!busy} onClick={() => setMode("form")}>Project details</button></div>
        {mode === "natural" ? <div className="coord-body">
          <span className="coord-tag">NATURAL LANGUAGE INPUT</span><h2>What would you like to build?</h2><p>Include the project name, goal, dates, and team. We’ll turn your description into editable project details.</p>
          <label htmlFor="coord-description">Your project description</label><textarea id="coord-description" className="coord-prompt" value={text} maxLength={5000} disabled={!!busy} onChange={(e) => setText(e.target.value)} placeholder={example} />
          <div className="coord-prompt-footer"><span>{text.length} / 5,000 characters</span><button className="coord-text-button" type="button" disabled={!!busy} onClick={() => setText(example)}>Try an example</button></div>
          <div className="coord-hint"><strong>For accurate extraction</strong><p>Use dates like 2026-10-12 and a name like “Project: Inventory System”. Relative dates such as “next month” need manual entry.</p><p>Extracting again replaces the current draft, including edited tasks and resources.</p></div>
          <button className="coord-primary" type="button" disabled={!!busy || text.trim().length < 10} onClick={interpret}>{busy === "interpret" ? "Reading your description…" : "Extract project details →"}</button><p className="coord-footnote">Local rule-based NLP. Review details before sending to Planning.</p>
        </div> : <form className="coord-body" onSubmit={submit}><fieldset disabled={!!busy}>
          <legend>{interpretation ? "Review your extracted project" : "Tell us about your project"}</legend>
          {interpretation && <div className="coord-hint"><strong>Details extracted — ready for review</strong><p>{interpretation.missing_fields.length ? `Please complete: ${interpretation.missing_fields.map((key) => key.replaceAll("_", " ")).join(", ")}.` : "Check the details before generating your schedule."}</p>{interpretation.notes.map((note) => <p key={note}>{note}</p>)}</div>}
          <label>Project name<input required maxLength={200} value={form.project_name} onChange={(e) => update("project_name", e.target.value)} placeholder="e.g. Inventory System" /></label>
          <label>Project objective<textarea required minLength={10} maxLength={3000} value={form.objective} onChange={(e) => update("objective", e.target.value)} placeholder="What will the project deliver?" /></label>
          <div className="coord-dates"><label>Start date<input type="date" required value={form.start_date} onChange={(e) => update("start_date", e.target.value)} /></label><label>Deadline<input type="date" required min={form.start_date || undefined} value={form.deadline} onChange={(e) => update("deadline", e.target.value)} /></label></div>
          <label>Planning instructions<textarea required maxLength={1500} value={form.message} onChange={(e) => update("message", e.target.value)} /></label>
          <div className="coord-section-title"><h3>Your team</h3><button className="coord-text-button" type="button" onClick={() => setResources([...resources, { resource_id: `R-${crypto.randomUUID().slice(0, 8)}`, name: "", capacity: 1 }])}>+ Add resource</button></div>
          {!resources.length && <p className="coord-muted">Add roles and capacity if known.</p>}{rows(resources, setResources, "resource")}
          <details className="coord-task-details"><summary>Known tasks (optional)</summary><p className="coord-muted">Leave empty to let Planning generate tasks. New tasks have no dependencies and medium priority.</p>{rows(tasks, setTasks, "task")}<button className="coord-text-button" type="button" onClick={() => setTasks([...tasks, { task_id: `T-${crypto.randomUUID().slice(0, 8)}`, name: "", duration_days: 1, priority: "MEDIUM", dependencies: [] }])}>+ Add task</button></details>
          <label className="coord-checkbox"><input type="checkbox" checked={form.use_llm} onChange={(e) => update("use_llm", e.target.checked)} />Use AI to generate tasks when none are supplied</label><p className="coord-muted">With AI disabled and no tasks, Planning uses its fallback template.</p>
          <button className="coord-primary" type="submit">{busy === "plan" ? "Generating your schedule…" : "Send to Planning →"}</button>
        </fieldset></form>}
      </section>
      <aside className="coord-sidebar"><span className="coord-tag">HOW IT WORKS</span><h2>One request.<br />A clear next step.</h2><ol>{[["Capture your project", "Use the form or describe it naturally."], ["Review the details", "Check goals, dates, and team capacity."], ["Coordinate Planning", "Approved details become a structured planning request."], ["Make a decision", "Review the schedule and deadline recommendation."]].map(([title, description]) => <li key={title}><strong>{title}</strong><p>{description}</p></li>)}</ol><div className="coord-scope"><span>CONNECTED WORKFLOW</span><strong>Planning & Scheduling</strong><p>Includes Planning’s existing risk workflow. Actual-progress monitoring is not performed.</p></div></aside>
    </div>
    {busy && <p className="coord-feedback" role="status">{busy === "interpret" ? "Extracting project details…" : "Waiting for Planning to generate and save your schedule…"}</p>}{error && <p className="coord-error" role="alert">{error}</p>}
    {result && <section className="coord-result" aria-live="polite"><span className="coord-tag">02 / RECOMMENDATION</span><h2>{result.status === "completed" ? "Your project recommendation" : result.status === "failed" ? "Planning is unavailable" : "A few details need attention"}</h2><p className="coord-recommendation">{result.recommendation || result.error}</p>{result.clarification_questions.map((question) => <p key={question}>{question}</p>)}
      {result.planning_result && <><div className="coord-metrics"><div><span>Planned duration</span><strong>{result.planning_result.analysis.total_duration_days} days</strong></div><div><span>Available time</span><strong>{result.planning_result.analysis.available_duration_days} days</strong></div><div><span>Deadline</span><strong>{result.planning_result.analysis.deadline_met ? "Within target" : `${result.planning_result.analysis.delay_days} days over`}</strong></div></div><div className="coord-table"><table><caption>Generated project schedule</caption><thead><tr><th>Task</th><th>Start</th><th>End</th><th>Critical</th></tr></thead><tbody>{result.planning_result.schedule.map((task) => <tr key={task.task_id}><td>{task.name}</td><td>{task.planned_start}</td><td>{task.planned_end}</td><td>{task.is_critical ? "Yes" : "—"}</td></tr>)}</tbody></table></div><Link to={`/schedule/${result.project_id}`}>Open saved schedule →</Link></>}
      <ul>{result.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>{result.warnings.length > 0 && <details><summary>Review notes ({result.warnings.length})</summary><ul>{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></details>}<details><summary>Planning request and response</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>
    </section>}
  </main>;
}
