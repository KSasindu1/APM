# Monitoring & Reporting Agent

Backend-only reporting from real Planning baselines, separate actual-progress observations, and optional Risk data. No production sample projects, silent fallback data, registered Monitoring routes, or frontend integration. Imports do not start services or load models.

## Layout and import migration

The runtime consists of seven modules:

| Module | Responsibility |
| --- | --- |
| `__init__.py` | Public agent, request, report, principal, and settings exports |
| `agent.py` | Authorization, orchestration, persistence-before-publication, report reads |
| `schemas.py` | Validated models and provider/repository/sink protocols |
| `providers.py` | Planning HTTP/message, actual-progress, and Risk HTTP adapters |
| `analysis.py` | Deterministic progress, task, milestone, warning, and status calculations |
| `reporting.py` | Evidence-bound summaries, report construction, passive Planning feedback |
| `storage.py` | Supabase settings, report repository, read-only connection CLI |

`monitoring_reports.sql` remains beside the modules as a manual SQL reference. Tests live in `tests/`: `__init__.py`, fixture-only `conftest.py`, `test_agent.py`, `test_providers.py`, and `test_storage.py`. Fake inputs belong only in test fixtures.

Update imports; there are **no compatibility wrappers** for the replaced paths:

- `adapters.py` / `risk_api.py` → `providers.py`; `interfaces.py` → `schemas.py`.
- `progress_calculator.py` / `milestone_monitor.py` / `status_analyzer.py` → `analysis.py`.
- `report_generator.py` → `reporting.py`; `security.py` → `agent.py`.
- `supabase_repository.py` → `storage.py`, retaining `SupabaseMonitoringSettings` and `SupabaseMonitoringReportRepository` names and contracts. Production `mock_data.py` and `prompts.py` are removed.

## Inputs and providers

Dates are project-local calendar dates; JSON dates use `YYYY-MM-DD`. Percentages use 0–100. Provider protocols in `schemas.py` expose `get_plan(project_id)`, `get_actual(project_id)`, and `get_risks(project_id)`. Results and nested model instances are revalidated: project IDs must match, task references must exist, dependencies must be acyclic, and observations/updates cannot postdate the report's `as_of`. A current live fetch cannot substitute for a historical snapshot.

### Planning

- `PlanningAgentAPIProvider(client, *, timeout_seconds=30, today=date.today)` reads **`GET /api/planning/schedule/{project_id}`**; it never creates or changes a schedule. The caller owns the `httpx.AsyncClient`, supplies a trusted server-root `base_url`, and sets an optional `X-API-Key` header when Planning authentication is enabled.
- `PlanningMessageProvider(payload, *, received_on: date)` accepts the PDF-recommended envelope: `sender="planning_agent"`, `receiver="project_monitor_report_agent"`, `action="SCHEDULE_CREATED"`, caller-supplied `request_id`, and `data` containing the genuine Planning `ScheduleResponse`.

Both adapters consume `project_id`, `project_name`, `schedule`, and `analysis`, plus optional `warnings` and `responsible_ai`. Scheduled tasks supply `task_id`, `name`, `planned_start`, `planned_end`, `priority`, `dependencies`, and `status`. **Planning task status is not an actual execution measurement.**

The current Planning API supplies no source update timestamp or milestone definitions. `updated_on` stays `None` unless genuinely supplied; `fetched_on` separately records the fetch/receipt date. No milestones or source timestamps are fabricated. These coverage/freshness limitations are reported. The full schedule-response JSON, including `analysis`, `warnings`, and `responsible_ai`, is preserved in `report.planning_context`.

### Actual progress

`ActualProgressProvider(payload, *, observed_on: date)` and `adapt_actual_progress(payload, observed_on)` consume a **separate** project-scoped object with `project_id` and a `tasks` list. Each task supplies:

- `task_id`, execution `status` (`NOT_STARTED`/`PLANNED`, `IN_PROGRESS`, `BLOCKED`, or `COMPLETED`), and explicit `progress_percentage`.
- Optional `actual_start` date and `actual_end` date/null. `actual_end` maps to internal `completed_on`.
- Optional genuine `updated_on` dates at project/task level; otherwise the explicit caller observation date is used as the recording date, not claimed as a source-modification timestamp.

No percentage is inferred from status. `COMPLETED` requires 100% and `actual_end`; only completed tasks may have 100% or an end date. Not-started tasks require 0%. `ActualProjectSnapshot.observed_on` separately preserves the caller's observation date, so an observation received after `as_of` cannot be used in a historical report even when source dates are older. An undated task uses the observation date, never an older project update date; an inconsistent project/task date bundle is rejected. Supplied start/end/update dates must be consistent and not later than their observation. No actual-data API is assumed.

### Risk

