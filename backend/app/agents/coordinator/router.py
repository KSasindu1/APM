from fastapi import APIRouter, Header
from app.agents.coordinator.agent import CoordinatorAgent
from app.agents.coordinator.planning_gateway import PlanningGateway
from app.agents.coordinator.schemas import CoordinatorRequest, CoordinatorResponse
from app.agents.coordinator.nlp import NaturalLanguageRequest, NaturalLanguageResult, extract_project

router = APIRouter(prefix="/api/coordinator", tags=["Coordinator & Decision Agent"])
coordinator = CoordinatorAgent(PlanningGateway())


@router.post("/interpret", response_model=NaturalLanguageResult)
async def interpret(request: NaturalLanguageRequest):
    return extract_project(request.text)


@router.post("/request", response_model=CoordinatorResponse)
async def coordinate(request: CoordinatorRequest, x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    from app.api.planning import verify_api_key
    verify_api_key(x_api_key)
    return await coordinator.handle(request)
