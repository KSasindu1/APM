from fastapi import APIRouter, Depends, Header
from app.core.project_access import ProjectActor, require_project_actor
from app.core.security import auth_error
from app.agents.coordinator.agent import CoordinatorAgent
from app.agents.coordinator.planning_gateway import PlanningGateway
from app.agents.coordinator.schemas import CoordinatorRequest, CoordinatorResponse
from app.agents.coordinator.nlp import NaturalLanguageRequest, NaturalLanguageResult, extract_project

router = APIRouter(prefix="/api/coordinator", tags=["Coordinator & Decision Agent"])
coordinator = CoordinatorAgent(PlanningGateway())


def require_coordinator_actor(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ProjectActor:
    # Unlike trusted planning routes, browser interpretation has no service fallback.
    if authorization is None:
        raise auth_error(401, "Authentication is required.")
    actor = require_project_actor(authorization=authorization, x_api_key=None)
    if actor.role not in ("user", "admin"):
        raise auth_error(403, "Account authentication is required.")
    return actor


@router.post("/interpret", response_model=NaturalLanguageResult)
async def interpret(request: NaturalLanguageRequest, _actor: ProjectActor = Depends(require_coordinator_actor)):
    return extract_project(request.text)


@router.post("/request", response_model=CoordinatorResponse)
async def coordinate(request: CoordinatorRequest, x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    from app.api.planning import verify_api_key
    verify_api_key(x_api_key)
    return await coordinator.handle(request)
