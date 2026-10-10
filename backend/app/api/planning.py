
from __future__ import annotations

import logging
import secrets

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    status,
)

from app.core.config import get_settings
from app.model.planning import (
    AgentMessage,
    AgentMessageResponse,
    ProjectPlanningRequest,
    ScheduleResponse,
)
from app.repository.planning_repository import (
    PlanningRepository,
)
from app.services.planning_service import (
    PlanningService,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/planning",
    tags=["Planning & Scheduling Agent"],
)


planning_service = PlanningService()
planning_repository = PlanningRepository()


def verify_api_key(
    x_api_key: str | None,
) -> None:

    settings = get_settings()

    expected_key = settings.planning_api_key

    # API key is optional in development.
    if not expected_key:
        return

    if not x_api_key:

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required.",
        )

    if not secrets.compare_digest(
        x_api_key,
        expected_key,
    ):

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )


@router.get("/health")
async def planning_health():

    return {
        "agent": "Planning & Scheduling Agent",
        "status": "healthy",
    }


@router.post(
    "/schedule",
    response_model=ScheduleResponse,
)
async def create_schedule(
    request: ProjectPlanningRequest,
    x_api_key: str | None = Header(
        default=None,
        alias="X-API-Key",
    ),
):

    verify_api_key(x_api_key)

    try:

        # ADDED: Generate plan and request risk analysis.
        result = await (
            planning_service.create_schedule_with_risk(
                request
            )
        )

        planning_repository.save(result)

        return result

    except ValueError as exc:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:

        logger.exception(
            "Planning repository/configuration error."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    except Exception as exc:

        logger.exception(
            "Unable to create schedule."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to create schedule.",
        ) from exc


@router.get(
    "/schedule/{project_id}",
    response_model=ScheduleResponse,
)
async def get_schedule(
    project_id: str,
    x_api_key: str | None = Header(
        default=None,
        alias="X-API-Key",
    ),
):

    verify_api_key(x_api_key)

    try:

        result = planning_repository.get(
            project_id
        )

    except RuntimeError as exc:

        logger.exception(
            "Supabase configuration/repository error."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    except Exception as exc:

        logger.exception(
            "Unable to retrieve schedule."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve schedule.",
        ) from exc

    if not result:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule not found.",
        )

    return result


@router.get(
    "/schedules",
    response_model=list[ScheduleResponse],
)
async def get_all_schedules(
    x_api_key: str | None = Header(
        default=None,
        alias="X-API-Key",
    ),
):

    verify_api_key(x_api_key)

    try:

        return planning_repository.get_all()

    except Exception as exc:

        logger.exception(
            "Unable to retrieve schedules."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to retrieve schedules.",
        ) from exc


@router.put(
    "/schedule/{project_id}",
    response_model=ScheduleResponse,
)
async def update_schedule(
    project_id: str,
    request: ProjectPlanningRequest,
    x_api_key: str | None = Header(
        default=None,
        alias="X-API-Key",
    ),
):

    verify_api_key(x_api_key)

    if project_id != request.project_id:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Project ID in URL and request body "
                "must match."
            ),
        )

    try:

        existing = planning_repository.get(
            project_id
        )

        if existing is None:

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule not found.",
            )

        # ADDED: Risk analysis on updated plans too.
        result = await (
            planning_service.create_schedule_with_risk(
                request
            )
        )

        updated = planning_repository.update(
            project_id,
            result,
        )

        if updated is None:

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule not found.",
            )

        return updated

    except HTTPException:
        raise

    except ValueError as exc:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except Exception as exc:

        logger.exception(
            "Unable to update schedule."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update schedule.",
        ) from exc


@router.delete(
    "/schedule/{project_id}"
)
async def delete_schedule(
    project_id: str,
    x_api_key: str | None = Header(
        default=None,
        alias="X-API-Key",
    ),
):

    verify_api_key(x_api_key)

    try:

        deleted = planning_repository.delete(
            project_id
        )

    except Exception as exc:

        logger.exception(
            "Unable to delete schedule."
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to delete schedule.",
        ) from exc

    if not deleted:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule not found.",
        )

    return {
        "project_id": project_id,
        "deleted": True,
    }


@router.post(
    "/message",
    response_model=AgentMessageResponse,
)
async def handle_agent_message(
    message: AgentMessage,
    x_api_key: str | None = Header(
        default=None,
        alias="X-API-Key",
    ),
):

    verify_api_key(x_api_key)

    if message.action != "CREATE_SCHEDULE":

        return AgentMessageResponse(
            sender="planning_agent",
            receiver=message.sender,
            action=message.action,
            request_id=message.request_id,
            status="failed",
            error={
                "code": "UNSUPPORTED_ACTION",
                "message": (
                    f"Unsupported action: "
                    f"{message.action}"
                ),
            },
        )

    try:

        request = ProjectPlanningRequest(
            **message.data
        )

        # ADDED: Risk analysis for agent-to-agent requests.
        result = await (
            planning_service.create_schedule_with_risk(
                request
            )
        )

        planning_repository.save(result)

        return AgentMessageResponse(
            sender="planning_agent",
            receiver=message.sender,
            action="SCHEDULE_CREATED",
            request_id=message.request_id,
            status="success",
            data=result.model_dump(
                mode="json"
            ),
        )

    except ValueError as exc:

        return AgentMessageResponse(
            sender="planning_agent",
            receiver=message.sender,
            action="SCHEDULE_CREATED",
            request_id=message.request_id,
            status="failed",
            error={
                "code": "PLANNING_ERROR",
                "message": str(exc),
            },
        )

    except Exception:

        logger.exception(
            "Planning agent-to-agent request failed."
        )

        return AgentMessageResponse(
            sender="planning_agent",
            receiver=message.sender,
            action="SCHEDULE_CREATED",
            request_id=message.request_id,
            status="failed",
            error={
                "code": "INTERNAL_ERROR",
                "message": "Planning agent failed.",
            },
        )