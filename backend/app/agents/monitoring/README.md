# Monitoring & Reporting Agent

Monitoring compares a real Planning baseline with separately supplied actual-progress observations and optional live Risk analysis. It reports missing/stale coverage instead of fabricating progress or treating unavailable risks as zero. Reports and feedback require human review.

## Shared group-project backend and frontend

Monitoring is registered in `app.main` alongside the existing Planning and Risk routers. Follow the shared setup in [the developer guide](../../../../docs/DEV_GUIDE.md): run **one backend** and **one frontend**, not separate agent servers or Docker services.

In PowerShell, start from the project root:

```powershell
cd backend
```

Reuse `backend/venv` if it exists. **Only if it is missing**, create an isolated environment without system site packages:

```powershell
python -m venv venv
```

Then activate the existing environment, install the backend requirements into it, and start the shared backend on port 8000:

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

A virtual environment isolates Python packages; it does **not** guarantee a fix for Windows policy blocking native extensions such as `orjson` or Torch used by Risk initialization. The known Windows Risk/Torch policy block may prevent the shared backend from restarting. If an import is blocked, report the policy error rather than changing Windows security settings. Before restarting the shared backend, stop its existing process/listener on port 8000 first (normally Ctrl+C in its owning terminal); do not start a second backend or assume an old listener has loaded new configuration.

In a **new terminal**, start from the project root and run the existing frontend (dependencies are already installed; no `npm install` is needed):

```powershell
cd frontend
npm run dev
```

The shared backend is available at `http://127.0.0.1:8000` (Swagger at `/docs`); the existing Vite frontend normally uses `http://localhost:5173`.

All agent pages belong to that existing React app (Vite normally uses port 5173). All registered agent APIs use the shared backend on port 8000:

- Planning: `/api/planning/...`
- Risk: `/api/risk/...`
- Monitoring: `/api/monitoring/...`

Shared Monitoring uses the application's verified user/admin login. It reads Planning through a request-owned repository adapter with fresh owner filters, and calls Risk's existing HTTP contract **inside the same application** through `httpx.ASGITransport`. No separate Monitoring process, loopback network request, extra browser token, or Planning service key is needed for this browser flow. The internal Risk base URL is only an ASGI origin, not a listening port or fabricated source. Supabase uses its normal external client, never the internal application transport.

Public, configuration-only probe:

```sh
curl http://127.0.0.1:8000/api/monitoring/health
```

The shared application retains its existing startup/configuration requirements and Risk model initialization. Install/configure the group's backend dependencies before launching it; Monitoring does not bypass failures in other agents, migrate the database, or initialize an extra agent server. For non-loopback deployment, use an HTTPS reverse proxy and appropriate operational access controls; do not expose plaintext bearer-token traffic.

### Optional standalone compatibility

The isolated `app.agents.monitoring.api:app` entry point remains available for development/testing, but is **not the group-project startup**. In that mode only, Planning/Risk must be reachable services and their `MONITORING_*_BASE_URL` values must be configured. The shared app requires no second Monitoring port.

Health returns exactly these fields:

- `agent`: `Monitoring & Reporting Agent`.
- `status`: `ready` when authentication and live Planning are configured, otherwise `configuration_required`.
- `capabilities`: booleans `auth_configured`, `planning_configured`, `risk_configured`, `storage_configured`.
- `live_sources_verified`: always `false`.

Health makes **no external calls** and echoes no keys, tokens, URLs, or identities. `ready` describes configuration, **not a verified connection**. Optional Risk/storage configuration is not required for readiness. Shared mode reports account authentication as configured, without testing JWT settings or database availability. In standalone mode, missing or invalid identity mappings do not prevent health probes; private routes fail closed with 503.

## Backend configuration and credentials

`api.py` explicitly resolves and reads `backend/.env`, independent of the current working directory. `create_app(env_file=...)` and `MonitoringAPISettings.from_env(env_file=...)` can select a different local file. Process environment overrides file values; dotenv interpolation is disabled. Configuration is read without changing the file/environment or printing its values.

| Variable | Meaning |
| --- | --- |
| `MONITORING_AUTH_IDENTITIES` | Standalone mode only: private mapping of scoped access tokens to verified identities; not used by shared browser Monitoring |
| `MONITORING_PLANNING_BASE_URL` | Standalone mode only: trusted Planning server root; not needed for shared-app calls |
| `PLANNING_API_KEY` | Existing private Planning service key; shared browser Monitoring uses owner-filtered repository reads instead |
| `MONITORING_PLANNING_API_KEY` | Optional standalone-mode Planning-provider key; never supplied by the browser or sent to Risk/storage |
| `MONITORING_RISK_BASE_URL` | Standalone mode only: trusted Risk server root; not needed for shared-app calls |
| `MONITORING_RISK_FAILURE_POLICY` | `fail` by default; `report_unknown` explicitly permits an unavailable-Risk report with coverage/warnings |
| `MONITORING_PROVIDER_TIMEOUT_SECONDS` | Provider/storage/LLM timeout, default `30`; finite number greater than 0 and at most 300 |
| `GROQ_API_KEY` | Existing backend-only Groq credential, also used directly by Monitoring for summary fact selection |
| `GROQ_MODEL` | Existing Groq model setting; defaults to `openai/gpt-oss-120b`; must support structured JSON-schema output |
| `MONITORING_LLM_ENABLED` | `true` by default; `false` disables Monitoring's own Groq call without changing Risk settings; `1`/`0` are also accepted |
| `MONITORING_CORS_ORIGINS` | Comma-separated explicit frontend origins; defaults to `http://localhost:5173,http://127.0.0.1:5173` |
| `SUPABASE_URL` | Optional existing Supabase HTTPS server root |
| `SUPABASE_SECRET_KEY` | Optional existing **backend-only** Supabase credential |
| `SUPABASE_MONITORING_REPORTS_TABLE` | Optional public-schema table name; defaults to `monitoring_reports` |
| `SUPABASE_MONITORING_OBSERVATIONS_TABLE` | Optional public-schema table name; defaults to `monitoring_observations`; uses the same backend Supabase settings, not a separate observation URL |

