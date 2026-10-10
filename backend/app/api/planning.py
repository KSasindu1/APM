from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.account_security import canonical_username
from app.core.project_access import (
    ProjectActor,
    require_planning_service,
    require_project_actor,
    require_project_admin,
)
from app.core.project_access import verify_api_key as verify_api_key
from app.model.planning import (
    AgentMessage,
    AgentMessageResponse,
    ProjectPlanningRequest,
    ScheduleResponse,
)
from app.repository.planning_repository import PlanningRepository, ProjectScheduleExists
from app.services.account_store import get_account_store
from app.services.planning_service import PlanningService


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/planning",
    tags=["Planning & Scheduling Agent"],
)
planning_service = PlanningService()
planning_repository = PlanningRepository()


class ProjectOwnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    username: str = Field(min_length=3, max_length=50)


def _repository_call(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return operation(*args, **kwargs)
    except ProjectScheduleExists:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Schedule already exists."
        ) from None
    except Exception:
        logger.exception("Planning ownership storage is unavailable.")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Project storage is unavailable."
        ) from None


def _schedule_not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found.")


async def _generate_schedule(request: ProjectPlanningRequest) -> ScheduleResponse:
    try:
        return await planning_service.create_schedule_with_risk(request)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Invalid planning request."
        ) from None
    except Exception:
        logger.exception("Unable to generate schedule.")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Unable to generate schedule."
        ) from None


@router.get("/health")
async def planning_health():
    return {
        "agent": "Planning & Scheduling Agent",
        "status": "healthy",
    }


@router.post("/schedule", response_model=ScheduleResponse)
async def create_schedule(
    request: ProjectPlanningRequest,
    actor: Annotated[ProjectActor, Depends(require_project_actor)],
):
    if actor.role != "service":
        # Check only uniqueness before generation; never expose the existing payload.
        existing = _repository_call(
            planning_repository.get, request.project_id, owner_user_id=None,
        )
        if existing is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Schedule already exists.")

    result = await _generate_schedule(request)
    if actor.role == "service":
        # Retain the trusted service upsert contract; save must preserve any owner.
        _repository_call(planning_repository.save, result)
    else:
        # INSERT also enforces uniqueness when another creator wins during generation.
        _repository_call(
            planning_repository.create, result, owner_user_id=actor.user_id,
        )
    return result


@router.get("/schedule/{project_id}", response_model=ScheduleResponse)
async def get_schedule(
    project_id: str,
    actor: Annotated[ProjectActor, Depends(require_project_actor)],
):
    result = _repository_call(
        planning_repository.get, project_id, owner_user_id=actor.owner_filter,
    )
    if result is None:
        raise _schedule_not_found()
    return result


@router.get("/schedules", response_model=list[ScheduleResponse])
async def get_all_schedules(
    actor: Annotated[ProjectActor, Depends(require_project_actor)],
):
    return _repository_call(
        planning_repository.get_all, owner_user_id=actor.owner_filter,
    )


@router.put("/schedule/{project_id}", response_model=ScheduleResponse)
async def update_schedule(
    project_id: str,
    request: ProjectPlanningRequest,
    actor: Annotated[ProjectActor, Depends(require_project_actor)],
):
    existing = _repository_call(
        planning_repository.get, project_id, owner_user_id=actor.owner_filter,
    )
    if existing is None:
        raise _schedule_not_found()

    if project_id != request.project_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Project ID in URL and request body must match.",
        )

    result = await _generate_schedule(request)
    updated = _repository_call(
        planning_repository.update, project_id, result, owner_user_id=actor.owner_filter,
    )
    if updated is None:
        raise _schedule_not_found()
    return updated


@router.delete("/schedule/{project_id}")
async def delete_schedule(
    project_id: str,
    actor: Annotated[ProjectActor, Depends(require_project_actor)],
):
    deleted = _repository_call(
        planning_repository.delete, project_id, owner_user_id=actor.owner_filter,
    )
    if not deleted:
        raise _schedule_not_found()
    return {"project_id": project_id, "deleted": True}


@router.put("/schedule/{project_id}/owner")
async def assign_schedule_owner(
    project_id: str,
    request: ProjectOwnerRequest,
    actor: Annotated[ProjectActor, Depends(require_project_admin)],
):
    existing = _repository_call(
        planning_repository.get, project_id, owner_user_id=actor.owner_filter,
    )
    if existing is None:
        raise _schedule_not_found()

    try:
        username = canonical_username(request.username)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid owner username.") from None

    try:
        account = get_account_store().find(username)
    except Exception:
        logger.exception("Project ownership account storage is unavailable.")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Project storage is unavailable."
        ) from None

    if account is None or not account.active or account.role != "user":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "An active regular user is required."
        )

    assigned = _repository_call(
        planning_repository.assign_owner, project_id, account.user_id,
    )
    if not assigned:
        raise _schedule_not_found()
    return {
        "project_id": project_id,
        "owner_user_id": account.user_id,
        "owner_username": account.username,
    }


@router.post("/message", response_model=AgentMessageResponse)
async def handle_agent_message(
    message: AgentMessage,
    actor: Annotated[ProjectActor, Depends(require_planning_service)],
):
    if message.action != "CREATE_SCHEDULE":
        return AgentMessageResponse(
            sender="planning_agent",
            receiver=message.sender,
            action=message.action,
            request_id=message.request_id,
            status="failed",
            error={
                "code": "UNSUPPORTED_ACTION",
                "message": f"Unsupported action: {message.action}",
            },
        )

    try:
        request = ProjectPlanningRequest(**message.data)
        result = await planning_service.create_schedule_with_risk(request)
    except ValueError:
        return AgentMessageResponse(
            sender="planning_agent",
            receiver=message.sender,
            action="SCHEDULE_CREATED",
            request_id=message.request_id,
            status="failed",
            error={"code": "PLANNING_ERROR", "message": "Invalid planning request."},
        )
    except Exception:
        logger.exception("Planning agent-to-agent request failed.")
        return AgentMessageResponse(
            sender="planning_agent",
            receiver=message.sender,
            action="SCHEDULE_CREATED",
            request_id=message.request_id,
            status="failed",
            error={"code": "INTERNAL_ERROR", "message": "Planning agent failed."},
        )

    try:
        _repository_call(planning_repository.save, result)
    except HTTPException as exc:
        response = AgentMessageResponse(
            sender="planning_agent",
            receiver=message.sender,
            action="SCHEDULE_CREATED",
            request_id=message.request_id,
            status="failed",
            error={"code": "INTERNAL_ERROR", "message": "Planning agent failed."},
        )
        return JSONResponse(status_code=exc.status_code, content=response.model_dump(mode="json"))

    return AgentMessageResponse(
        sender="planning_agent",
        receiver=message.sender,
        action="SCHEDULE_CREATED",
        request_id=message.request_id,
        status="success",
        data=result.model_dump(mode="json"),
    )
