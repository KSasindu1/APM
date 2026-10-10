"""Offline monitoring behavior and project-scoped authorization tests."""

import asyncio
import json
from copy import deepcopy
from datetime import date
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import JsonValue, ValidationError

from app.agents.monitoring.agent import (
    MonitoringAccessDenied,
    MonitoringAgent,
    MonitoringTokenAuthenticator,
    require_access,
)
from app.agents.monitoring.providers import (
    ActualProgressProvider,
    PlanningAgentAPIProvider,
    PlanningMessageProvider,
    RiskAgentAPIError,
    RiskAgentAvailabilityError,
    RiskAgentIntegrityError,
)
from app.agents.monitoring.schemas import (
    ActualProjectSnapshot,
    MonitoringCoverage,
    MonitoringPrincipal,
    MonitoringReport,
    MonitoringRequest,
    MonitoringSettings,
    PlannedTask,
    PlanningSnapshot,
    RiskSnapshot,
    SummaryFact,
    SummarySelection,
    TaskAssessment,
)

from .conftest import FixtureProviders


PRINCIPAL = MonitoringPrincipal.model_validate({
    "subject": "manager", "project_ids": ["P001"], "permissions": ["monitoring:generate"],
})


def request(day="2026-09-18"):
    return MonitoringRequest(project_id="P001", request_id="req-1", as_of=date.fromisoformat(day))


@pytest.mark.asyncio
async def test_variance_delay_risk_and_report():
    report = await MonitoringAgent(FixtureProviders(), FixtureProviders(), FixtureProviders()).generate(
        request(), PRINCIPAL)
    assert report.metrics.actual_progress == 50
    assert report.metrics.expected_progress == 80
    assert report.metrics.progress_variance == -30
    assert report.metrics.delayed_tasks == 1
    assert report.metrics.high_risks == 1
    assert report.status == "DELAYED"
    assert report.milestones[0].status == "At Risk"
    assert report.summary_method == "deterministic_extractive"
    assert report.human_review_required


@pytest.mark.asyncio
async def test_overdue_and_completed_late_milestone():
    sample = FixtureProviders()
    report = await MonitoringAgent(sample, sample).generate(request("2026-09-21"), PRINCIPAL)
    assert report.metrics.overdue_tasks == 1
    assert report.milestones[0].status == "Delayed"
    assert any(w.code == "OVERDUE_TASK" for w in report.warnings)

    sample.actual = ActualProjectSnapshot.model_validate({
        "project_id": "P001", "updated_on": "2026-09-21",
        "tasks": [{"task_id": "T001", "status": "completed", "progress": 100,
                   "updated_on": "2026-09-21", "completed_on": "2026-09-21"}],
    })
    report = await MonitoringAgent(sample, sample).generate(request("2026-09-21"), PRINCIPAL)
    assert report.metrics.overdue_tasks == 0
    assert report.tasks[0].completed_late_days == 1
    assert report.milestones[0].status == "Delayed"


@pytest.mark.asyncio
async def test_missing_actual_is_not_zero_progress():
    sample = FixtureProviders()
    sample.actual = ActualProjectSnapshot(project_id="P001", updated_on=date(2026, 9, 18), tasks=[])
    report = await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)
    assert report.metrics.actual_progress is None
    assert report.metrics.missing_actual_tasks == 1
    assert report.status == "AT_RISK"
    assert any(w.code == "MISSING_ACTUAL" for w in report.warnings)


@pytest.mark.asyncio
async def test_dependency_delay_and_linear_expected_progress():
    sample = FixtureProviders()
    sample.planning = PlanningSnapshot.model_validate({
        "project_id": "P001", "updated_on": "2026-09-18",
        "tasks": [sample.planning.tasks[0].model_dump(),
                  {"task_id": "T002", "title": "Integration", "start_date": "2026-09-19",
                   "due_date": "2026-09-29", "dependency_ids": ["T001"]}],
    })
    sample.actual = ActualProjectSnapshot.model_validate({
        "project_id": "P001", "updated_on": "2026-09-18",
        "tasks": [sample.actual.tasks[0].model_dump(),
                  {"task_id": "T002", "progress": 0, "status": "not_started",
                   "updated_on": "2026-09-18"}],
    })
    report = await MonitoringAgent(sample, sample, settings=MonitoringSettings(delay_threshold=10)).generate(
        request(), PRINCIPAL)
    assert report.tasks[1].expected_progress == 0
    assert report.tasks[1].dependency_delayed
    assert report.tasks[1].delayed
    assert report.metrics.actual_progress == 25


@pytest.mark.asyncio
async def test_stale_actual_and_empty_plan_do_not_appear_on_track():
    sample = FixtureProviders()
    sample.planning = PlanningSnapshot(project_id="P001", updated_on=date(2026, 9, 18))
    sample.actual = ActualProjectSnapshot(project_id="P001", updated_on=date(2026, 9, 1))
    report = await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)
    assert report.status == "AT_RISK"
    assert report.metrics.actual_progress is None
    assert any(w.code == "STALE_ACTUAL" for w in report.warnings)


@pytest.mark.asyncio
async def test_mismatched_project_or_unknown_task_rejected():
    sample = FixtureProviders()
    class WrongProject:
        async def get_actual(self, project_id):
            return sample.actual.model_copy(update={"project_id": "P002"})

    with pytest.raises(ValueError, match="requested project"):
        await MonitoringAgent(sample, WrongProject()).generate(request(), PRINCIPAL)
    sample = FixtureProviders()
    sample.actual = ActualProjectSnapshot.model_validate({
        "project_id": "P001", "updated_on": "2026-09-18",
        "tasks": [{"task_id": "UNKNOWN", "progress": 0, "status": "not_started",
                   "updated_on": "2026-09-18"}],
    })
    with pytest.raises(ValueError, match="unknown task"):
        await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)


@pytest.mark.asyncio
async def test_unauthorized_request_never_fetches_data():
    sample = FixtureProviders()
    with pytest.raises(MonitoringAccessDenied):
        await MonitoringAgent(sample, sample).generate(request(), MonitoringPrincipal.model_validate({
            "subject": "visitor", "project_ids": ["P002"], "permissions": ["monitoring:generate"],
        }))


@pytest.mark.asyncio
async def test_optional_summarizer_only_selects_verified_facts_and_publishes():
    class Selector:
        async def select_facts(self, facts):
            return SummarySelection(fact_ids=["status", "progress"])

    class Sink:
        message = None

        async def publish(self, message):
            self.message = message

    sample = FixtureProviders()
    sink = Sink()
    report = await MonitoringAgent(sample, sample, sample, Selector(), sink).generate(request(), PRINCIPAL)
    assert report.summary_method == "llm_extractive"
    assert report.summary_fact_ids == ["status", "progress"]
    assert sink.message is not None
    assert sink.message.report == report
    assert sink.message.correlation_id == "req-1"

    class InvalidSelector:
        async def select_facts(self, facts):
            return SummarySelection(fact_ids=["invented"])

    fallback = await MonitoringAgent(sample, sample, summarizer=InvalidSelector()).generate(request(), PRINCIPAL)
    assert fallback.summary_method == "deterministic_extractive"
    assert "invented" not in fallback.summary


@pytest.mark.asyncio
async def test_on_track_and_configurable_variance_thresholds():
    sample = FixtureProviders()
    sample.actual = ActualProjectSnapshot.model_validate({
        "project_id": "P001", "updated_on": "2026-09-18",
        "tasks": [{"task_id": "T001", "progress": 80, "status": "in_progress",
                   "updated_on": "2026-09-18"}],
    })
    settings = MonitoringSettings(deadline_approaching_days=1)
    report = await MonitoringAgent(sample, sample, settings=settings).generate(request(), PRINCIPAL)
    assert report.status == "ON_TRACK"
    assert report.milestones[0].status == "On Track"
    assert "no monitoring thresholds were triggered" in report.summary

    sample.actual = FixtureProviders().actual
    settings = MonitoringSettings(delay_threshold=35, delayed_variance=40,
                                  at_risk_variance=10, deadline_approaching_days=1)
    report = await MonitoringAgent(sample, sample, settings=settings).generate(request(), PRINCIPAL)
    assert report.status == "AT_RISK"
    assert report.metrics.delayed_tasks == 0


