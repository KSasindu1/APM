
from pydantic import BaseModel, Field


class RiskItem(BaseModel):
    risk_id: str

    task_id: str | None = None

    risk: str

    description: str

    risk_level: str

    impact_days: int | None = Field(
        default=None,
        ge=0,
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    evidence: str

    source: str

    affected_area: str

    recommended_action: str


class RiskAnalysisResult(BaseModel):
    project_id: str

    risks: list[RiskItem]

    summary: str

    evidence_used: list[str]

    knowledge_sources: list[str] = Field(
        default_factory=list,
    )


class ProjectKnowledgeSearchRequest(BaseModel):
    project_id: str

    query: str

    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
    )


class ProjectPlanAnalysisRequest(BaseModel):
    project_id: str

    query: str = (
        "Identify current project risks, "
        "planning issues, dependencies, "
        "resource constraints, and missing information."
    )

    project_information: str = ""

    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
    )


class KnowledgeSearchResult(BaseModel):
    project_id: str

    results: list[dict]