Provider URLs must be HTTPS server roots, or explicitly `http://localhost` / `http://127.0.0.1` with an optional development port. Credentials, paths, queries, fragments, and external plaintext HTTP are rejected. Destinations cannot be supplied in API bodies. Redirects are not followed, proxy-environment configuration is not used, and each request owns separate Planning, Risk, Groq, and database clients. Browser Authorization is never forwarded upstream.

In shared mode, the application's existing `CORS_ORIGINS` middleware governs browser access. `MONITORING_CORS_ORIGINS` applies only to the optional standalone application, which accepts explicit origins, Authorization/Content-Type headers and GET/POST methods with `allow_credentials=false`. CORS is browser policy, **not authorization**. Invalid URLs/CORS/timeouts/policies produce sanitized configuration errors. Missing or invalid optional Supabase configuration leaves storage unavailable rather than preventing health or unsaved report generation.

### Shared signed-in access

Sign in through `/login`. Users may monitor their owned, existing projects; a verified configured or managed administrator may monitor all existing projects, including unassigned ones. The backend derives `monitoring:read` and `monitoring:generate` from that access policy, not browser role fields or imported messages. Public signup creates ordinary users only.

The shared session returns managed-account UUIDs as subjects, or `bootstrap-admin` for the configured administrator. Its project list uses owner-filtered storage. Each subsequent project request refreshes account state and ownership before accessing sources or Monitoring storage; foreign/unassigned/missing projects return the same 404 to users. Rechecks before save and response release prevent stale authorization from publishing results after slow work, but are not atomic transactions across databases. A write already completed during reassignment cannot be rolled back by these checks.

Monitoring retains its existing ASCII identifier contract: `[A-Za-z0-9][A-Za-z0-9_.-]{0,99}` for project and task identifiers. Accessible projects with incompatible legacy IDs are omitted from the chooser with an optional `unsupported_project_count` notice, not renamed or deleted; compatible projects remain usable. A selected baseline with incompatible task IDs still fails validation rather than inventing replacements.

Shared browser routes reject Planning service keys and standalone Monitoring tokens as identity. No browser bearer is forwarded to Risk or storage. See [Authentication and authorization](../../../../docs/AUTHENTICATION.md) for account setup and remaining source-API deployment boundaries.

### Scoped access tokens — standalone only

For a separate standalone deployment, an operator must generate cryptographically random access tokens, privately maintain `MONITORING_AUTH_IDENTITIES`, verify each subject's real project membership, and deliver only the corresponding scoped token to that user. There is no default token, hardcoded identity, wildcard scope, public provisioning route, or automatically discovered project list.

The JSON object's **keys** are bearer-token strings of at least 32 characters, using HTTP bearer-token characters. Each **value** has exactly:

- `subject`: an authorized user's identifier.
- `project_ids`: a JSON array of explicitly authorized project identifiers.
- `permissions`: a JSON array containing only `monitoring:read` and/or `monitoring:generate`.

Identifiers match `[A-Za-z0-9][A-Za-z0-9_.-]{0,99}`. Scope arrays must contain unique strings, with no wildcards or surrounding whitespace. Duplicate JSON members, unknown permissions/fields, malformed identities, and short tokens invalidate the mapping; the entire private API then fails closed. Empty scope arrays grant no project operations.

`MonitoringTokenAuthenticator` verifies the configured token and supplies the server-side `MonitoringPrincipal`. Identity/project membership is **never accepted from a report body**. Read and generate permissions are separate; neither implies the other. Changing environment configuration requires restarting/recreating this service. This temporary mapping does not implement token expiry, Supabase user-JWT verification, login, or automatic membership synchronization; rotate/revoke tokens operationally.

This mapping applies only to the isolated service. The shared application instead uses verified account/admin JWTs and stored ownership; its browser page provides no manual standalone-token form.

**A standalone service token is not a Supabase secret, Groq key, or automatic shared-app grant.** Keep provider/database credentials only on the backend. Do not put secrets in source control, frontend build variables, screenshots, logs, reports, or this document. Never send an upstream API key as the Monitoring bearer token.

#### Operator: issue a token for an existing authorized user