def test_explicit_fixture_contracts_and_invalid_risk_level():
    fixture = FixtureProviders()
    assert fixture.planning.milestones[0].title == "Backend Complete"
    assert fixture.actual.tasks[0].status == "in_progress"
    assert fixture.actual.tasks[0].actual_start is None
    assert fixture.risk.risks[0].task_ids == ["T001"]
    assert fixture.risk.risks[0].impact_days is None
    with pytest.raises(ValueError):
        RiskSnapshot.model_validate({"project_id": "P001", "updated_on": "2026-09-18",
            "risks": [{"risk_id": "R001", "level": "urgent", "description": "Invalid"}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("as_of,deadline_passed", [
    ("2026-09-18", False), ("2026-09-20", False), ("2026-09-21", True),
])
async def test_optional_actual_provider_preserves_unknown_completion(as_of, deadline_passed):
    sample = FixtureProviders()
    report = await MonitoringAgent(sample).generate(request(as_of), PRINCIPAL)
    assert report.status == "AT_RISK"
    assert report.metrics.actual_progress is None
    assert report.metrics.progress_variance is None
    assert report.metrics.missing_actual_tasks == 1
    assert report.metrics.overdue_tasks == report.metrics.delayed_tasks == 0
    assert not report.metrics.actual_progress_is_lower_bound
    assert report.tasks[0].model_dump() == {
        "task_id": "T001", "title": "Backend Development", "status": "missing",
        "actual_progress": None, "expected_progress": 80.0, "variance": None,
        "overdue_days": 0, "completed_late_days": 0, "delayed": False,
        "dependency_delayed": False, "start_delay_days": 0,
        "planned_deadline_passed": deadline_passed,
        "actual_is_stale": False, "last_observed_progress": None,
        "is_critical": False, "planned_start": date(2026, 9, 1), "due_date": date(2026, 9, 20),
        "actual_start": None, "completed_on": None, "last_updated_on": None,
    }
    assert report.milestones[0].progress is None
    assert report.milestones[0].status == "At Risk"
    assert not report.milestones[0].completed
    assert not report.milestones[0].overdue
    assert report.planning_feedback == []
    codes = {warning.code for warning in report.warnings}
    assert "MISSING_ACTUAL" in codes
    assert "STALE_ACTUAL" not in codes
    assert "OVERDUE_TASK" not in codes
    assert "DELAYED_TASK" not in codes
    assert ("PLANNED_DEADLINE_PASSED" in codes) is deadline_passed
    if deadline_passed:
        warning = next(w for w in report.warnings if w.code == "PLANNED_DEADLINE_PASSED")
        assert "completion is unknown" in warning.message


@pytest.mark.asyncio
async def test_explicit_empty_actual_snapshot_never_confirms_overdue():
    sample = FixtureProviders()
    sample.actual = ActualProjectSnapshot(project_id="P001", updated_on=date(2026, 9, 21))
    report = await MonitoringAgent(sample, sample).generate(request("2026-09-21"), PRINCIPAL)
    assert report.status == "AT_RISK"
    assert report.tasks[0].planned_deadline_passed
    assert report.tasks[0].actual_progress is report.tasks[0].variance is None
    assert report.metrics.overdue_tasks == report.metrics.delayed_tasks == 0
    assert report.planning_feedback == []


@pytest.mark.asyncio
async def test_undated_plan_is_not_assumed_stale():
    sample = FixtureProviders()
    sample.planning.updated_on = None
    report = await MonitoringAgent(sample).generate(request(), PRINCIPAL)
    assert report.status == "AT_RISK"
    assert not any(w.code == "STALE_PLAN" for w in report.warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize("date_field", ["updated_on", "fetched_on"])
async def test_future_planning_snapshot_is_rejected(date_field):
    sample = FixtureProviders()
    sample.planning.updated_on = None
    setattr(sample.planning, date_field, date(2026, 9, 19))
    with pytest.raises(ValueError, match="postdate|historical"):
        await MonitoringAgent(sample).generate(request(), PRINCIPAL)


@pytest.mark.asyncio
async def test_observed_late_start_emits_passive_feedback_for_descendants(planning_payload, actual_payload):
    schedule = planning_payload["schedule"]
    for task_id, dependencies, start, end in [
        ("T002", ["T001"], "2026-10-28", "2026-10-30"),
        ("T003", ["T002"], "2026-10-31", "2026-11-02"),
        ("T004", [], "2026-10-28", "2026-10-30"),
    ]:
        task = deepcopy(schedule[0])
        task.update(task_id=task_id, name=task_id, dependencies=dependencies,
                    planned_start=start, planned_end=end)
        schedule.append(task)
    actual_payload["tasks"].extend([
        {"task_id": task_id, "status": "NOT_STARTED", "progress_percentage": 0}
        for task_id in ("T002", "T003", "T004")
    ])
    requests = []

    def handler(sent):
        requests.append(sent)
        assert sent.method == "GET", "Planning feedback must not make an unsupported POST"
        assert sent.url.path == "/api/planning/schedule/P001"
        return httpx.Response(200, json=planning_payload)

    observed_on = date(2026, 10, 18)
    coordinator = AsyncMock()
    async with httpx.AsyncClient(
        base_url="https://planning.example.test", transport=httpx.MockTransport(handler),
    ) as client:
        planning = PlanningAgentAPIProvider(client, today=lambda: observed_on)
        actual = ActualProgressProvider(actual_payload, observed_on=observed_on)
        report = await MonitoringAgent(planning, actual, coordinator=coordinator).generate(
            request("2026-10-18"), PRINCIPAL)

    assert len(requests) == 1
    assert report.tasks[0].start_delay_days == 1
    assert report.tasks[0].actual_progress == 40
    assert report.tasks[0].expected_progress == 10
    assert report.tasks[0].variance == 30
    assert report.tasks[0].delayed
    assert report.tasks[1].dependency_delayed
    assert report.tasks[2].dependency_delayed
    assert not report.tasks[3].dependency_delayed
    assert report.metrics.overdue_tasks == 0
    assert report.status == "AT_RISK"
    assert report.planning_context == planning_payload
    assert report.milestones == []
    assert any(w.code == "LATE_START" and w.task_ids == ["T001"] for w in report.warnings)
    assert any(f.fact_id == "late_start" for f in report.facts)
    assert [message.model_dump() for message in report.planning_feedback] == [{
        "sender": "project_monitor_report_agent", "receiver": "planning_agent",
        "action": "SCHEDULE_DELAY_DETECTED", "request_id": "req-1",
        "data": {
            "project_id": "P001", "task_id": "T001", "delay_days": 1,
            "reason": "late_start", "affected_tasks": ["T002", "T003"],
        },
    }]
    coordinator.publish.assert_awaited_once()
    assert coordinator.publish.call_args.args[0].report == report


@pytest.mark.asyncio
async def test_message_and_actual_providers_generate_report_offline(planning_message, actual_payload):
    observed_on = date(2026, 10, 18)
    planning = PlanningMessageProvider(planning_message, received_on=observed_on)
    actual = ActualProgressProvider(actual_payload, observed_on=observed_on)
    report = await MonitoringAgent(planning, actual).generate(request("2026-10-18"), PRINCIPAL)
    assert report.metrics.actual_progress == 40
    assert report.metrics.expected_progress == 10
    assert report.metrics.progress_variance == 30
    assert report.tasks[0].start_delay_days == 1
    assert report.planning_feedback[0].action == "SCHEDULE_DELAY_DETECTED"
    assert report.planning_feedback[0].data["affected_tasks"] == []
    for field in ("schedule", "analysis", "responsible_ai", "warnings"):
        assert report.planning_context[field] == planning_message["data"][field]


@pytest.mark.asyncio
@pytest.mark.parametrize("planning_status", ["PLANNED", "IN_PROGRESS", "COMPLETED"])
async def test_planning_status_and_utilization_never_become_actuals(planning_message, planning_status):
    planning_message["data"]["schedule"][0]["status"] = planning_status
    planning_message["data"]["analysis"]["utilization_percentage"] = 100
    planning_message["data"]["schedule"][0]["planned_end"] = "2026-10-18"
    observed_on = date(2026, 10, 19)
    planning = PlanningMessageProvider(planning_message, received_on=observed_on)
    report = await MonitoringAgent(planning).generate(request("2026-10-19"), PRINCIPAL)
    assert report.status == "AT_RISK"
    assert report.metrics.actual_progress is report.metrics.progress_variance is None
    assert report.metrics.completed_tasks == report.metrics.overdue_tasks == report.metrics.delayed_tasks == 0
    assert report.metrics.missing_actual_tasks == 1
    assert report.tasks[0].status == "missing"
    assert report.tasks[0].planned_deadline_passed
    assert report.planning_feedback == []


@pytest.mark.asyncio
async def test_baseline_deadline_failure_is_risk_not_execution_delay(planning_message):
    planning_message["data"]["analysis"].update(deadline_met=False, delay_days=4)
    planning_message["data"]["analysis"]["warnings"] = ["Baseline exceeds available duration"]
    planning_message["data"]["warnings"] = ["Baseline exceeds available duration"]
    planning = PlanningMessageProvider(planning_message, received_on=date(2026, 10, 18))
    report = await MonitoringAgent(planning).generate(request("2026-10-18"), PRINCIPAL)
    assert report.status == "AT_RISK"
    assert report.metrics.overdue_tasks == report.metrics.delayed_tasks == 0
    assert report.planning_feedback == []
    assert any(w.code == "BASELINE_DEADLINE_NOT_MET" for w in report.warnings)
    assert len([w for w in report.warnings if w.code == "PLANNING_WARNING"]) == 1
    analysis = report.planning_context["analysis"]
    assert isinstance(analysis, dict)
    assert analysis["delay_days"] == 4


@pytest.mark.parametrize("project_ids,permissions", [
    ({"P002"}, {"monitoring:generate"}), ({"P001"}, {"monitoring:read"}),
    ({"P001"}, set()), (set(), {"monitoring:generate"}),
])
def test_require_access_needs_both_project_and_permission(project_ids, permissions):
    principal = MonitoringPrincipal(subject="visitor", project_ids=frozenset(project_ids),
                                    permissions=frozenset(permissions))
    with pytest.raises(MonitoringAccessDenied):
        require_access(principal, "P001", "monitoring:generate")


def test_require_access_accepts_authorized_principal():
    assert require_access(PRINCIPAL, "P001", "monitoring:generate") is None


@pytest.mark.parametrize("field", ["principal", "requested_by", "permissions"])
def test_request_body_cannot_supply_trusted_identity(field):
    payload = request().model_dump()
    payload[field] = PRINCIPAL.model_dump()
    with pytest.raises(ValueError):
        MonitoringRequest.model_validate(payload)


@pytest.mark.parametrize("identities", [
    {}, {"": PRINCIPAL}, {"short": PRINCIPAL}, {"t" * 31: PRINCIPAL},
    {"t" * 32: PRINCIPAL, "short": PRINCIPAL},
])
def test_token_authenticator_rejects_empty_or_short_token_maps(identities):
    with pytest.raises(ValueError, match="at least one bearer token of 32 or more characters"):
        MonitoringTokenAuthenticator(identities)


@pytest.mark.parametrize("token", ["t" * 32, "offline-monitoring-token-" + "a" * 16])
def test_token_authenticator_accepts_valid_token(token):
    authenticator = MonitoringTokenAuthenticator({token: PRINCIPAL})
    authenticated = authenticator.authenticate(token)
    assert authenticated is not None
    assert authenticated == PRINCIPAL
    assert authenticated is not PRINCIPAL
    require_access(authenticated, "P001", "monitoring:generate")


@pytest.mark.parametrize("token", ["", "short", "t" * 31, "u" * 32, "t" * 32 + "suffix"])
def test_token_authenticator_rejects_unknown_token(token):
    authenticator = MonitoringTokenAuthenticator({"t" * 32: PRINCIPAL})
    assert authenticator.authenticate(token) is None


def test_token_authenticator_matches_each_configured_identity():
    reader = MonitoringPrincipal(subject="reader", project_ids=frozenset({"P002"}),
                                 permissions=frozenset({"monitoring:read"}))
    authenticator = MonitoringTokenAuthenticator({"t" * 32: PRINCIPAL, "r" * 32: reader})
    assert authenticator.authenticate("t" * 32) == PRINCIPAL
    authenticated_reader = authenticator.authenticate("r" * 32)
    assert authenticated_reader is not None
    assert authenticated_reader == reader
    assert authenticated_reader is not reader
    require_access(authenticated_reader, "P002", "monitoring:read")
    with pytest.raises(MonitoringAccessDenied):
        require_access(authenticated_reader, "P001", "monitoring:generate")


def test_token_authenticator_defensively_copies_input_identities():
    token = "t" * 32
    supplied = PRINCIPAL.model_copy(deep=True)
    expected = supplied.model_copy(deep=True)
    identities = {token: supplied}
    authenticator = MonitoringTokenAuthenticator(identities)

    supplied.subject = "changed-manager"
    supplied.project_ids = frozenset({"P002"})
    supplied.permissions = MonitoringPrincipal.model_validate({
        "subject": "reader", "project_ids": ["P002"], "permissions": ["monitoring:read"],
    }).permissions
    identities.clear()

    authenticated = authenticator.authenticate(token)
    assert authenticated is not None
    assert authenticated == expected
    assert authenticated is not supplied
    require_access(authenticated, "P001", "monitoring:generate")
    with pytest.raises(MonitoringAccessDenied):
        require_access(authenticated, "P002", "monitoring:read")


def test_token_authenticator_defensively_copies_returned_principal():
    token = "t" * 32
    authenticator = MonitoringTokenAuthenticator({token: PRINCIPAL})
    returned = authenticator.authenticate(token)
    assert returned is not None
    assert returned == PRINCIPAL
    returned.subject = "changed-manager"
    returned.project_ids = frozenset({"P001", "P002"})
    returned.permissions = MonitoringPrincipal.model_validate({
        "subject": "manager", "project_ids": ["P001", "P002"],
        "permissions": ["monitoring:read", "monitoring:generate"],
    }).permissions

    authenticated_again = authenticator.authenticate(token)
    assert authenticated_again is not None
    assert authenticated_again == PRINCIPAL
    assert authenticated_again is not returned
    assert authenticated_again is not PRINCIPAL
    require_access(authenticated_again, "P001", "monitoring:generate")
    with pytest.raises(MonitoringAccessDenied):
        require_access(authenticated_again, "P002", "monitoring:read")


@pytest.mark.asyncio
async def test_later_actual_observation_cannot_backdate_historical_report():
    sample = FixtureProviders()
    actual = ActualProgressProvider({
        "project_id": "P001", "updated_on": "2026-09-18",
        "tasks": [{"task_id": "T001", "status": "IN_PROGRESS",
                   "progress_percentage": 50, "updated_on": "2026-09-18"}],
    }, observed_on=date(2026, 9, 19))
    snapshot = await actual.get_actual("P001")
    assert snapshot.updated_on == snapshot.tasks[0].updated_on == request().as_of
    assert snapshot.observed_on == date(2026, 9, 19)
    repository = AsyncMock()
    coordinator = AsyncMock()
    agent = MonitoringAgent(sample, actual, repository=repository, coordinator=coordinator)
    with pytest.raises(ValueError, match="later actual observation.*historical progress"):
        await agent.generate(request(), PRINCIPAL)
    repository.save.assert_not_awaited()
    coordinator.publish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_style", ["assignment", "model_copy"])
@pytest.mark.parametrize("changes,message", [
    ({"progress": 101}, "less than or equal to 100"),
    ({"status": "completed", "progress": 100, "completed_on": None}, "Completed tasks require"),
    ({"status": "completed", "progress": 100, "completed_on": date(2026, 9, 19)},
     "Completion cannot be after the update date"),
    ({"completed_on": date(2026, 9, 17)}, "Only completed tasks"),
    ({"updated_on": date(2026, 9, 19)}, "Task update cannot be newer than its snapshot"),
    ({"actual_start": date(2026, 9, 19)}, "Actual start must"),
])
async def test_agent_revalidates_mutated_nested_actual_models(mutation_style, changes, message):
    sample = FixtureProviders()
    if mutation_style == "model_copy":
        task = sample.actual.tasks[0].model_copy(update=changes)
        sample.actual = sample.actual.model_copy(update={"tasks": [task]})
    else:
        for field, value in changes.items():
            setattr(sample.actual.tasks[0], field, value)
    repository = AsyncMock()
    coordinator = AsyncMock()
    agent = MonitoringAgent(sample, sample, repository=repository, coordinator=coordinator)
    with pytest.raises(ValidationError, match=message):
        await agent.generate(request(), PRINCIPAL)
    repository.save.assert_not_awaited()
    coordinator.publish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["planning", "risk"])
@pytest.mark.parametrize("mutation_style", ["assignment", "model_copy"])
async def test_agent_revalidates_mutated_nested_planning_and_risk_models(source, mutation_style):
    sample = FixtureProviders()
    if source == "planning":
        if mutation_style == "model_copy":
            task = sample.planning.tasks[0].model_copy(update={"expected_progress": 101})
            sample.planning = sample.planning.model_copy(update={"tasks": [task]})
        else:
            sample.planning.tasks[0].expected_progress = 101
    elif mutation_style == "model_copy":
        risk = sample.risk.risks[0].model_copy(update={"confidence": 1.1})
        sample.risk = sample.risk.model_copy(update={"risks": [risk]})
    else:
        sample.risk.risks[0].confidence = 1.1
    repository = AsyncMock()
    coordinator = AsyncMock()
    agent = MonitoringAgent(sample, sample, sample, repository=repository, coordinator=coordinator)
    with pytest.raises(ValidationError, match="less than or equal to"):
        await agent.generate(request(), PRINCIPAL)
    repository.save.assert_not_awaited()
    coordinator.publish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,progress", [
    ("in_progress", 50), ("blocked", 50), ("not_started", 0),
])
async def test_stale_unfinished_task_does_not_establish_current_progress_or_overdue(status, progress):
    sample = FixtureProviders()
    sample.planning.tasks[0].due_date = date(2026, 9, 15)
    sample.planning.milestones[0].due_date = date(2026, 9, 15)
    sample.actual = ActualProjectSnapshot.model_validate({
        "project_id": "P001", "updated_on": "2026-09-18", "observed_on": "2026-09-18",
        "tasks": [{"task_id": "T001", "status": status, "progress": progress,
                   "updated_on": "2026-09-10"}],
    })
    report = await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)
    assert report.status == "AT_RISK"
    task = report.tasks[0]
    assert task.status == "stale"
    assert task.actual_is_stale
    assert task.last_observed_progress == progress
    assert task.actual_progress is task.variance is None
    assert task.overdue_days == task.completed_late_days == 0
    assert not task.delayed
    assert report.metrics.model_dump() == {
        "stale_actual_tasks": 1, "total_tasks": 1, "completed_tasks": 0,
        "missing_actual_tasks": 0, "overdue_tasks": 0, "blocked_tasks": 0,
        "in_progress_tasks": 0, "not_started_tasks": 0, "delayed_tasks": 0,
        "high_risks": 0, "actual_progress": None, "expected_progress": 80.0,
        "progress_variance": None, "actual_progress_is_lower_bound": False,
    }
    assert report.milestones[0].status == "At Risk"
    assert report.milestones[0].progress is None
    assert not report.milestones[0].overdue
    assert report.planning_feedback == []
    codes = {warning.code for warning in report.warnings}
    assert "STALE_ACTUAL_TASK" in codes
    assert not codes.intersection({"STALE_ACTUAL", "MISSING_ACTUAL", "OVERDUE_TASK", "DELAYED_TASK", "BLOCKED_TASK"})
    warning = next(w for w in report.warnings if w.code == "STALE_ACTUAL_TASK")
    assert warning.task_ids == ["T001"]
    assert warning.source_refs == ["task:T001"]
    assert "Current progress and completion are unknown" in warning.message
    assert "stale unfinished actual records" in report.summary


