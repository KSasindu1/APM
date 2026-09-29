"""Explicit opt-in demo providers; never substitute these for missing live data."""

from datetime import date

from .schemas import ActualProjectSnapshot, PlanningSnapshot, RiskSnapshot


class SampleProviders:
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
            raise ValueError("Unknown sample project")
        return self.planning.model_copy(deep=True)

    async def get_actual(self, project_id: str) -> ActualProjectSnapshot:
        if project_id != self.actual.project_id:
            raise ValueError("Unknown sample project")
        return self.actual.model_copy(deep=True)

    async def get_risks(self, project_id: str) -> RiskSnapshot:
        if project_id != self.risk.project_id:
            raise ValueError("Unknown sample project")
        return self.risk.model_copy(deep=True)