1. Verify the user's real identity and access to **existing real Planning project IDs**, using the team's trusted membership records. Do not invent a project ID, copy a sample ID, or grant access merely because someone requests it. This procedure does not create accounts, memberships, or Planning projects.
2. In a private local terminal, from `backend/` with its existing virtual environment activated, generate a token:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   This command prints a secret for the operator to capture securely **locally**. Do not run it through an agent/shared transcript, paste its output into chat, or record it in logs/screenshots.
3. Privately edit `backend/.env` to add the verified token mapping. The following is a **template only**; **all angle-bracket placeholders must be replaced** and none is a working default:

   ```dotenv
   MONITORING_AUTH_IDENTITIES='{"<GENERATED_TOKEN>":{"subject":"<VERIFIED_USER_ID>","project_ids":["<AUTHORIZED_PROJECT_ID>"],"permissions":["monitoring:read","monitoring:generate"]}}'
   ```

   Use the generated token, the verified user's identifier, and the exact existing Planning project ID whose access you checked. Grant only required permissions/projects; preserve other authorized entries when updating an existing mapping. Keep this file private and out of source control. If the process environment already sets `MONITORING_AUTH_IDENTITIES`, reconcile that private override because it takes precedence over `.env`.
4. Coordinate a restart of the separate standalone service with its operator, stopping the old listener first. Restarting the shared `app.main` does not activate this mapping for shared Monitoring. Health remains configuration-only, not proof of live Planning/Risk/storage access.
5. Deliver only this caller's scoped token through an approved private channel for its standalone API client. The shared React page uses account login instead and cannot accept these standalone grants. Do not put tokens in browser storage, provider-key fields, URLs, logs, or exports.

## Monitoring frontend

The Monitoring page is registered in the shared React app at `/monitoring` and `/monitoring/:projectId`. In a new terminal at the project root, run the existing frontend on Vite's normal port 5173:

```powershell
cd frontend
npm run dev
```

Monitoring honors the existing `VITE_API_BASE_URL` when explicitly set to an API **server root** (not `/api/monitoring`). Otherwise, browser requests default to the **current browser origin**. The existing Vite development server proxies `/api/monitoring` to `http://127.0.0.1:8000`, so the browser stays on its actual frontend origin on port 5173, including literal IPv6 `http://[::1]:5173`, without a cross-origin Monitoring request. Outside a browser (for example, Node helpers/tests), the default remains `http://127.0.0.1:8000`. No separate Monitoring API environment variable or frontend server is needed; the shared backend/frontend ports remain 8000/5173.

Vite's development proxy is **not** included in a deployed frontend build. If the deployed frontend and API do not already share an origin, configure a reverse proxy for `/api/monitoring` or explicitly set `VITE_API_BASE_URL` to the API server root. An explicit cross-origin API root requires the shared backend's `CORS_ORIGINS` to allow the exact frontend origin (scheme, hostname, and port); `localhost`, `127.0.0.1`, and `[::1]` are distinct origins. Use HTTPS outside loopback development; never place access tokens or provider/database keys in frontend environment variables.

Sign in normally, then use **Monitor Projects** on the user dashboard, **Monitor all projects** on the admin dashboard, or **Monitor progress** on a schedule. The route verifies your account/admin session before automatic Monitoring verification; the chooser shows only server-authorized projects. No additional token is entered. Load a live baseline or import a genuine dated message for that owned/existing project; actual rows start blank and require explicit inclusion/status/progress. Saving is opt-in and configuration-gated. Imported data never grants ownership.

Reports distinguish confirmed task delays from upstream dependency concerns and show source coverage, unknown/stale observations, evidence, and actual-observation provenance. JSON/Coordinator exports preserve actual context while removing credential-like fields; exports are passive artifacts, not network dispatch. Legacy reports without coverage remain explicitly unknown.

### Report review and observation workflows

- **Saved comparison:** select another authorized snapshot from the loaded history. Deltas are current minus selected; progress uses percentage points. Chronology, task scope, baseline dates/criticality/dependencies, supplied targets, calculation methods, and fresh actual coverage gate each metric separately. Unknown mixed-target provenance disables target-dependent comparisons rather than inventing a matching baseline. Missing risks are labelled **no longer returned**, never automatically resolved.
- **Snapshot charts:** independent actual/expected bars and an accessible table use saved reports only. The latest verified snapshot per as-of date is retained; baseline changes, missing data, and omitted invalid records are disclosed. History is limited to 20 loaded reports, not all-time history or a forecast.
- **Review alerts:** evidence-bound task and risk prompts link back to report assessments. Only explicit source recommendations are shown; ambiguous risk references do not mix recommendations. No email, notifications, Planning update, or Coordinator message is dispatched.
- **Exports:** task CSV includes every task regardless of screen filters, quotes text safely, escapes spreadsheet formula prefixes, and leaves unknown measurements blank. **Print / Save as PDF** opens the browser print dialog; the user chooses Save as PDF. Print includes all assessed tasks and closed risk/evidence disclosures while excluding access tokens, session/input/history controls, and raw source contexts. Downloads contain private project information and must be shared carefully.
- **Advanced observation intake:** paste a real `ActualProgress` object, enter a caller-claimed source and explicit observation date, and choose validation-only or explicit save. Genuine `updated_on` dates are preserved and absent dates are never manufactured. `PLANNED` is accepted as the documented not-started alias; manual observation rows still require an explicit observed status. A receipt is not adopted automatically.
- **Receipt review:** stored history requires read permission and separately configured observation storage. A persisted receipt requires protected GET review before explicit adoption, then generation sends only `observation_id`. An unsaved receipt is adopted as dated inline actuals, not a durable ID. UTC acceptance timestamps and project-local observation dates are independent calendar bases.
- **Safe state:** project/source/date/observation edits discard obsolete selections and cancel local pending work. Invalid report-source/evidence project metadata is rejected before display; comparison and alert helper rejection is contained with a safe notice. Local cancellation cannot undo a server save that may already have completed; inspect stored history before retrying an ambiguous request.

