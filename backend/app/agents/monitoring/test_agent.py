"""Run with: python -m pytest app/agents/monitoring/test_agent.py"""

from datetime import date

import pytest

from .adapters import adapt_actual, adapt_planning, adapt_risks
from .agent import MonitoringAgent
from .mock_data import SampleProviders
from .schemas import (
    ActualProjectSnapshot, MonitoringPrincipal, MonitoringRequest, MonitoringSettings,
    PlanningSnapshot, RiskSnapshot, SummarySelection,
)
from .security import MonitoringAccessDenied


PRINCIPAL = MonitoringPrincipal.model_validate({
    "subject": "manager", "project_ids": ["P001"], "permissions": ["monitoring:generate"],
})


def request(day="2026-09-18"):
    return MonitoringRequest(project_id="P001", request_id="req-1", as_of=date.fromisoformat(day))


@pytest.mark.asyncio
async def test_variance_delay_risk_and_report():
    report = await MonitoringAgent(SampleProviders(), SampleProviders(), SampleProviders()).generate(
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
    sample = SampleProviders()
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
    sample = SampleProviders()
    sample.actual = ActualProjectSnapshot(project_id="P001", updated_on=date(2026, 9, 18), tasks=[])
    report = await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)
    assert report.metrics.actual_progress is None
    assert report.metrics.missing_actual_tasks == 1
    assert report.status == "AT_RISK"
    assert any(w.code == "MISSING_ACTUAL" for w in report.warnings)


@pytest.mark.asyncio
async def test_dependency_delay_and_linear_expected_progress():
    sample = SampleProviders()
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
    sample = SampleProviders()
    sample.planning = PlanningSnapshot(project_id="P001", updated_on=date(2026, 9, 18))
    sample.actual = ActualProjectSnapshot(project_id="P001", updated_on=date(2026, 9, 1))
    report = await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)
    assert report.status == "AT_RISK"
    assert report.metrics.actual_progress is None
    assert any(w.code == "STALE_ACTUAL" for w in report.warnings)


@pytest.mark.asyncio
async def test_mismatched_project_or_unknown_task_rejected():
    sample = SampleProviders()
    class WrongProject:
        async def get_actual(self, project_id):
            return sample.actual.model_copy(update={"project_id": "P002"})

    with pytest.raises(ValueError, match="requested project"):
        await MonitoringAgent(sample, WrongProject()).generate(request(), PRINCIPAL)
    sample = SampleProviders()
    sample.actual = ActualProjectSnapshot.model_validate({
        "project_id": "P001", "updated_on": "2026-09-18",
        "tasks": [{"task_id": "UNKNOWN", "progress": 0, "status": "not_started",
                   "updated_on": "2026-09-18"}],
    })
    with pytest.raises(ValueError, match="unknown task"):
        await MonitoringAgent(sample, sample).generate(request(), PRINCIPAL)


@pytest.mark.asyncio
async def test_unauthorized_request_never_fetches_data():
    sample = SampleProviders()
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

    sample = SampleProviders()
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
    sample = SampleProviders()
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

    sample.actual = SampleProviders().actual
    settings = MonitoringSettings(delay_threshold=35, delayed_variance=40,
                                  at_risk_variance=10, deadline_approaching_days=1)
    report = await MonitoringAgent(sample, sample, settings=settings).generate(request(), PRINCIPAL)
    assert report.status == "AT_RISK"
    assert report.metrics.delayed_tasks == 0


def test_sample_payload_adapters_and_validation():
    planning = adapt_planning({"project_id": "P001", "tasks": [{
        "task_id": "T001", "task_name": "Backend Development",
        "planned_start_date": "2026-09-01", "planned_end_date": "2026-09-20",
        "expected_progress": 80, "priority": "High", "milestone": "Backend Complete",
        "dependencies": [],
    }]}, date(2026, 9, 18))
    actual = adapt_actual({"project_id": "P001", "tasks": [{
        "task_id": "T001", "actual_progress": 50, "status": "In Progress",
        "last_updated": "2026-09-18", "actual_completion_date": None,
    }]}, date(2026, 9, 18))
    risk = adapt_risks({"project_id": "P001", "risks": [{
        "risk_id": "R001", "task_id": "T001", "risk_level": "High",
        "description": "API integration issue",
    }]}, date(2026, 9, 18))
    assert planning.milestones[0].title == "Backend Complete"
    assert actual.tasks[0].status == "in_progress"
    assert risk.risks[0].task_ids == ["T001"]
    with pytest.raises(ValueError):
        RiskSnapshot.model_validate({"project_id": "P001", "updated_on": "2026-09-18",
            "risks": [{"risk_id": "R001", "level": "urgent", "description": "Invalid"}]})
