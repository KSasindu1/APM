from app.agents.risk_knowledge.agent import RiskKnowledgeAgent
from app.communication.messages import (
    KnowledgeSearchMessage,
    ProjectPlanMessage,
    RiskRequestMessage,
)


class RiskAgentProtocol:
    """
    Internal communication protocol for the Risk & Knowledge Agent.

    Other agents can use this interface to send:
    - newly created project plans
    - risk-analysis requests
    - knowledge-search requests
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
        Analyse a newly created plan received from the
        Planning & Scheduling Agent.

        The plan can be analysed immediately while the Planning
        Agent also stores its official version in PostgreSQL.
        """

        return self.agent.analyse_project(
            project_id=message.project_id,
            project_information=(
                f"Plan title: {message.plan_title}\n\n"
                f"Plan content:\n{message.plan_content}"
            ),
            query=(
                "Identify risks, dependencies, "
                "timeline issues, resource constraints, "
                "and inconsistencies in this project plan."
            ),
        )

    def process_request(
        self,
        request: RiskRequestMessage,
    ):
        """
        Analyse a project using documents, PostgreSQL planning
        information, and current agent-provided information.
        """

        return self.agent.analyse_project(
            project_id=request.project_id,
            project_information=(
                request.project_information
            ),
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