`RiskAgentAPIProvider(client, *, project_information='', top_k=5, timeout_seconds=30, today=date.today)` posts to **`/api/risk/analyse`** with the requested `project_id`, `source_agent="Monitoring Agent"`, the monitoring-risk query, `project_information`, and `top_k` (integer 1–20). Use its own caller-owned client and trusted server-root `base_url`.

The adapter preserves source `risk_id`, maps source `task_id` to `task_ids`, and retains `impact_days`, risk text, confidence, evidence, source, affected area, and recommended action. A stable hash ID is used **only when the source omits a risk ID**; task links are never guessed from `affected_area`. Summary, evidence-used, and knowledge-source metadata are forwarded to the report. The snapshot date is the actual request date. HTTP/schema/project mismatch failures propagate; there is no fake-data fallback. Missing Risk data means unknown coverage, not proof of zero risks.

## Assessment and report behavior

- Planning API expected progress is a **linear calendar-day estimate**, not an exact target supplied by Planning. A custom `PlannedTask.expected_progress` can supply a target. Variance is actual minus expected in percentage points; overall progress is an unweighted task average.
- `MonitoringAgent(planning, actual=None, risk=None, summarizer=None, coordinator=None, settings=None, repository=None)` permits missing actual data. Missing tasks have `status="missing"`, null actual progress/variance, and contribute to `metrics.missing_actual_tasks`. Overall actual progress/variance remain null until every planned task has a fresh measurement or certain historical completion. Missing data alone produces `AT_RISK`, **not confirmed `DELAYED`**.
- Stale unfinished task records have `status="stale"`, null current actual progress/variance, `actual_is_stale=true`, and retained `last_observed_progress`. They count separately in `metrics.stale_actual_tasks`, cannot confirm current overdue/delay or generate overdue feedback, and produce `STALE_ACTUAL_TASK` warnings. Freshness is checked per task, not just at project level; known past completions and late starts remain historical facts.
- A passed baseline deadline without an actual record produces `PLANNED_DEADLINE_PASSED`: completion is unknown, not confirmed overdue. Planning `analysis.deadline_met=False` and source warnings indicate planning risk, not an observed execution delay.
- Observed unfinished tasks can be delayed by overdue dates, late starts, progress shortfalls, approaching deadlines, or delayed dependencies. A known late start records `start_delay_days`; supplied milestones are assessed from their member-task observations, never invented by the API adapters. Any fresh unfinished required member can confirm a passed milestone deadline even if other members are unknown; complete progress/completion still requires all members.
- With default settings, project `DELAYED` requires an observed overdue high/critical-priority task, a delayed milestone, or overall variance ≤ −25 points. Otherwise `AT_RISK` covers empty plans, missing/stale actuals, fresh blocked tasks, observed task delays/late starts, at-risk milestones, Planning warnings/deadline failure, stale Planning/actual data, open high/critical risks, or variance ≤ −10 points. Otherwise status is `ON_TRACK`. Thresholds are configurable in `MonitoringSettings`; missing source dates remain unknown freshness.
- Reports retain warnings/source references, limitations, computed facts, Planning context, and Risk metadata. Summaries are deterministic unless an optional `select_facts(facts)` summarizer selects valid existing fact IDs; it receives defensive copies so mutations cannot corrupt verified facts or deterministic fallback. No independent retrieval is performed (`retrieved_evidence` is empty). Risk recommendations are attributed suggestions, not Monitoring decisions; `human_review_required` is always true.

### Passive Planning feedback

Observed late starts, overdue unfinished work, and late completions produce messages in `report.planning_feedback` with `sender="project_monitor_report_agent"`, `receiver="planning_agent"`, `action="SCHEDULE_DELAY_DETECTED"`, and the report `request_id`. Message `data` includes `project_id`, `task_id`, `delay_days`, `reason` (`late_start`, `overdue`, or `completed_late`), and transitive dependent-task IDs in `affected_tasks`. Missing actual records do not generate these messages.

**These messages are report artifacts only, not posted requests.** Planning's current `/message` handler supports only `CREATE_SCHEDULE`, not rescheduling or this feedback action. There is no ready end-to-end feedback/rescheduling loop.

## Server-side usage

All identifiers and observations below come from real callers. Obtain `principal` from verified server-side identity and project membership, never from an untrusted request body. Run from `backend/` when using the explicit `.env` path.

### Live HTTP sources and saved reports

Pass a real actual-progress provider, or leave `actual_provider=None`. Keep Planning, Risk, and database clients separate so credentials cannot leak between services. Trusted URLs/keys come from server configuration, not request-supplied destinations.

