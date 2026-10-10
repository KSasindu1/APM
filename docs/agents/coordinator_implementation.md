# Coordinator component

The Coordinator provides a structured input form, local natural-language extraction, editable resource/task lists, JSON delegation to Planning, and a deadline recommendation. Its scoped styling follows the existing Planning/Schedule palette (#2b4ea8 blue, #f6f5f2 paper, #121826 ink) and Manrope typography. No separate server or port is needed.

## Running after shared-file integration

From `backend`, use the normal command:

```powershell
python -m uvicorn app.main:app --reload
```

From `frontend`, run `npm run dev`, then open `/coordinator`. The frontend reuses `VITE_API_BASE_URL`, defaulting to http://127.0.0.1:8000. No environment changes are needed for standard local use.

## Shared changes requiring approval

In `backend/app/main.py`, add `from app.agents.coordinator.router import router as coordinator_router` and register `app.include_router(coordinator_router)` after the Planning router registration.

In `frontend/src/App.jsx`, add `import Coordinator from "./pages/Coordinator";` and `<Route path="/coordinator" element={<Coordinator />} />` inside Routes. These are the only required shared-file changes. Optional navigation links are not required.

## Behavior

`POST /api/coordinator/interpret` extracts a draft without invoking Planning. NLP is rule-based, not a general LLM: names introduced by `Project:`, `Project name:`, `called`, or `named`; explicit ISO dates; and numbered developers/testers/designers/engineers/analysts are supported. Missing fields are left for manual entry. Long text is shortened with a visible note. Re-extracting replaces the draft.

`POST /api/coordinator/request` validates the existing Planning contract and forwards it through an in-process adapter. It reuses Planning's configured service and repository, calls `create_schedule_with_risk`, and saves through the existing repository. This preserves the newly integrated Risk workflow without modifying another member's implementation. The response contains the actual `planning_request`, `planning_result`, decision, and original warnings. Monitoring is not invoked.

Planning's API-key requirement is applied to Coordinator schedule requests through the existing verification function. The frontend follows the existing Planning client's `VITE_PLANNING_API_KEY` convention; Vite environment values are browser-visible and should not hold privileged server secrets. NLP extraction adds no identity system. Existing application/database/LLM configuration remains necessary.

## Verification

From `backend`: `python -m pytest tests/test_coordinator_component.py -q`.
Tests mock the agents and persistence, checking JSON mapping, deadline decisions, failure states, NLP extraction, and use of the current risk/persistence workflow. A live saved schedule requires the existing backend configuration.