@pytest.mark.asyncio
@pytest.mark.parametrize("updated_on,threshold,stale", [
    (date(2026, 9, 11), 7, False), (date(2026, 9, 10), 7, True),
    (date(2026, 9, 16), 2, False), (date(2026, 9, 15), 2, True),
])
async def test_task_freshness_uses_individual_date_and_configured_boundary(updated_on, threshold, stale):
    sample = FixtureProviders()
    sample.actual.tasks[0].progress = 80
    sample.actual.tasks[0].updated_on = updated_on
    settings = MonitoringSettings(stale_after_days=threshold, deadline_approaching_days=0)
    report = await MonitoringAgent(sample, sample, settings=settings).generate(request(), PRINCIPAL)
    assert report.tasks[0].actual_is_stale is stale
    assert report.tasks[0].last_observed_progress == (80 if stale else None)
    assert report.metrics.stale_actual_tasks == int(stale)
    assert report.metrics.actual_progress == (None if stale else 80)
    assert report.metrics.progress_variance == (None if stale else 0)
    assert report.status == ("AT_RISK" if stale else "ON_TRACK")
    assert any(w.code == "STALE_ACTUAL_TASK" for w in report.warnings) is stale
    assert not any(w.code == "STALE_ACTUAL" for w in report.warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline,late_days,expected_status,milestone_status", [
    (date(2026, 9, 20), 0, "ON_TRACK", "Completed"),
    (date(2026, 9, 9), 1, "DELAYED", "Delayed"),
])
async def test_historical_completed_task_remains_certain(deadline, late_days, expected_status, milestone_status):
    sample = FixtureProviders()
    sample.planning.tasks[0].due_date = deadline
    sample.planning.milestones[0].due_date = deadline
    sample.actual = ActualProjectSnapshot.model_validate({
        "project_id": "P001", "updated_on": "2026-09-18", "observed_on": "2026-09-18",
        "tasks": [{"task_id": "T001", "status": "completed", "progress": 100,
                   "updated_on": "2026-09-10", "completed_on": "2026-09-10"}],
    })
    report = await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)
    assert report.status == expected_status
    assert report.tasks[0].status == "completed"
    assert report.tasks[0].actual_progress == 100
    assert report.tasks[0].variance == 20
    assert not report.tasks[0].actual_is_stale
    assert report.tasks[0].last_observed_progress is None
    assert report.tasks[0].completed_late_days == late_days
    assert report.metrics.actual_progress == 100
    assert report.metrics.completed_tasks == 1
    assert report.metrics.stale_actual_tasks == report.metrics.overdue_tasks == report.metrics.delayed_tasks == 0
    assert report.milestones[0].completed
    assert report.milestones[0].status == milestone_status
    assert not any(w.code == "STALE_ACTUAL_TASK" for w in report.warnings)
    assert [message.data["reason"] for message in report.planning_feedback] == (["completed_late"] if late_days else [])


