from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)


class TaskPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TaskStatus(str, Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskInput(BaseModel):
    risk_id: str | None = None
    task_id: str | None = None
    risk_level: RiskLevel = RiskLevel.LOW

    impact_days: int = Field(
        default=0,
        ge=0,
        le=365,
    )

    description: str | None = Field(
        default=None,
        max_length=2000,
    )

    @field_validator(
        "risk_id",
        "task_id",
        "description",
    )
    @classmethod
    def clean_optional_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()
        return value or None


class ResourceInput(BaseModel):
    resource_id: str | None = None

    name: str = Field(
        min_length=1,
        max_length=200,
    )

    capacity: int = Field(
        default=1,
        ge=1,
        le=100,
    )

    @field_validator("resource_id")
    @classmethod
    def clean_resource_id(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()
        return value or None

    @field_validator("name")
    @classmethod
    def clean_name(
        cls,
        value: str,
    ) -> str:
        return value.strip()


class TaskInput(BaseModel):
    task_id: str = Field(
        min_length=1,
        max_length=100,
    )

    name: str = Field(
        min_length=1,
        max_length=200,
    )

    description: str | None = Field(
        default=None,
        max_length=2000,
    )

    duration_days: int = Field(
        default=1,
        ge=1,
        le=365,
    )

    priority: TaskPriority = TaskPriority.MEDIUM

    dependencies: list[str] = Field(
        default_factory=list,
    )

    resource_id: str | None = None

    @field_validator("task_id", "name")
    @classmethod
    def strip_required_text(
        cls,
        value: str,
    ) -> str:
        return value.strip()

    @field_validator(
        "description",
        "resource_id",
    )
    @classmethod
    def strip_optional_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()
        return value or None

    @field_validator("dependencies")
    @classmethod
    def clean_dependencies(
        cls,
        value: list[str],
    ) -> list[str]:
        result: list[str] = []

        for dependency in value:
            dependency = str(dependency).strip()

            if dependency and dependency not in result:
                result.append(dependency)

        return result

    @field_validator(
        "priority",
        mode="before",
    )
    @classmethod
    def normalize_priority(
        cls,
        value: Any,
    ) -> Any:
        if isinstance(value, TaskPriority):
            return value

        if isinstance(value, str):
            return value.strip().upper()

        return value


class ProjectPlanningRequest(BaseModel):
    project_id: str = Field(
        min_length=1,
        max_length=100,
    )

    project_name: str = Field(
        min_length=1,
        max_length=200,
    )

    objective: str = Field(
        min_length=10,
        max_length=5000,
    )

    start_date: date
    deadline: date

    tasks: list[TaskInput] = Field(
        default_factory=list,
    )

    resources: list[ResourceInput] = Field(
        default_factory=list,
    )

    risks: list[RiskInput] = Field(
        default_factory=list,
    )

    use_llm: bool = True

    @field_validator(
        "project_id",
        "project_name",
        "objective",
    )
    @classmethod
    def clean_text(
        cls,
        value: str,
    ) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_dates(self) -> "ProjectPlanningRequest":
        if self.deadline < self.start_date:
            raise ValueError(
                "Project deadline cannot be before project start date."
            )

        return self


class ScheduledTask(BaseModel):
    task_id: str
    name: str
    description: str | None = None

    duration_days: int

    priority: TaskPriority
    status: TaskStatus = TaskStatus.PLANNED

    dependencies: list[str] = Field(
        default_factory=list,
    )

    planned_start: date
    planned_end: date

    resource_id: str | None = None

    is_critical: bool = False

    risk_level: RiskLevel = RiskLevel.LOW

    risk_buffer_days: int = 0

    rationale: str


class ScheduleAnalysis(BaseModel):
    total_duration_days: int
    available_duration_days: int

    deadline_met: bool
    delay_days: int

    critical_path: list[str]

    critical_path_duration_days: int

    utilization_percentage: float

    warnings: list[str] = Field(
        default_factory=list,
    )


class ResponsibleAIInfo(BaseModel):
    ai_generated: bool

    human_review_required: bool

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    explanation: str

    limitations: list[str] = Field(
        default_factory=list,
    )


class ScheduleResponse(BaseModel):
    project_id: str
    project_name: str

    schedule: list[ScheduledTask]

    analysis: ScheduleAnalysis

    responsible_ai: ResponsibleAIInfo

    warnings: list[str] = Field(
        default_factory=list,
    )


class AgentMessage(BaseModel):
    sender: str
    receiver: str
    action: str
    request_id: str
    data: dict[str, Any]


class AgentMessageResponse(BaseModel):
    sender: str
    receiver: str
    action: str
    request_id: str
    status: str

    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None