Shared authentication requires the existing account/admin configuration and Planning ownership setup, not a Monitoring identity mapping. Refreshing access clears unsaved inputs and reports; changing project/routes/session also cancels pending local work. A 401 signs out the originating session, while 403/404 clear private Monitoring content and allow revalidation without automatically logging out. Observation saving still requires the reviewed manual `monitoring_observations` SQL migration; a health configuration flag does not verify table readiness or live writes.

From `frontend/`, offline client/helper validation and build:

```sh
node --test src/services/monitoringApi.test.js src/services/monitoringView.test.js src/services/accountMonitoring.test.js src/components/navigation.test.js
node --input-type=module -e "import { build } from 'vite'; await build({ envDir: '.monitoring-validation.local/empty-env' });"
```

Create an empty `frontend/.monitoring-validation.local/empty-env` directory before that environment-isolated build and remove it afterward. These tests do not establish browser interaction or live source availability.

### Browser connection troubleshooting

- A reachable backend does not prove browser access: direct cross-origin requests from literal `http://[::1]:5173` have been confirmed to fail CORS. For the normal local workflow, leave `VITE_API_BASE_URL` unset so Monitoring uses the current browser origin and Vite's proxy. An explicit API root bypasses that same-origin route; check its value and the shared application's exact-origin CORS configuration, not standalone `MONITORING_CORS_ORIGINS`.
- After a Vite proxy/configuration change, if Vite has not automatically restarted, stop and restart `npm run dev` in the existing frontend terminal, then reload the page. Restart Vite after changing frontend environment configuration too.
- On the **actual frontend origin shown in the browser**, request **`GET /api/monitoring/health`** (for example, `http://[::1]:5173/api/monitoring/health` when that is your frontend address). This public route needs **no token or Authorization header**. A JSON health response checks the frontend-to-backend route; an HTML page, proxy error, or connection failure does not. Compare with the direct backend probe at `http://127.0.0.1:8000/api/monitoring/health` to distinguish proxy/browser routing from backend reachability.
- `ready` or configured capability flags do **not** verify live sources. If health works but account verification fails, 401 means missing/invalid/expired login; sign in again. A 503 can mean unavailable authentication/project storage or source/storage configuration. A 403 means denied project/operation access; a 404 can mean the project is no longer accessible or a saved record is absent. Refresh project access after ownership changes. Do not substitute a Supabase/Groq credential or fabricate projects to clear an error.

## Frontend/backend contract

All routes are below `/api/monitoring`. Except `/health`, shared callers send their verified account/admin bearer; standalone callers send their separately configured scoped token. Private responses use `Cache-Control: no-store`.

| Method and path | Permission | Result |
| --- | --- | --- |
| `GET /health` | Public | Configuration readiness only |
| `GET /session` | Verified identity | `{subject, project_ids, permissions}`; IDs/permissions sorted; optional incompatible-project count in shared mode; no token |
| `GET /projects/{project_id}/baseline` | Project scope + `monitoring:read` | Direct canonical `PlanningSnapshot` from owner-filtered storage in shared mode, Planning API in standalone mode |
| `POST /projects/{project_id}/observations` | Project scope + `monitoring:generate` | Direct `ObservationReceipt`; validation-only by default; explicit save requires successful storage |
| `GET /projects/{project_id}/observations?limit=20` | Project scope + `monitoring:read` | List of saved receipts; limit 1–100 |
| `GET /projects/{project_id}/observations/{observation_id}` | Project scope + `monitoring:read` | Saved receipt, or 404 |
| `POST /reports` | Body project scope + `monitoring:generate`; also `monitoring:read` when using `observation_id` | Direct `MonitoringReport`, **not** a status/report wrapper |
| `GET /projects/{project_id}/reports?limit=20` | Project scope + `monitoring:read` | List of stored `MonitoringReport`; limit 1–100 |
| `GET /projects/{project_id}/reports/{report_id}` | Project scope + `monitoring:read` | Stored `MonitoringReport`, or 404 |
| `GET /projects/{project_id}/reports/{report_id}/coordinator-message` | Project scope + `monitoring:read` | `CoordinatorReportMessage` from that authorized stored report |

Authorization is checked **before** provider/client factories, Planning/Risk analysis, or Monitoring report/observation storage access. Shared identity and ownership verification itself uses account and Planning storage. Reads/export revalidate stored models and requested project/report binding; a mismatched or corrupt stored report is not returned.

The frontend uses the shared backend address, obtains `/session` with the current login, scopes its project selector/actions to that response, and sends real source identifiers and observations. Health does not supply projects or prove connectivity. The service does not provision accounts/tokens or infer observations. Shared owners/admins receive both read/generate permissions; standalone callers retain their separately configured permission sets.