@pytest.mark.asyncio
async def test_stale_task_keeps_historical_late_start_but_not_overdue_feedback():
    sample = FixtureProviders()
    sample.planning.tasks[0].due_date = date(2026, 9, 8)
    sample.planning.milestones[0].due_date = date(2026, 9, 8)
    child = sample.planning.tasks[0].model_copy(update={
        "task_id": "T002", "title": "Integration", "start_date": date(2026, 9, 19),
        "due_date": date(2026, 9, 29), "dependency_ids": ["T001"], "expected_progress": None,
    })
    sample.planning.tasks.append(child)
    sample.actual.tasks[0].updated_on = date(2026, 9, 10)
    sample.actual.tasks[0].actual_start = date(2026, 9, 2)
    report = await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)
    assert report.status == "AT_RISK"
    assert report.tasks[0].status == "stale"
    assert report.tasks[0].start_delay_days == 1
    assert report.tasks[0].overdue_days == 0
    assert not report.tasks[0].delayed
    assert not report.tasks[1].dependency_delayed
    assert not report.tasks[1].delayed
    assert report.metrics.stale_actual_tasks == report.metrics.missing_actual_tasks == 1
    assert report.metrics.overdue_tasks == report.metrics.delayed_tasks == 0
    assert report.metrics.actual_progress is report.metrics.progress_variance is None
    assert [message.model_dump() for message in report.planning_feedback] == [{
        "sender": "project_monitor_report_agent", "receiver": "planning_agent",
        "action": "SCHEDULE_DELAY_DETECTED", "request_id": "req-1",
        "data": {"project_id": "P001", "task_id": "T001", "delay_days": 1,
                 "reason": "late_start", "affected_tasks": ["T002"]},
    }]
    warning = next(w for w in report.warnings if w.code == "LATE_START")
    assert "historical start observed on 2026-09-02" in warning.message
    assert any(fact.fact_id == "late_start" for fact in report.facts)
    assert not any(w.code == "OVERDUE_TASK" for w in report.warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize("first_state,second_state,overdue", [
    ("in_progress", "missing", True), ("in_progress", "stale", True),
    ("completed", "missing", False), ("completed", "stale", False),
    ("stale", "missing", False), ("stale", "stale", False),
])
async def test_milestone_overdue_requires_any_fresh_unfinished_member(first_state, second_state, overdue):
    sample = FixtureProviders()
    sample.planning.tasks.append(sample.planning.tasks[0].model_copy(update={
        "task_id": "T002", "title": "Secondary task",
    }))
    sample.planning.milestones[0].task_ids.append("T002")
    sample.planning.milestones[0].due_date = date(2026, 9, 15)
    first = {"task_id": "T001", "status": "in_progress", "progress": 80,
             "updated_on": "2026-09-18"}
    if first_state == "completed":
        first.update(status="completed", progress=100, updated_on="2026-09-14", completed_on="2026-09-14")
    elif first_state == "stale":
        first["updated_on"] = "2026-09-10"
    tasks = [first]
    if second_state == "stale":
        tasks.append({"task_id": "T002", "status": "in_progress", "progress": 80,
                      "updated_on": "2026-09-10"})
    sample.actual = ActualProjectSnapshot.model_validate({
        "project_id": "P001", "updated_on": "2026-09-18", "tasks": tasks,
    })
    report = await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)
    milestone = report.milestones[0]
    assert milestone.overdue is overdue
    assert milestone.status == ("Delayed" if overdue else "At Risk")
    assert not milestone.completed
    assert milestone.progress is None
    assert report.status == ("DELAYED" if overdue else "AT_RISK")
    assert report.metrics.actual_progress is report.metrics.progress_variance is None
    assert report.metrics.overdue_tasks == report.metrics.delayed_tasks == 0
    assert report.metrics.stale_actual_tasks == int(first_state == "stale") + int(second_state == "stale")
    assert report.metrics.missing_actual_tasks == int(second_state == "missing")
    assert any(w.code == "MILESTONE_DELAYED" for w in report.warnings) is overdue
    assert report.planning_feedback == []


