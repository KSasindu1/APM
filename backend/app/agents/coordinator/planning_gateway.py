"""In-process adapter preserves Planning's current risk and persistence workflow."""
from app.model.planning import ProjectPlanningRequest, ScheduleResponse


class PlanningGateway:
    async def create_schedule(self, request: ProjectPlanningRequest) -> ScheduleResponse:
        # Reuse the configured instances, without modifying another member's code.
        from app.api.planning import planning_service, planning_repository
        from starlette.concurrency import run_in_threadpool

        result = await planning_service.create_schedule_with_risk(request)
        await run_in_threadpool(planning_repository.save, result)
        return result
