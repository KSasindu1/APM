from typing import Literal
from uuid import uuid4
from pydantic import ConfigDict, Field, BaseModel, field_validator
from app.model.planning import ProjectPlanningRequest, ScheduleResponse


class CoordinatorRequest(ProjectPlanningRequest):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(default_factory=lambda: f"PRJ-{uuid4().hex[:12]}", min_length=1, max_length=100)
    message: str = Field(default="Create a schedule and assess deadline feasibility.", min_length=1, max_length=1500)

    @field_validator("project_id", "project_name", "objective", "message")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value


class CoordinatorResponse(BaseModel):
    request_id: str
    project_id: str
    status: Literal["completed", "needs_clarification", "failed"]
    agents_called: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    planning_request: ProjectPlanningRequest | None = None
    planning_result: ScheduleResponse | None = None
    recommendation: str | None = None
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
