
from typing import Any

from pydantic import BaseModel, Field


class ProjectPlanMessage(BaseModel):
    """
    Structured project-plan message sent by the
    Planning & Scheduling Agent to the
    Risk & Knowledge Retrieval Agent.

    The Planning Agent can also store the official plan
    in PostgreSQL. This message provides the current plan
    information directly to the Risk Agent.
    """

    sender: str = "planning_agent"
    receiver: str = "risk_knowledge_agent"
    action: str = "ASSESS_PROJECT_RISKS"
    request_id: str = ""

    project_id: str
    project_name: str = ""
    objective: str = ""

    project_dates: dict[str, Any] = Field(
        default_factory=dict,
    )

    tasks: list[dict[str, Any]] = Field(
        default_factory=list,
    )

    critical_path: list[str] = Field(
        default_factory=list,
    )

    critical_path_duration_days: int | None = None


class RiskRequestMessage(BaseModel):
    """
    Request from another ProjectFlow AI agent asking the
    Risk & Knowledge Retrieval Agent to analyse a project.

    This contract is kept compatible with the existing
    Monitoring Agent integration.
    """

    project_id: str
    source_agent: str
    query: str
    project_information: str = ""
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
    )


class KnowledgeSearchMessage(BaseModel):
    """
    Request for project knowledge retrieval without performing
    full LLM-based risk analysis.
    """

    project_id: str
    source_agent: str
    query: str
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
    )