### GenerateReportRequest

The top-level JSON object is strict: extra identity, permission, credential, or provider-URL fields are rejected.

| Field | Contract |
| --- | --- |
| `project_id` | Required validated real project identifier, authorized by the token |
| `request_id` | Required validated caller correlation identifier |
| `as_of` | Required project-local calendar date, `YYYY-MM-DD`; cannot be after today |
| `query` | Optional existing Monitoring default: `project progress delays deadlines milestones risks issues`; 1–500 characters |
| `planning_message` | Optional genuine Planning `SCHEDULE_CREATED` envelope, or null; otherwise use live Planning |
| `received_on` | Required date when `planning_message` is supplied; absent/null otherwise |
| `actual_progress` | Optional separate project-scoped observation object, or null |
| `observed_on` | Required date when inline `actual_progress` is supplied; absent/null otherwise |
| `observation_id` | Optional saved receipt ID for this project; mutually exclusive with inline `actual_progress` and `observed_on`; requires both generate and read permissions |
| `include_risks` | Strict boolean, defaults to `true` |
| `save_report` | Strict boolean, defaults to `false` |

Future report `as_of` dates return 422 after authorization and before local source factories, observation lookup, provider/client creation, or storage access. Reports describe actual observations, not forecasts. Observation/receipt dates must not postdate `as_of`. Submitted actuals must have the same `project_id`; their statuses, percentages, completion/start/update dates, and date pairs are validated **before live calls**. Task references are checked against the fetched baseline before Risk receives context. Current live Planning/Risk observations cannot stand in for historical snapshots; historical requests require genuinely dated inputs/providers.

- `include_risks=true` with no configured Risk source returns 503, not an empty successful risk analysis. Explicit `false` permits partial coverage and preserves that limitation.
- With `MONITORING_RISK_FAILURE_POLICY=fail`, a configured but failing Risk source fails the request. `report_unknown` permits degraded reports only for transport/timeouts, HTTP 408/429, and server availability failures (5xx), with `coverage.risk_status="unavailable"`, `RISK_UNAVAILABLE`, and truthful limitations. Invalid JSON/schema, wrong-project responses, redirects, and other HTTP failures still fail without saving or publishing. Injected Risk providers must raise `RiskAgentAvailabilityError` for degradable failures; generic `RiskAgentAPIError` does not qualify. The policy does not permit an unconfigured included Risk source.
- `save_report=true` requires configured storage and an awaited successful save before the report response. A failed/timed-out save does not return a successful saved report. The default performs **no storage writes**, even when storage is configured.
- Inline/no-actual generation does not require read permission. Stored-receipt generation requires **both** permissions before local payload factories or storage. It resolves the authorized project/ID, revalidates dates and task IDs against the **chosen current/genuinely dated Planning baseline**, and uses the existing `ActualProgressProvider`; an old receipt cannot silently authorize removed/new tasks or bypass live historical-date guards.
- Receipt metadata is retained at `report.actual_context.observation_receipt` before report persistence/export, including caller-claimed source, verified submitter, and UTC acceptance time. The report's own request/author remain those of the generating caller, not the original observation submitter. Submitter identity is not forwarded to Risk. Acceptance time is **not** substituted for `observed_on` or genuine source updates; a receipt accepted today may support a historical report only with appropriately dated observations and Planning/Risk inputs.
- Generation may call Monitoring's own Groq summarizer as described below, independently of `include_risks`. It does not create/mutate a Planning schedule, automatically publish to Coordinator, or POST Planning feedback.

### Observation intake contract

`POST /api/monitoring/projects/{project_id}/observations` accepts exactly:

```json
{
  "request_id": "field-update-42",
  "source": "site-inspection",
  "observed_on": "2026-10-09",
  "actual_progress": {
    "project_id": "P001",
    "tasks": [{
      "task_id": "T001",
      "status": "IN_PROGRESS",
      "progress_percentage": 40,
      "actual_start": "2026-10-02"
    }]
  },
  "save_observation": false
}
```

This is a **contract illustration**, not seed data: use real authorized Planning project/task IDs and separately recorded execution measurements. Optional `planning_message` must be the genuine `SCHEDULE_CREATED` envelope with a paired `received_on` date; without it, Monitoring reads live Planning. Task/project `updated_on`, `actual_start`, and `actual_end` are optional genuine source dates, never inferred from Planning estimates.