@pytest.mark.asyncio
async def test_fresh_blocked_task_is_at_risk_even_when_progress_matches_target():
    sample = FixtureProviders()
    sample.actual.tasks[0].status = "blocked"
    sample.actual.tasks[0].progress = 80
    settings = MonitoringSettings(deadline_approaching_days=1)
    report = await MonitoringAgent(sample, sample, settings=settings).generate(request(), PRINCIPAL)
    assert report.status == "AT_RISK"
    assert report.metrics.actual_progress == report.metrics.expected_progress == 80
    assert report.metrics.progress_variance == report.metrics.delayed_tasks == report.metrics.overdue_tasks == 0
    assert report.metrics.blocked_tasks == report.metrics.in_progress_tasks == 1
    assert report.metrics.stale_actual_tasks == 0
    assert not report.tasks[0].actual_is_stale
    assert not report.tasks[0].delayed
    warning = next(w for w in report.warnings if w.code == "BLOCKED_TASK")
    assert warning.task_ids == ["T001"]
    assert warning.source_refs == ["task:T001"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["valid", "invalid", "exception"])
async def test_summarizer_cannot_mutate_verified_facts_or_fallback(outcome):
    sample = FixtureProviders()
    baseline = await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)

    class MutatingSelector:
        received_facts: list[SummaryFact] | None = None

        async def select_facts(self, facts: list[SummaryFact]) -> SummarySelection:
            self.received_facts = facts
            facts[0].fact_id = "injected"
            facts[0].text = "UNVERIFIED replacement status"
            facts[0].source_refs.append("untrusted:injected")
            facts.append(SummaryFact(fact_id="fabricated", text="UNVERIFIED extra fact",
                                     source_refs=["untrusted:injected"]))
            if outcome == "exception":
                raise RuntimeError("Synthetic selector failure after mutation")
            return SummarySelection(fact_ids=["status", "fabricated" if outcome == "invalid" else "progress"])

    selector = MutatingSelector()
    report = await MonitoringAgent(sample, sample, summarizer=selector).generate(request(), PRINCIPAL)
    assert selector.received_facts is not None
    assert report.facts == baseline.facts
    assert report.metrics == baseline.metrics
    assert "UNVERIFIED" not in report.summary
    assert not any("untrusted:injected" in fact.source_refs for fact in report.facts)
    if outcome == "valid":
        assert report.summary_method == "llm_extractive"
        assert report.summary_fact_ids == ["status", "progress", "risk_coverage"]
        by_id = {fact.fact_id: fact for fact in baseline.facts}
        assert report.summary.endswith(" ".join(by_id[fact_id].text for fact_id in report.summary_fact_ids))
    else:
        assert report.summary_method == "deterministic_extractive"
        assert report.summary_fact_ids == baseline.summary_fact_ids
        assert report.summary == baseline.summary
        assert "AI fact selection unavailable or invalid; deterministic summary used" in report.limitations
    selector.received_facts[0].text = "UNVERIFIED later mutation"
    selector.received_facts[0].source_refs.clear()
    assert report.facts == baseline.facts


@pytest.mark.asyncio
@pytest.mark.parametrize("priority", ["low", "medium", "high", "critical"])
@pytest.mark.parametrize("critical", [False, True])
async def test_fresh_overdue_critical_tasks_are_delayed_independently_of_priority(priority, critical):
    sample = FixtureProviders()
    sample.planning.milestones = []
    sample.planning.tasks[0].priority = priority
    sample.planning.tasks[0].is_critical = critical
    sample.actual.tasks[0].progress = 80
    settings = MonitoringSettings(delayed_variance=100)
    report = await MonitoringAgent(sample, sample, settings=settings).generate(request("2026-09-21"), PRINCIPAL)
    severe = critical or priority in ("high", "critical")
    assert report.status == ("DELAYED" if severe else "AT_RISK")
    assert report.tasks[0].is_critical is critical
    assert report.tasks[0].overdue_days == 1
    assert sample.planning.tasks[0].priority == priority
    warning = next(w for w in report.warnings if w.code == "OVERDUE_TASK")
    assert warning.severity == ("critical" if severe else "warning")


