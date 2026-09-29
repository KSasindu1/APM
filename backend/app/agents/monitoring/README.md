# Monitoring & Reporting Agent (version 1)

This folder is self-contained. No API route or shared service is registered here. Only a trusted server-side caller should create `MonitoringPrincipal`; never construct it from an untrusted request body. Dates are project-local calendar dates and percentages are 0–100. Use the same date convention in all providers.

## Workflow and contracts

`MonitoringAgent(planning, actual, risk=None, summarizer=None, coordinator=None, settings=None)` exposes `await generate(MonitoringRequest(project_id, request_id, as_of), principal) -> MonitoringReport`. The principal needs project membership and `monitoring:generate` permission. Providers defined in `interfaces.py` must implement:

- `async get_plan(project_id) -> PlanningSnapshot`: `project_id`, `updated_on`, tasks (`task_id`, `title`, `start_date`, `due_date`, optional `expected_progress`, `priority`, `dependency_ids`), and optional milestones (`milestone_id`, `title`, `due_date`, nonempty `task_ids`).
- `async get_actual(project_id) -> ActualProjectSnapshot`: `project_id`, `updated_on`, tasks (`task_id`, `progress`, `status` in `not_started/in_progress/blocked/completed`, `updated_on`, optional `completed_on`). Completed means 100% plus completion date; not-started means 0%.
- `async get_risks(project_id) -> RiskSnapshot | None`: `project_id`, `updated_on`, risks (`risk_id`, `description`, `level` in `low/medium/high/critical`, optional `status` in `open/mitigated/closed`, optional `task_ids`). Only open high/critical risks count.
- Optional `async publish(CoordinatorReportMessage) -> None` on the Coordinator sink. This carries `message_type=monitoring.report.ready`, `sender=monitoring_reporting`, `recipient=coordinator`, `correlation_id=request_id`, `project_id`, and the whole report. There is no network transport in this folder; the Coordinator team wires one up if desired.
- Optional `async select_facts(list[SummaryFact]) -> SummarySelection` for an LLM. It receives **only computed fact sentences** and returns existing fact IDs; invalid/failed selections fall back to deterministic text. No LLM is required, and recommendations are separated into `suggested_actions`. A human project manager retains final decisions.

Provider results are revalidated, project IDs and reference IDs must match, and snapshot dates cannot be later than `as_of`. Missing actual tasks are flagged rather than assumed 0%; overall actual progress/variance remain null until all planned tasks have records. A missing risk provider means risk coverage is unknown, not zero known risks. Reports include `limitations`, `warnings` with codes and source references, task and milestone assessments, `metrics`, `status` (`ON_TRACK`, `AT_RISK`, `DELAYED`), `summary_method`, and `human_review_required=true`. `retrieved_evidence` remains empty: this component does not retrieve or make claims from risk knowledge documents.

## Rules (all configurable where a threshold is used)

- Expected progress uses `expected_progress` if supplied, otherwise linear calendar-day interpolation between task start and end. Variance = actual minus expected, in percentage points; overall progress is a simple unweighted average of all planned tasks. `weight` is reserved for later weighted progress.
- A task is overdue after its deadline if not completed. It is delayed if overdue, behind by at least `delay_threshold` points (default 10), behind with a deadline within `deadline_approaching_days` (default 3), or has a delayed/overdue dependency. Missing actual records are unknown and flagged, not called delayed.
- A milestone is `Delayed` if unfinished after its deadline or completed after it; `Completed` if all member tasks finished on time; `At Risk` if its due date approaches or a member is delayed/unknown; otherwise `On Track`.
- Project `DELAYED`: a high/critical-priority task is overdue, a milestone is delayed, or overall variance is at most `-delayed_variance` (default -25). Otherwise `AT_RISK`: missing data, empty plan, any overdue/delayed task, at-risk milestone, open high/critical risk, stale plan/actual, or variance at most `-at_risk_variance` (default -10). Otherwise `ON_TRACK`. Staleness threshold is `stale_after_days` (default 7). Risk snapshots can be absent/stale and are labelled as limitations. Status reasons are visible in metrics and warnings.

`adapters.py` converts the example team payloads to these contracts; pass an explicit `updated_on` date when the payload has no snapshot timestamp. Named sample milestones are grouped by name and assigned local IDs `M1`, `M2`, etc., with the latest member task deadline. If Planning publishes explicit milestones, consume `PlanningSnapshot` directly instead. `mock_data.SampleProviders` is opt-in sample input, **never** a silent fallback for failed real providers. Real providers should not return this sample for live projects.

## Demo and testing

From `backend/`, run `python -B -m pytest app/agents/monitoring/test_agent.py -p no:cacheprovider` to avoid generating new bytecode and pytest cache files. No dependency beyond `requirements.txt` is required. To try it in a local caller:

```python
from datetime import date
from app.agents.monitoring.agent import MonitoringAgent
from app.agents.monitoring.mock_data import SampleProviders
from app.agents.monitoring.schemas import MonitoringPrincipal, MonitoringRequest

sample = SampleProviders()  # demo only
agent = MonitoringAgent(planning=sample, actual=sample, risk=sample)
principal = MonitoringPrincipal(subject="manager", project_ids={"P001"}, permissions={"monitoring:generate"})
report = await agent.generate(MonitoringRequest(project_id="P001", request_id="demo-1", as_of=date(2026, 9, 18)), principal)
message = report.model_dump(mode="json")  # send to the Coordinator through a trusted caller
```

The Planning, actual-progress, and Risk team members need only implement their read provider methods (or map their payloads through `adapters.py`); the Coordinator member can implement the optional sink. Whoever adds the API route later must supply a **verified** principal and should keep authorization server-side. This folder does not change those teams' code.