- `request_id` and `source` use the same `Identifier` format as project/task IDs. `source` is solely a **caller-claimed label**, not an authenticated agent identity, signature, URL, credential, or destination. The verified bearer principal authorizes the submission and supplies `submitted_by`; it does not prove that the execution measurements are true. Planning message sender names likewise are structural, not proof of origin.
- Dates must be `YYYY-MM-DD`; observation/message receipt dates cannot be in the future. Strict extra-field rejection applies at body/project/task levels. Percentages must be JSON numbers, not booleans/strings. Save flags are strict JSON booleans. Intake accepts 1–1,000 task records; Monitoring POST bodies are limited to **1 MiB** (413 when exceeded).
- Project scope and `monitoring:generate` are checked before local source factories, upstream clients, or storage. The existing actual adapter checks duplicate IDs, statuses, 100% completion/end dates, starts, and project/task/observation update consistency. Every submitted task must exist in the selected real Planning baseline **before any storage access**. Intake never calls Risk, a model, report storage, or an external summarizer.
- Response fields are exactly `observation_id`, `request_id`, `project_id`, `source`, `observed_on`, `accepted_at`, `submitted_by`, `actual_progress`, `persisted`, and `limitations`. `accepted_at` is a server UTC timestamp. `actual_progress` contains only canonical recognized input fields: normalized execution statuses (`PLANNED` becomes `NOT_STARTED`), numeric percentages, and original genuine update dates. Missing update dates remain absent in the receipt; only the existing analysis adapter uses the observation recording date with an explicit date-basis label.
- `save_observation` defaults to `false`: the receipt is **validation-only**, not durable, not held in a fallback cache, and not usable through GET or stored-ID generation. Only explicit `true` inserts into configured observation storage; the insert is awaited before returning `persisted=true`. Failures/timeouts return errors, not a successful receipt. Receipt IDs are server-generated; `request_id` is correlation, **not an idempotency key**. Retrying after an ambiguous timeout may create another receipt.
- GET list/detail require project scope plus `monitoring:read`; missing authorized IDs return 404. Stored receipts are rebound and revalidated; cross-project, wrong-ID, metadata-mismatched, unsaved, and corrupt rows fail closed. Observation history is separate from report history. There is no observation edit/delete route or standalone actual-source provider configuration.

### Error contract

Errors contain a sanitized `detail`, not upstream response bodies, validation inputs, exception strings, URLs, or server keys:

- 401: invalid/missing token when auth is configured; includes `WWW-Authenticate: Bearer`.
- 403: missing project scope or required permission.
- 413: a Monitoring POST body exceeds 1 MiB.
- 422: invalid request identifiers/body/dates/message/observations or unknown submitted task IDs.
- 503: unavailable authentication/project storage or required Planning/Risk/storage configuration; shared Monitoring persistence failures also return safe 503.
- 502: source or storage failure, invalid upstream schema/project binding, or corrupt stored report.
- 504: source timeout and standalone storage timeout, including timeouts wrapped by existing adapters; shared auth/project/persistence timeouts return safe 503.
- 404: inaccessible/nonexistent shared project, or an authorized stored-report/observation lookup has no result.

A source failure is not replaced with synthetic tasks, actuals, risks, or success status. A failed source call does not change health into a claim of verified live connectivity.

## Real interagent flow and source accuracy

1. A verified caller selects an authorized existing project and requests its baseline. Shared `MonitoringAccountAccess` reads through `PlanningRepository` with a fresh owner filter; standalone `PlanningAgentAPIProvider` reads **`GET /api/planning/schedule/{project_id}`**. Neither creates or mutates schedules.
2. The caller supplies separately recorded actual task observations, or deliberately requests a report without them. Planning statuses/utilization do **not** become actual execution measurements.
3. Monitoring validates project/date/task bindings and assesses actual freshness. Its contextual Risk adapter sends **`POST /api/risk/analyse`**, with `source_agent="Monitoring Agent"`, the project ID, monitoring-risk query, `top_k`, and serialized validated Planning/actual context, as-of/source dates, freshness, and interpretation limitations. Server credentials and caller identity are not passed as context; credential-like fields are removed recursively, including camelCase/separator spellings and JSON-encoded nested metadata. Risk operates through its public API, not shared services or model imports.
4. Monitoring calculates verified report facts, directly asks Groq to select summary fact IDs when enabled/configured, and falls back to the standard summary on failure. It returns an evidence-bound report, optionally saving that exact snapshot. Risk IDs/task links, summary, evidence, knowledge sources, and recommendations remain source-attributed; task links are not guessed from `affected_area`.
5. A caller may retrieve a **stored** report's Coordinator envelope. It has `message_type="monitoring.report.ready"`, `sender="monitoring_reporting"`, `recipient="coordinator"`, `project_id`, `report`, and `correlation_id=report.request_id`. This is **export only**; there is no automatic Coordinator delivery or unsupported network call. Unsaved reports are not available through stored export/history routes.

Including risks sends project/task observations to the configured Risk service and may invoke that service's own paid or rate-limited inference. Its deployment controls downstream privacy, cost, and availability; Monitoring does not initialize those models itself.

### Direct Monitoring LLM summary

Monitoring now calls **`POST https://api.groq.com/openai/v1/chat/completions`** directly for extractive summary selection. No separate NLP pipeline, document retrieval, model download, or additional server is added. The shared/standalone API uses the existing backend `GROQ_API_KEY` and `GROQ_MODEL`; leave `MONITORING_LLM_ENABLED=true` (the default). Restart the existing backend after operator configuration/code changes. Do not put keys in the browser or API request bodies.

The LLM receives only verified fact IDs/text, not raw Planning/actual contexts, observation submitters, browser tokens, or database/provider credentials. A fact text can still contain private project/task information; enabling this feature sends it to Groq and adds latency, inference cost, and rate-limit exposure. The destination is fixed, redirects and environment proxies are disabled, clients are request-owned, and shared access is rechecked before disclosure.