```python
from datetime import date
import httpx
from app.agents.monitoring import MonitoringAgent, MonitoringRequest
from app.agents.monitoring.providers import PlanningAgentAPIProvider, RiskAgentAPIProvider
from app.agents.monitoring.storage import (
    SupabaseMonitoringReportRepository, SupabaseMonitoringSettings,
)

async def generate_live_report(
    principal, project_id, request_id, *, planning_base_url, risk_base_url,
    actual_provider=None, planning_api_key=None, project_information="", coordinator=None,
):
    settings = SupabaseMonitoringSettings.from_env(".env")
    planning_headers = {"X-API-Key": planning_api_key} if planning_api_key else {}
    async with (
        httpx.AsyncClient(base_url=planning_base_url, headers=planning_headers,
                          follow_redirects=False) as planning_client,
        httpx.AsyncClient(base_url=risk_base_url, follow_redirects=False) as risk_client,
        httpx.AsyncClient(follow_redirects=False) as db_client,
    ):
        agent = MonitoringAgent(
            planning=PlanningAgentAPIProvider(planning_client),
            actual=actual_provider,
            risk=RiskAgentAPIProvider(risk_client, project_information=project_information),
            repository=SupabaseMonitoringReportRepository(db_client, settings),
            coordinator=coordinator,
        )
        return await agent.generate(
            MonitoringRequest(project_id=project_id, request_id=request_id, as_of=date.today()),
            principal,
        )
```

### Receiving a real Planning message instead

This is caller integration, not a registered message route. `schedule_message` is the genuine `SCHEDULE_CREATED` envelope described above; optional `actual_payload` contains real observations, with an explicit `observed_on`. Risk, storage, and Coordinator integrations remain independently injectable.

```python
from datetime import date
from app.agents.monitoring import MonitoringAgent, MonitoringRequest
from app.agents.monitoring.providers import ActualProgressProvider, PlanningMessageProvider

async def generate_from_message(
    schedule_message, principal, project_id, request_id, *, received_on: date, as_of: date,
    actual_payload=None, observed_on: date | None = None,
    risk_provider=None, repository=None, coordinator=None,
):
    if actual_payload is not None and observed_on is None:
        raise ValueError("Actual observations require observed_on")
    actual = (
        ActualProgressProvider(actual_payload, observed_on=observed_on)
        if actual_payload is not None else None
    )
    agent = MonitoringAgent(
        planning=PlanningMessageProvider(schedule_message, received_on=received_on),
        actual=actual, risk=risk_provider, repository=repository, coordinator=coordinator,
    )
    return await agent.generate(
        MonitoringRequest(project_id=project_id, request_id=request_id, as_of=as_of), principal,
    )
```

## Persistence and access

`storage.py` stores the whole report JSON plus matching `report_id`, `project_id`, `request_id`, and `generated_at` row metadata in `public.monitoring_reports`; reads validate both the report and row metadata. It stores report snapshots, not Planning tasks or actual-progress source records.

`SupabaseMonitoringSettings.from_env(env_file=None)` reads only process environment unless an explicit file is supplied; process values override dotenv values. Required variables are `SUPABASE_URL` (HTTPS server root) and backend-only `SUPABASE_SECRET_KEY`; `SUPABASE_MONITORING_REPORTS_TABLE` defaults to `monitoring_reports`. The backend `.env` remains untouched and Git-ignored. Never expose/log its credentials or send them to Planning, Risk, or the frontend. Modern `sb_secret_` keys use `apikey`; legacy service-role keys additionally use Bearer authorization. Server credentials can bypass RLS, so application authorization is mandatory.

- `generate(request, principal)` and repository `save(report, principal)` require project membership and `monitoring:generate`; save also verifies the report author.
- `get_report(project_id, report_id, principal)` / `list_reports(project_id, principal, limit=20)` and repository `get` / `list` require project membership and `monitoring:read`. Generation permission does not imply read permission. Reads are project-scoped; list limits are 1–100.
- An injected repository is awaited **before** optional Coordinator `publish(CoordinatorReportMessage)`. A failed/timed-out save prevents publication. The sink is caller-provided; no Coordinator network transport is implemented here.

`monitoring_reports.sql` is not auto-applied. It references a JSONB report table, project/date index, and enabled RLS without public policies, grants, or a `projects` foreign key. Verify any existing schema separately: `IF NOT EXISTS` does not reconcile differing definitions. Persistence does not supply real-user authentication, register routes, or connect the frontend.

## Validation

From `backend/`, run the migrated offline tests without bytecode or pytest cache output:

```sh
python -B -m pytest app/agents/monitoring/tests -p no:cacheprovider
```

Fixtures and mocked HTTP cover agent behavior, provider contracts, authorization, storage validation, and save-before-publish ordering. They do **not** establish a tested live Planning/Risk integration, live database writes, or a working feedback loop.

For a backend operator's **read-only network** check of the configured Supabase table:

```sh
python -B -m app.agents.monitoring.storage --env-file .env
```

This selects at most one report ID without generating reports or inserting/updating/deleting rows. Success confirms readability with the configured server credential, not write permission, RLS correctness, or user authorization.
