from pydantic import BaseModel, Field


class ProjectPlanMessage(BaseModel):
    """
    Message that the Planning Agent can send to the
    Risk & Knowledge Agent.

    The plan is also stored in PostgreSQL by the Planning Agent.
    """

    project_id: str
    source_agent: str
    plan_title: str
    plan_content: str


class RiskRequestMessage(BaseModel):
    """
    Request from another ProjectFlow AI agent asking the
    Risk & Knowledge Agent to analyse a project.
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