@pytest.mark.asyncio
async def test_critical_path_does_not_turn_stale_or_missing_progress_into_overdue():
    sample = FixtureProviders()
    sample.planning.milestones = []
    sample.planning.tasks[0].priority = "low"
    sample.planning.tasks[0].due_date = date(2026, 9, 15)
    sample.planning.context = {"analysis": {"critical_path": ["T001"]}}
    sample.actual.tasks[0].updated_on = date(2026, 9, 10)
    for actual in (sample, None):
        report = await MonitoringAgent(sample, actual).generate(request(), PRINCIPAL)
        assert report.tasks[0].is_critical
        assert report.status == "AT_RISK"
        assert report.tasks[0].actual_progress is report.tasks[0].variance is None
        assert report.tasks[0].overdue_days == 0
        assert not any(w.code == "OVERDUE_TASK" for w in report.warnings)


@pytest.mark.asyncio
async def test_assessment_exposes_supplied_dates_without_forecasts():
    sample = FixtureProviders()
    sample.actual.tasks[0].actual_start = date(2026, 9, 2)
    report = await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)
    task = report.tasks[0]
    assert task.planned_start == date(2026, 9, 1)
    assert task.due_date == date(2026, 9, 20)
    assert task.actual_start == date(2026, 9, 2)
    assert task.completed_on is None
    assert task.last_updated_on == date(2026, 9, 18)
    sample.actual.tasks[0].status = "completed"
    sample.actual.tasks[0].progress = 100
    sample.actual.tasks[0].completed_on = date(2026, 9, 17)
    completed = await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)
    assert completed.tasks[0].completed_on == date(2026, 9, 17)


@pytest.mark.asyncio
async def test_coverage_exact_contract_and_full_report_persisted_before_publish():
    sample = FixtureProviders()
    sample.planning.fetched_on = date(2026, 9, 18)
    sample.actual.observed_on = date(2026, 9, 18)
    sample.risk.observed_on = date(2026, 9, 18)
    repository = Mock(spec=["save"], save=AsyncMock())
    coordinator = Mock(spec=["publish"], publish=AsyncMock())
    report = await MonitoringAgent(sample, sample, sample, repository=repository, coordinator=coordinator).generate(
        request(), PRINCIPAL)
    assert report.coverage is not None
    assert report.coverage.model_dump(mode="json") == {
        "planning_status": "available", "planning_freshness": "known", "actual_status": "complete",
        "risk_status": "available", "milestones_status": "available", "total_tasks": 1,
        "fresh_actual_tasks": 1, "missing_actual_tasks": 0, "stale_actual_tasks": 0,
        "planning_updated_on": "2026-09-18", "planning_observed_on": "2026-09-18",
        "actual_updated_on": "2026-09-18", "actual_observed_on": "2026-09-18",
        "risk_observed_on": "2026-09-18", "expected_progress_method": "supplied_target",
        "actual_progress_method": "unweighted_task_average",
    }
    repository.save.assert_awaited_once_with(report, PRINCIPAL)
    assert coordinator.publish.await_args.args[0].report.coverage == report.coverage


@pytest.mark.asyncio
@pytest.mark.parametrize("states,status,counts", [
    (["fresh", "fresh"], "complete", (2, 0, 0)),
    (["fresh", "missing"], "partial", (1, 1, 0)),
    (["fresh", "stale"], "partial", (1, 0, 1)),
    (["stale", "stale"], "stale", (0, 0, 2)),
    (["stale", "missing"], "stale", (0, 1, 1)),
    (["missing", "missing"], "missing", (0, 2, 0)),
    (["completed", "fresh"], "complete", (2, 0, 0)),
])
async def test_actual_coverage_counts_use_individual_measurements(states, status, counts):
    sample = FixtureProviders()
    sample.planning.tasks.append(sample.planning.tasks[0].model_copy(update={"task_id": "T002"}))
    records = []
    for index, state in enumerate(states, 1):
        if state == "missing":
            continue
        record = {"task_id": f"T00{index}", "status": "in_progress", "progress": 80,
                  "updated_on": "2026-09-10" if state in ("stale", "completed") else "2026-09-18"}
        if state == "completed":
            record.update(status="completed", progress=100, completed_on="2026-09-10")
        records.append(record)
    sample.actual = ActualProjectSnapshot.model_validate({
        "project_id": "P001", "updated_on": "2026-09-18", "tasks": records,
    })
    report = await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)
    assert report.coverage is not None
    assert report.coverage.actual_status == status
    assert (report.coverage.fresh_actual_tasks, report.coverage.missing_actual_tasks,
            report.coverage.stale_actual_tasks) == counts
    assert report.coverage.total_tasks == sum(counts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("updated_on,freshness", [(None, "unknown"), ("2026-09-11", "known"), ("2026-09-10", "stale")])
@pytest.mark.parametrize("targets,method", [([80], "supplied_target"), ([None], "calendar_estimate"),
                                         ([80, None], "mixed"), ([], "unavailable")])
async def test_coverage_planning_freshness_and_expected_method(updated_on, freshness, targets, method):
    sample = FixtureProviders()
    sample.planning = PlanningSnapshot.model_validate({
        "project_id": "P001", "updated_on": updated_on,
        "tasks": [{"task_id": f"T00{index}", "title": "Planned work", "start_date": "2026-09-01",
                   "due_date": "2026-09-20", "expected_progress": target}
                  for index, target in enumerate(targets, 1)],
    })
    report = await MonitoringAgent(sample).generate(request(), PRINCIPAL)
    assert report.coverage is not None
    assert report.coverage.planning_freshness == freshness
    assert report.coverage.expected_progress_method == method
    assert report.coverage.actual_status == "missing"
    assert report.coverage.milestones_status == "not_supplied"
    assert report.coverage.actual_updated_on is report.coverage.actual_observed_on is None
    assert report.metrics.actual_progress is None


@pytest.mark.asyncio
async def test_unknown_risk_coverage_is_distinct_from_available_zero_risks():
    sample = FixtureProviders()
    sample.actual.tasks[0].progress = 80
    sample.risk.risks = []
    settings = MonitoringSettings(deadline_approaching_days=1)
    unknown = await MonitoringAgent(sample, sample, settings=settings).generate(request(), PRINCIPAL)
    known = await MonitoringAgent(sample, sample, sample, settings=settings).generate(request(), PRINCIPAL)
    assert unknown.coverage is not None and known.coverage is not None
    assert unknown.metrics.high_risks == known.metrics.high_risks == 0
    assert unknown.coverage.risk_status == "not_supplied"
    assert known.coverage.risk_status == "available"
    assert "risk coverage is unknown" in unknown.summary
    assert "zero risk count does not establish no risks" in unknown.summary
    assert "0 open high/critical risk(s) were supplied" in known.summary
    assert "risk coverage is unknown" not in known.summary
    assert unknown.status == known.status == "ON_TRACK"


@pytest.mark.asyncio
async def test_stale_risk_snapshot_does_not_assert_current_zero_risks():
    sample = FixtureProviders()
    sample.actual.tasks[0].progress = 80
    sample.risk.risks = []
    sample.risk.updated_on = date(2026, 9, 10)
    report = await MonitoringAgent(sample, sample, sample, settings=MonitoringSettings(deadline_approaching_days=1)).generate(
        request(), PRINCIPAL)
    assert report.coverage is not None
    assert report.coverage.risk_status == "stale"
    assert report.coverage.risk_observed_on is None
    assert report.status == "AT_RISK"
    assert any(w.code == "STALE_RISK" for w in report.warnings)
    assert "risk coverage is unknown (risk snapshot is stale)" in report.summary


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["api_error", "timeout", "http_timeout"])
@pytest.mark.parametrize("policy", ["fail", "report_unknown"])
async def test_risk_failure_policy_preserves_default_fail_and_optional_unknown(failure, policy):
    sample = FixtureProviders()
    sample.actual.tasks[0].progress = 80
    risk = Mock(spec=["get_risks"], get_risks=AsyncMock())
    error = {"api_error": RiskAgentAvailabilityError("Sensitive synthetic error must not appear in reports"),
             "timeout": TimeoutError(),
             "http_timeout": httpx.ReadTimeout("Sensitive synthetic timeout must not appear in reports")}[failure]
    risk.get_risks.side_effect = error
    repository = Mock(spec=["save"], save=AsyncMock())
    coordinator = Mock(spec=["publish"], publish=AsyncMock())
    settings = MonitoringSettings(risk_failure_policy=policy, deadline_approaching_days=1)
    agent = MonitoringAgent(sample, sample, risk, settings=settings, repository=repository, coordinator=coordinator)
    if policy == "fail":
        with pytest.raises(type(error)) as raised:
            await agent.generate(request(), PRINCIPAL)
        assert raised.value is error
        repository.save.assert_not_awaited()
        coordinator.publish.assert_not_awaited()
    else:
        report = await agent.generate(request(), PRINCIPAL)
        assert report.status == "AT_RISK"
        assert report.coverage is not None
        assert report.coverage.risk_status == "unavailable"
        assert report.coverage.risk_observed_on is None
        assert report.risks == [] and report.risk_summary == ""
        assert report.metrics.high_risks == 0
        assert any(w.code == "RISK_UNAVAILABLE" for w in report.warnings)
        assert "Risk Agent unavailable" in report.summary
        assert any("risks are unknown" in item for item in report.limitations)
        assert "Sensitive synthetic" not in report.model_dump_json()
        repository.save.assert_awaited_once_with(report, PRINCIPAL)
        coordinator.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_risk_failure_does_not_downgrade_independently_observed_delay():
    sample = FixtureProviders()
    risk = Mock(spec=["get_risks"], get_risks=AsyncMock(side_effect=RiskAgentAvailabilityError("Risk unavailable")))
    report = await MonitoringAgent(sample, sample, risk, settings=MonitoringSettings(
        risk_failure_policy="report_unknown")).generate(request(), PRINCIPAL)
    assert report.coverage is not None and report.coverage.risk_status == "unavailable"
    assert report.status == "DELAYED"
    assert any(w.code == "RISK_UNAVAILABLE" for w in report.warnings)