The model selects existing facts rather than inventing prose, metrics, percentages, dates, classifications, or actions. Structured responses must contain unique known fact IDs and project status; reporting restores missing actual/risk coverage facts and unavailable milestone coverage even if the selection omits them. Deterministic calculations remain authoritative. Prompt text embedded in task names is treated as data; output validation prevents it from adding new report facts.

A successful selection sets `summary_method="llm_extractive"`; the UI labels it **AI-assisted summary**. Missing credentials, timeout, HTTP/rate-limit/model-schema errors, refusals, oversize input/output, or invalid selections retain `summary_method="deterministic_extractive"` with a safe fallback limitation. Explicitly setting `MONITORING_LLM_ENABLED=false` uses the standard summary without attempting Groq or adding a failure limitation. Risk's own LLM call is independent: turning off **Include risk source** does not turn off Monitoring's summary LLM, and disabling Monitoring's LLM does not disable Risk.

Health adds `capabilities.llm_configured` as a configuration-only flag; it never calls Groq or claims a successful live inference. Observation intake, history/detail reads, and exports do not call the summarizer. Saved reports preserve the exact generated summary and method; no table/schema migration is needed.

### Planning message input

Instead of a live GET, `PlanningMessageProvider` accepts the PDF structural contract: `sender="planning_agent"`, `receiver="project_monitor_report_agent"`, `action="SCHEDULE_CREATED"`, a valid envelope `request_id`, successful status, and `data` containing the genuine Planning schedule response. `received_on` is explicit. The original envelope and receipt metadata are preserved in Planning context.

**The sender string is structural validation, not a cryptographic signature or proof of origin.** Verified identity plus ownership authorizes shared submission; a separately scoped token authorizes standalone submission. Callers/operators remain responsible for source provenance. Monitoring does not invent an agent-authentication protocol.

### Actual observations

`actual_progress` contains `project_id` and `tasks`. Each task supplies `task_id`, execution `status` (`NOT_STARTED`/`PLANNED`, `IN_PROGRESS`, `BLOCKED`, or `COMPLETED`), and explicit `progress_percentage` on 0–100. Optional dates are `actual_start`, `actual_end`, and genuine task/project `updated_on`.

Completed tasks require 100% and `actual_end`; only completed tasks may have 100% or an end date. Not-started tasks require 0%. Missing update dates use the explicit observation recording date, **not an invented source-modification timestamp**. Coverage retains the distinction between `actual_updated_on` and `actual_observed_on`, and source payload/date bases remain in `actual_context`. No actual-data API is assumed and no progress percentage is inferred from a status.

## Coverage, assessment, and export limits

The API returns the core report unchanged, including typed `coverage`, source context, actual context, warnings, facts, Risk metadata, and passive Planning feedback. It does not add substitute core schemas or reinterpret report status.

- Coverage separates Planning availability/freshness, actual `complete`/`partial`/`missing`/`stale`, Risk `available`/`not_supplied`/`stale`/`unavailable`, milestone availability, source/observation dates, task counts, and progress methods. Legacy stored reports without coverage retain **`coverage=null`**, never manufactured completeness.
- Planning expected progress is a **linear calendar-day estimate** unless Planning genuinely supplies a target. Variance is actual minus expected in percentage points; aggregate actual progress is an unweighted task average. Source priority and explicit `is_critical` remain distinct.
- Missing/stale unfinished actuals have null current progress/variance; overall progress remains unknown without adequate fresh observations. Historical completion/late-start facts remain facts. Missing actuals alone are `AT_RISK`, not confirmed execution delay; a passed baseline deadline without actuals means completion is unknown.
- Planning without real source update dates has unknown freshness. The current schedule adapter does not invent milestones, timestamps, or execution measurements from Planning analysis/status.
- Risk omission/unavailability means unknown risk coverage, even if a count field is zero. A successful empty Risk snapshot is different from no Risk snapshot.
- Status/threshold calculation belongs to `MonitoringSettings` and the deterministic analysis core. Summary fact selection uses Monitoring's direct Groq LLM when enabled/configured, with an evidence-bound deterministic fallback. Reports carry limitations and always require human review. No independent retrieval or separate NLP pipeline is enabled by this API.
- `report.planning_feedback` contains `SCHEDULE_DELAY_DETECTED` artifacts for genuinely observed delays, with request correlation and dependent-task effects. These are **not posted**: Planning's existing handler does not implement this feedback/rescheduling action. Exporting a report does not establish a functioning Coordinator or feedback loop.

## Storage

The existing `SupabaseMonitoringReportRepository` stores the full report plus matching report/project/request/generated-at row metadata. Reads validate the row/report bindings. Report construction and serialization recursively remove credential-bearing fields using the same filter as Risk-bound context, including nested and JSON-encoded metadata, while retaining ordinary evidence, source dates, and receipt provenance without mutating source payloads. This applies to generation, persistence, history/detail reads, and Coordinator exports, including legacy stored reports and injected repositories. Legacy database rows are not rewritten automatically; operators must separately clean up and rotate any previously persisted credentials. `SupabaseMonitoringObservationRepository` separately stores immutable receipts plus matching observation/project/request/source/observed-date/author/acceptance-time row metadata and checks every binding on reads. Observations do not mutate Planning tasks; reports remain independent snapshots. Modern `sb_secret_` credentials use `apikey`; legacy service-role credentials additionally use backend Bearer authorization. Neither is sent to other agents or returned to the UI.

