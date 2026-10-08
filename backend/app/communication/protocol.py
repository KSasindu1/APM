
from app.agents.risk_knowledge.agent import RiskKnowledgeAgent
from app.communication.messages import (
    KnowledgeSearchMessage,
    ProjectPlanMessage,
    RiskRequestMessage,
)


class RiskAgentProtocol:
    """
    Communication interface for the Risk & Knowledge Retrieval Agent.

    Supports:
    - Planning Agent -> Risk Agent project-plan messages
    - Other agents -> Risk Agent risk-analysis requests
    - Other agents -> Risk Agent knowledge-search requests

    Communication is performed through the API layer.
    This class handles the message after it reaches the
    Risk Agent backend.
    """

    def __init__(
        self,
        agent: RiskKnowledgeAgent | None = None,
    ):
        self.agent = agent or RiskKnowledgeAgent()

    def process_plan(
        self,
        message: ProjectPlanMessage,
    ):
        """
        Process a structured project plan received from
        the Planning & Scheduling Agent.

        The structured planning information is converted into
        project context for the Risk Agent's existing RAG,
        NLP, retrieval, and LLM pipeline.
        """

        project_information_parts = []

        if message.project_name:
            project_information_parts.append(
                f"Project name: {message.project_name}"
            )

        if message.objective:
            project_information_parts.append(
                f"Project objective: {message.objective}"
            )

        if message.project_dates:
            project_information_parts.append(
                f"Project dates: {message.project_dates}"
            )

        if message.tasks:
            project_information_parts.append(
                f"Project tasks: {message.tasks}"
            )

        if message.critical_path:
            project_information_parts.append(
                f"Critical path: {message.critical_path}"
            )

        if message.critical_path_duration_days is not None:
            project_information_parts.append(
                "Critical path duration: "
                f"{message.critical_path_duration_days} days"
            )

        project_information = "\n".join(
            project_information_parts
        )

        return self.agent.analyse_project(
            project_id=message.project_id,
            project_information=project_information,
            query=(
                "Identify project risks in the newly created "
                "project plan. Analyse task dependencies, "
                "critical-path risks, timeline issues, "
                "resource constraints, inconsistencies, "
                "and risks supported by the existing project "
                "knowledge."
            ),
        )

    def process_request(
        self,
        request: RiskRequestMessage,
    ):
        """
        Analyse a project using the existing Risk Agent
        contract.

        This method is intentionally unchanged in behaviour
        so existing Monitoring Agent integration remains
        compatible.
        """

        return self.agent.analyse_project(
            project_id=request.project_id,
            project_information=request.project_information,
            query=request.query,
            top_k=request.top_k,
        )

    def search_knowledge(
        self,
        request: KnowledgeSearchMessage,
    ):
        """
        Retrieve project knowledge from ChromaDB and
        PostgreSQL without performing LLM analysis.
        """

        return self.agent.search_project_knowledge(
            project_id=request.project_id,
            query=request.query,
            top_k=request.top_k,
        )