@pytest.mark.asyncio
async def test_risk_wait_for_timeout_reports_unknown_without_fake_snapshot():
    sample = FixtureProviders()
    sample.actual.tasks[0].progress = 80

    class SlowRisk:
        async def get_risks(self, project_id):
            await asyncio.sleep(1)
            pytest.fail("The provider must be cancelled before returning a snapshot")

    report = await MonitoringAgent(sample, sample, SlowRisk(), settings=MonitoringSettings(
        risk_failure_policy="report_unknown", provider_timeout_seconds=0.02, deadline_approaching_days=1,
    )).generate(request(), PRINCIPAL)
    assert report.coverage is not None
    assert report.coverage.risk_status == "unavailable"
    assert report.status == "AT_RISK"
    assert report.risks == []


@pytest.mark.asyncio
@pytest.mark.parametrize("contextual", [False, True])
@pytest.mark.parametrize("invalid", ["plan_project", "actual_project", "unknown_task", "plan_date", "plan_observation",
                                    "actual_date", "actual_observation", "actual_task", "analysis"])
async def test_invalid_inputs_never_reach_risk(contextual, invalid):
    sample = FixtureProviders()
    if invalid == "plan_project":
        sample.planning.project_id = "P002"
    elif invalid == "actual_project":
        sample.actual.project_id = "P002"
    elif invalid == "unknown_task":
        sample.actual.tasks[0].task_id = "UNKNOWN"
    elif invalid == "plan_date":
        sample.planning.updated_on = date(2026, 9, 19)
    elif invalid == "plan_observation":
        sample.planning.fetched_on = date(2026, 9, 19)
    elif invalid == "actual_date":
        sample.actual.updated_on = date(2026, 9, 19)
    elif invalid == "actual_observation":
        sample.actual.observed_on = date(2026, 9, 19)
    elif invalid == "actual_task":
        sample.actual.tasks[0].progress = 101
    else:
        sample.planning.context = {"analysis": {"deadline_met": "false"}}
    risk = Mock(spec=["get_risks", "get_risks_for_monitoring"] if contextual else ["get_risks"],
                get_risks=AsyncMock())
    if contextual:
        risk.get_risks_for_monitoring = AsyncMock()
    agent = MonitoringAgent(sample, sample, risk, settings=MonitoringSettings(risk_failure_policy="report_unknown"))
    with pytest.raises(ValueError):
        await agent.generate(request(), PRINCIPAL)
    risk.get_risks.assert_not_awaited()
    if contextual:
        risk.get_risks_for_monitoring.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["planning", "actual"])
@pytest.mark.parametrize("policy", ["fail", "report_unknown"])
async def test_planning_and_actual_source_failure_is_never_downgraded_to_risk_unknown(source, policy):
    sample = FixtureProviders()
    broken = Mock(spec=["get_plan", "get_actual"], get_plan=AsyncMock(), get_actual=AsyncMock())
    broken.get_plan.side_effect = broken.get_actual.side_effect = ValueError("Source unavailable")
    risk = Mock(spec=["get_risks"], get_risks=AsyncMock())
    agent = MonitoringAgent(broken if source == "planning" else sample,
                            broken if source == "actual" else sample, risk,
                            settings=MonitoringSettings(risk_failure_policy=policy))
    with pytest.raises(ValueError, match="Source unavailable"):
        await agent.generate(request(), PRINCIPAL)
    risk.get_risks.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_risk_snapshot_is_not_hidden_by_failure_policy():
    sample = FixtureProviders()
    sample.risk.risks[0].task_ids = ["UNKNOWN"]
    with pytest.raises(ValueError, match="unknown tasks"):
        await MonitoringAgent(sample, sample, sample, settings=MonitoringSettings(
            risk_failure_policy="report_unknown")).generate(request(), PRINCIPAL)


@pytest.mark.asyncio
async def test_context_is_bounded_including_configured_information_and_never_truncated():
    sample = FixtureProviders()
    risk = Mock(spec=["get_risks", "get_risks_for_monitoring", "project_information"],
                get_risks=AsyncMock(), get_risks_for_monitoring=AsyncMock())
    risk.project_information = "Original project description"
    risk.get_risks_for_monitoring.return_value = None
    await MonitoringAgent(sample, sample, risk).generate(request(), PRINCIPAL)
    information = risk.get_risks_for_monitoring.await_args.args[1]
    limit = len(information)
    assert 1000 <= limit < 100000
    risk.reset_mock()
    settings = MonitoringSettings(risk_context_max_chars=limit)
    await MonitoringAgent(sample, sample, risk, settings=settings).generate(request(), PRINCIPAL)
    assert risk.get_risks_for_monitoring.await_args.args[1] == information
    risk.reset_mock()
    settings.risk_context_max_chars = limit - 1
    with pytest.raises(ValueError, match="exceeds risk_context_max_chars"):
        await MonitoringAgent(sample, sample, risk, settings=settings).generate(request(), PRINCIPAL)
    risk.get_risks_for_monitoring.assert_not_awaited()
    risk.get_risks.assert_not_awaited()
    risk.project_information = "Large configured description " * 10000
    with pytest.raises(ValueError, match="exceeds risk_context_max_chars"):
        await MonitoringAgent(sample, sample, risk).generate(request(), PRINCIPAL)
    risk.get_risks_for_monitoring.assert_not_awaited()


