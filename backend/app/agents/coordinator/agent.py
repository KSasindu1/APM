import logging
from uuid import uuid4
from app.agents.coordinator.schemas import CoordinatorRequest, CoordinatorResponse
from app.model.planning import ProjectPlanningRequest

logger = logging.getLogger(__name__)


class CoordinatorAgent:
    def __init__(self, planning):
        self.planning = planning

    async def handle(self, request: CoordinatorRequest) -> CoordinatorResponse:
        response = CoordinatorResponse(request_id=str(uuid4()), project_id=request.project_id, status="completed")
        objective = f"{request.objective}\nUser planning requirements: {request.message}"
        if len(objective) > 5000:
            response.status = "needs_clarification"
            response.clarification_questions = ["Shorten the combined objective and instructions to 5,000 characters."]
            return response
        payload = ProjectPlanningRequest.model_validate({**request.model_dump(exclude={"message"}), "objective": objective})
        response.planning_request = payload
        response.agents_called = ["planning_agent"]
        try:
            result = await self.planning.create_schedule(payload)
            if result.project_id != request.project_id:
                raise RuntimeError("Planning returned a different project.")
        except ValueError as exc:
            response.status = "needs_clarification"
            response.clarification_questions = [str(exc)]
            return response
        except Exception:
            logger.exception("Coordinator request %s failed", response.request_id)
            response.status = "failed"
            response.error = "Planning could not return a saved schedule. Check backend configuration and logs before retrying."
            return response
        response.planning_result = result
        analysis = result.analysis
        response.recommendation = (
            "The generated schedule fits the deadline. Review the estimates and warnings before approving it."
            if analysis.deadline_met else
            f"The schedule exceeds the deadline by {analysis.delay_days} days. Review scope, resources, or a revised deadline with the team."
        )
        response.reasons = [f"Planned duration: {analysis.total_duration_days} days; available: {analysis.available_duration_days} days.",
                           f"Critical path: {', '.join(analysis.critical_path) or 'not supplied'}.", result.responsible_ai.explanation]
        response.warnings = list(dict.fromkeys([*result.warnings, *analysis.warnings, *result.responsible_ai.limitations,
                                               "Actual-progress monitoring was not performed. Planning's existing risk workflow is preserved."]))
        return response
