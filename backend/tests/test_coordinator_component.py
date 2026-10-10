import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from app.agents.coordinator.agent import CoordinatorAgent
from app.agents.coordinator.planning_gateway import PlanningGateway
from app.agents.coordinator.schemas import CoordinatorRequest
from app.agents.coordinator.nlp import extract_project
from app.model.planning import ScheduleResponse


def request():
    return CoordinatorRequest(project_name="Inventory", objective="Build a stock tracking application.", start_date="2026-10-12", deadline="2026-12-12")


def schedule(project_id, met=True):
    return ScheduleResponse.model_validate({
        "project_id": project_id, "project_name": "Inventory", "schedule": [],
        "analysis": {"total_duration_days": 10 if met else 65, "available_duration_days": 61,
                     "deadline_met": met, "delay_days": 0 if met else 4,
                     "critical_path": [], "critical_path_duration_days": 10, "utilization_percentage": 50},
        "responsible_ai": {"ai_generated": False, "human_review_required": True, "confidence": .35,
                           "explanation": "Fallback template.", "limitations": ["Review estimates."]},
        "warnings": ["Risk Agent unavailable."],
    })


@pytest.mark.parametrize("met", [True, False])
def test_mapping_and_decision(met):
    async def create(payload):
        assert "message" not in payload.model_dump()
        assert "User planning requirements:" in payload.objective
        return schedule(payload.project_id, met)
    result = asyncio.run(CoordinatorAgent(SimpleNamespace(create_schedule=create)).handle(request()))
    assert result.status == "completed"
    assert ("fits the deadline" if met else "4 days") in result.recommendation
    assert "Risk Agent unavailable." in result.warnings


@pytest.mark.parametrize("exc,status", [(ValueError("Invalid dependencies"), "needs_clarification"), (RuntimeError("Database error"), "failed")])
def test_failure_states(exc, status):
    result = asyncio.run(CoordinatorAgent(SimpleNamespace(create_schedule=AsyncMock(side_effect=exc))).handle(request()))
    assert result.status == status
    assert result.planning_result is None
    assert "Database error" not in (result.error or "")


def test_gateway_preserves_risk_workflow_and_saves(monkeypatch):
    payload = request()
    result = schedule(payload.project_id)
    service = SimpleNamespace(create_schedule_with_risk=AsyncMock(return_value=result))
    repository = SimpleNamespace(save=Mock())
    monkeypatch.setitem(sys.modules, "app.api.planning", SimpleNamespace(planning_service=service, planning_repository=repository))
    assert asyncio.run(PlanningGateway().create_schedule(payload)) == result
    service.create_schedule_with_risk.assert_awaited_once_with(payload)
    repository.save.assert_called_once_with(result)


def test_extract_project_and_team():
    result = extract_project("Project: Inventory System. Build stock tracking. Start date: 2026-10-12. Deadline: 2026-12-12. We have 3 developers and 1 tester.")
    assert result.draft["project_name"] == "Inventory System"
    assert not result.missing_fields
    assert [item["capacity"] for item in result.draft["resources"]] == [3, 1]


def test_no_invented_dates():
    result = extract_project("Build an inventory application next month. Start: 2026-02-30.")
    assert "start_date" in result.missing_fields
    assert "deadline" in result.missing_fields
    assert any("Invalid" in note for note in result.notes)