@pytest.mark.asyncio
async def test_risk_context_and_reports_omit_credentials_without_mutating_sources():
    sample = FixtureProviders()
    sample.planning.context = {
        "project_name": "Safe baseline", "api_key": "secret-source-key",
        "nested": {"access_token": "secret-source-token", "description": "Safe source description"},
    }
    sample.actual.context = {"credentials": {"password": "secret-source-password"}, "source": "actual-input"}
    originals = deepcopy((sample.planning.context, sample.actual.context))
    sample.actual.tasks[0].updated_on = date(2026, 9, 10)
    risk = Mock(spec=["get_risks_for_monitoring", "get_risks"],
                get_risks=AsyncMock(), get_risks_for_monitoring=AsyncMock())
    risk.get_risks_for_monitoring.return_value = None
    repository, coordinator = AsyncMock(), AsyncMock()
    report = await MonitoringAgent(sample, sample, risk, repository=repository,
                                   coordinator=coordinator).generate(request(), PRINCIPAL)
    information = risk.get_risks_for_monitoring.await_args.args[1]
    context = json.loads(information)
    assert "secret-source" not in information
    assert context["planned_baseline"]["context"]["nested"]["description"] == "Safe source description"
    assert context["observed_actual_measurements"]["tasks"][0]["progress"] == 50
    assert context["measurement_freshness"]["T001"] == {"actual_is_stale": True, "current_progress": None}
    assert report.planning_context == {
        "project_name": "Safe baseline", "nested": {"description": "Safe source description"},
    }
    assert report.actual_context == {"source": "actual-input"}
    assert "secret-source" not in report.model_dump_json()
    assert repository.save.await_args.args[0].planning_context == report.planning_context
    assert "secret-source" not in coordinator.publish.await_args.args[0].model_dump_json()
    assert (sample.planning.context, sample.actual.context) == originals


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [
    "accessToken", "clientSecret", "APIKey", "X-API-Key", "session.access_token",
    " refresh-token ", "privateKey", "awsAccessKeyId", "Cookie", "Proxy-Authorization",
    "serviceCredentials", "ACCESS_TOKEN", "clientSECRET", "access_token", "serverKey", "signingKey",
])
async def test_risk_context_filters_nested_and_serialized_credentials_without_losing_evidence(field: str):
    sample = FixtureProviders()
    marker = "synthetic-sensitive-field"
    evidence: dict[str, JsonValue] = {"description": "Vendor review is pending", "document_id": "review-1"}
    record: dict[str, JsonValue] = {field: marker, **evidence}
    sample.planning.context = {
        field: marker, "nested": [record], "serialized": json.dumps({"records": [record]}),
        "serialized_evidence": '{ "document_id": "review-1", "page": 3 }',
        "key_findings": ["Vendor review is pending"],
    }
    sample.actual.context = {"source_payload": {"records": [record]}}
    originals = deepcopy((sample.planning.context, sample.actual.context))

    configured = json.dumps(json.dumps(record))
    risk = Mock(spec=["get_risks_for_monitoring", "get_risks", "project_information"],
                get_risks_for_monitoring=AsyncMock(return_value=None), get_risks=AsyncMock())
    risk.project_information = configured
    report = await MonitoringAgent(sample, sample, risk).generate(request(), PRINCIPAL)
    information = risk.get_risks_for_monitoring.await_args.args[1]
    context = json.loads(information)
    baseline = context["planned_baseline"]["context"]
    assert marker not in information
    assert baseline["nested"] == [evidence]
    assert json.loads(baseline["serialized"]) == {"records": [evidence]}
    assert baseline["serialized_evidence"] == originals[0]["serialized_evidence"]
    assert baseline["key_findings"] == ["Vendor review is pending"]
    assert context["observed_actual_measurements"]["context"]["source_payload"]["records"] == [evidence]
    assert json.loads(json.loads(context["configured_project_information"])) == evidence
    assert report.planning_context == baseline
    assert report.actual_context == context["observed_actual_measurements"]["context"]
    assert marker not in report.model_dump_json()
    assert (sample.planning.context, sample.actual.context) == originals
    assert risk.project_information == configured
    risk.get_risks.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RiskAgentAPIError, RiskAgentIntegrityError])
@pytest.mark.parametrize("contextual", [False, True])
async def test_degraded_policy_does_not_catch_integrity_or_unclassified_errors(error_type, contextual):
    sample = FixtureProviders()
    failure = error_type("Synthetic integrity failure")
    risk = Mock(spec=["get_risks_for_monitoring"] if contextual else ["get_risks"])
    method = AsyncMock(side_effect=failure)
    setattr(risk, "get_risks_for_monitoring" if contextual else "get_risks", method)
    repository = Mock(spec=["save"], save=AsyncMock())
    coordinator = Mock(spec=["publish"], publish=AsyncMock())
    with pytest.raises(error_type) as captured:
        await MonitoringAgent(sample, sample, risk, repository=repository, coordinator=coordinator,
                              settings=MonitoringSettings(risk_failure_policy="report_unknown")).generate(
            request(), PRINCIPAL)
    assert captured.value is failure
    method.assert_awaited_once()
    repository.save.assert_not_awaited()
    coordinator.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_risk_fact_cannot_be_omitted_by_optional_selector():
    sample = FixtureProviders()
    selector = Mock(spec=["select_facts"], select_facts=AsyncMock())
    selector.select_facts.return_value = SummarySelection(fact_ids=["status"])
    risk = Mock(spec=["get_risks"], get_risks=AsyncMock())
    risk.get_risks.side_effect = RiskAgentAvailabilityError("Risk failed")
    report = await MonitoringAgent(sample, sample, risk, summarizer=selector, settings=MonitoringSettings(
        risk_failure_policy="report_unknown")).generate(request(), PRINCIPAL)
    assert report.summary_fact_ids == ["status", "risk_coverage"]
    assert "Current risk coverage is unknown (Risk Agent unavailable)" in report.summary


@pytest.mark.asyncio
async def test_legacy_report_has_no_invented_coverage_or_task_date_basis():
    sample = FixtureProviders()
    report = await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)
    payload = report.model_dump(mode="json")
    del payload["coverage"]
    del payload["actual_context"]
    for task in payload["tasks"]:
        for name in ("is_critical", "planned_start", "due_date", "actual_start", "completed_on", "last_updated_on"):
            del task[name]
    legacy = MonitoringReport.model_validate(payload)
    assert legacy.coverage is None
    assert legacy.actual_context == {}
    assert not legacy.tasks[0].is_critical
    assert legacy.tasks[0].planned_start is legacy.tasks[0].due_date is legacy.tasks[0].last_updated_on is None
    assert legacy.model_dump(mode="json")["coverage"] is None


def test_additive_model_and_setting_defaults():
    sample = FixtureProviders()
    task = PlannedTask.model_validate(sample.planning.tasks[0].model_dump(exclude={"is_critical"}))
    assert task.is_critical is False
    assessment = TaskAssessment(task_id="T001", title="Work", status="missing", actual_progress=None,
                                expected_progress=0, variance=None, overdue_days=0, completed_late_days=0)
    assert not assessment.is_critical
    assert all(getattr(assessment, name) is None for name in
               ("planned_start", "due_date", "actual_start", "completed_on", "last_updated_on"))
    settings = MonitoringSettings()
    assert settings.risk_failure_policy == "fail"
    assert settings.risk_context_max_chars == 100000


@pytest.mark.parametrize("setting,value", [("risk_failure_policy", "ignore"), ("risk_context_max_chars", 999),
                                          ("risk_context_max_chars", 1000001), ("risk_context_max_chars", True)])
def test_invalid_risk_settings_are_rejected(setting, value):
    with pytest.raises(ValidationError):
        MonitoringSettings.model_validate({setting: value})


@pytest.mark.parametrize("name", ["total_tasks", "fresh_actual_tasks", "missing_actual_tasks", "stale_actual_tasks"])
def test_coverage_counts_cannot_be_negative(name):
    payload = {"planning_freshness": "unknown", "actual_status": "missing", "risk_status": "not_supplied",
               "milestones_status": "not_supplied", "total_tasks": 0, "fresh_actual_tasks": 0,
               "missing_actual_tasks": 0, "stale_actual_tasks": 0, "expected_progress_method": "unavailable"}
    payload[name] = -1
    with pytest.raises(ValidationError):
        MonitoringCoverage.model_validate(payload)