Server credentials may bypass RLS, so project/permission authorization remains mandatory for every operation. `monitoring_reports.sql` is a **manual** reference only; the service neither applies nor reconciles schemas. Its appended `monitoring_observations` table enables RLS, adds a project/date index and receipt metadata constraints, and grants no browser policies. An operator must review/apply the SQL in the intended Supabase project, preserving any existing schema/security setup; for custom table names, adapt the manual SQL consistently. Confirm both tables, public REST exposure, privileges, and RLS independently. No migration or database writes run at import/startup/health.

Health's additive `capabilities.observations_storage_configured` is true for an injected observation repository or valid Supabase settings (default table name included). It means **configuration only**, not table existence, live readability/writeability, successful migration, or verified access. Report and observation capabilities are independent with injected repositories. Existing account/admin auth setup remains operator-required in shared mode, and standalone auth still requires its explicit mapping. This implementation does not modify/provision operator credentials as part of migration.

For an operator's explicitly requested **read-only network** check, from `backend/`:

```sh
python -B -m app.agents.monitoring.storage --env-file .env
```

This selects at most one report ID and makes no writes. Success checks readability with the configured backend credential, not user authorization, RLS correctness, or write permission. API health does not run this check.

## Factories and shared-account wiring

`create_app(settings=None, *, env_file=None, authenticator=None, planning_provider_factory=None, risk_provider_factory=None, repository=None, observation_repository=None, client_factory=httpx.AsyncClient)` supports isolated deployment and offline injection. Passing `MonitoringAPISettings()` avoids environment loading for that factory call. Provider factories receive a request-owned client; injected repositories are server-owned objects. `client_factory` allows `httpx.MockTransport` in tests without real credentials/network.

`create_router(...)` exposes the reusable route group, defaults to unconfigured standalone settings rather than reading environment, and retains safe validation/error responses. `create_shared_router(app, settings=None, *, planning_api_key=None)` is mounted by `app.main` and enables account-only authentication with fresh project ownership. It uses a request-owned Planning repository, an ASGI Risk client, and separate external storage clients. The service-key argument remains for call-site compatibility but is not needed for browser Planning reads. Current-date provider checks still apply; a repository read is not a historical snapshot.

Standalone factories retain `MonitoringTokenAuthenticator`; shared mode ignores its mapping and rejects legacy scoped/service tokens rather than falling back after a bad account JWT. Automatic Coordinator delivery and supported Planning feedback handling still require explicit future integration.

## Module layout and validation

| Module | Responsibility |
| --- | --- |
| `api.py` | Shared/standalone FastAPI routers, in-process Risk transport, auth/recheck gates, configuration and safe HTTP contract |
| `account_access.py` | Shared request-owned account verification, project ownership, and Planning repository adapter |
| `agent.py` | Authorized orchestration, contextual Risk integration, persistence-before-publication, report reads |
| `schemas.py` | Validated models, coverage, provider/repository/sink protocols |
| `providers.py` | Real Planning HTTP/message, supplied actual-progress, and Risk HTTP adapters |
| `analysis.py` | Deterministic progress, coverage, task/milestone/status assessment |
| `reporting.py` | Evidence-bound summaries, required coverage facts, report construction, passive feedback |
| `summarizer.py` | Async direct Groq fact selection, fixed destination, bounded requests, strict/sanitized output validation |
| `storage.py` | Supabase settings/repository and read-only operator CLI |
| `__init__.py` | Public agent/request/report/principal/settings exports |

From `backend/`, bounded offline API validation (no bytecode or pytest cache output):

```sh
python -B -m pytest tests/test_monitoring_account_access.py app/agents/monitoring/tests/test_api.py -p no:cacheprovider
```

Broader Monitoring suite:

```sh
python -B -m pytest app/agents/monitoring/tests -p no:cacheprovider
```

`tests/test_api.py` guards import-time dotenv loading so protected operator `.env`/`.env.example` files are never read; subprocess import checks also guard dotenv. Configuration tests use isolated temporary files or mocked readers, and factories receive explicit settings. Use `-B` and `-p no:cacheprovider` to avoid bytecode/test-cache outputs. These guards are test-only; normal operator configuration behavior is unchanged.

`tests/test_api.py` owns its API fixtures and uses `TestClient`, `ASGITransport`, injected fakes, and `MockTransport`. Existing Monitoring fixtures reject real HTTP. Tests cover configuration-only health, fail-closed auth, scopes before data access, strict/datetime input contracts, canonical adapters/context, request-owned credential isolation, explicit saves, failed storage responses, stored reads/export/project binding, legacy coverage, safe errors/CORS/URLs, shared-app routing/transport isolation, actual application bootstrap wiring with offline upstreams, and standalone import isolation from shared services/models. `tests/test_summarizer.py` and `tests/test_llm_api.py` exercise direct Groq transport/output validation, authorization gates, configuration, unchanged calculations, and safe fallback using offline mocks. They do **not** establish live Groq/Planning/Risk/database connectivity, successful real database writes, browser end-to-end integration, or automatic feedback/Coordinator delivery.
