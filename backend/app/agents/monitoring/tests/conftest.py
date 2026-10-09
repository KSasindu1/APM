"""Synthetic providers used only by offline monitoring tests."""

import httpx
import pytest

from app.agents.monitoring.schemas import ActualProjectSnapshot, PlanningSnapshot, RiskSnapshot


@pytest.fixture(autouse=True)
def forbid_real_http(monkeypatch):
    async def reject_async_request(*args, **kwargs):
        pytest.fail("Monitoring tests must use MockTransport, never real HTTP")

    def reject_sync_request(*args, **kwargs):
        pytest.fail("Monitoring tests must use MockTransport, never real HTTP")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_async_request)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject_sync_request)


class FixtureProviders:
    def __init__(self):
        self.planning = PlanningSnapshot.model_validate({
            "project_id": "P001", "updated_on": "2026-09-18",
            "tasks": [{"task_id": "T001", "title": "Backend Development",
                       "start_date": "2026-09-01", "due_date": "2026-09-20",
                       "expected_progress": 80, "priority": "high"}],
            "milestones": [{"milestone_id": "M001", "title": "Backend Complete",
                            "due_date": "2026-09-20", "task_ids": ["T001"]}],
        })
        self.actual = ActualProjectSnapshot.model_validate({
            "project_id": "P001", "updated_on": "2026-09-18",
            "tasks": [{"task_id": "T001", "progress": 50, "status": "in_progress",
                       "updated_on": "2026-09-18"}],
        })
        self.risk = RiskSnapshot.model_validate({
            "project_id": "P001", "updated_on": "2026-09-18",
            "risks": [{"risk_id": "R001", "task_ids": ["T001"], "level": "high",
                       "description": "API integration issue"}],
        })

    async def get_plan(self, project_id: str) -> PlanningSnapshot:
        if project_id != self.planning.project_id:
            raise ValueError("Unknown fixture project")
        return self.planning.model_copy(deep=True)

    async def get_actual(self, project_id: str) -> ActualProjectSnapshot:
        if project_id != self.actual.project_id:
            raise ValueError("Unknown fixture project")
        return self.actual.model_copy(deep=True)

    async def get_risks(self, project_id: str) -> RiskSnapshot:
        if project_id != self.risk.project_id:
            raise ValueError("Unknown fixture project")
        return self.risk.model_copy(deep=True)


@pytest.fixture
def planning_payload():
    """A synthetic payload matching Planning's real ScheduleResponse fields."""
    return {
        "project_id": "P001",
        "project_name": "Offline monitoring contract",
        "schedule": [{
            "task_id": "T001", "name": "Backend Development",
            "description": "Verify the offline monitoring contract",
            "duration_days": 10, "priority": "HIGH", "status": "PLANNED",
            "dependencies": [], "planned_start": "2026-10-17",
            "planned_end": "2026-10-27", "resource_id": "resource-1",
            "is_critical": True, "risk_level": "LOW", "risk_buffer_days": 0,
            "rationale": "Complete this prerequisite before integration",
        }],
        "analysis": {
            "total_duration_days": 10, "available_duration_days": 14,
            "deadline_met": True, "delay_days": 0, "critical_path": ["T001"],
            "critical_path_duration_days": 10, "utilization_percentage": 72.5,
            "warnings": [],
        },
        "responsible_ai": {
            "ai_generated": False, "human_review_required": True,
            "confidence": 1.0, "explanation": "Synthetic offline fixture",
            "limitations": ["Execution updates are supplied separately"],
        },
        "warnings": [],
    }


@pytest.fixture
def planning_message(planning_payload):
    return {
        "sender": "planning_agent", "receiver": "project_monitor_report_agent",
        "action": "SCHEDULE_CREATED", "request_id": "planning-message-test",
        "status": "success", "data": planning_payload,
    }


@pytest.fixture
def actual_payload():
    return {
        "project_id": "P001",
        "tasks": [{
            "task_id": "T001", "status": "IN_PROGRESS", "progress_percentage": 40,
            "actual_start": "2026-10-18", "actual_end": None,
        }],
    }
