"""Request-scoped account access for the shared Monitoring router only."""
from __future__ import annotations

import asyncio
from datetime import date
from typing import TYPE_CHECKING, Literal, overload
from uuid import UUID

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError

from .api import _ServiceError
from .providers import adapt_planning_schedule
from .schemas import Identifier, MonitoringPrincipal


_identifier = TypeAdapter(Identifier)

if TYPE_CHECKING:
    from app.core.project_access import ProjectActor
    from app.model.planning import ScheduleResponse
    from app.repository.planning_repository import PlanningRepository


class MonitoringAccountAccess:
    def __init__(self, authorization: str | None, timeout: float):
        self.authorization = authorization
        self.timeout = timeout
        self.actor: ProjectActor | None = None
        self._repository: PlanningRepository | None = None
        self.unsupported_project_count = 0

    async def _actor(self) -> ProjectActor:
        # Lazy imports keep standalone Monitoring isolated from shared services.
        from app.core.project_access import require_project_actor

        if self.authorization is None:
            raise _ServiceError(401, "Authentication is required.", headers={"WWW-Authenticate": "Bearer"})
        try:
            actor = await asyncio.wait_for(asyncio.to_thread(
                require_project_actor, authorization=self.authorization, x_api_key=None,
            ), self.timeout)
            if actor.role not in ("user", "admin"):
                raise _ServiceError(401, "Invalid authentication token.", headers={"WWW-Authenticate": "Bearer"})
            if actor.user_id is not None:
                if str(UUID(actor.user_id)) != actor.user_id:
                    raise ValueError("Invalid account ID")
            elif actor.role == "user":
                raise ValueError("Missing account ID")
            if self.actor is not None and actor != self.actor:
                raise _ServiceError(401, "Invalid authentication token.", headers={"WWW-Authenticate": "Bearer"})
            self.actor = actor
            return actor
        except HTTPException as exc:
            raise _ServiceError(exc.status_code, exc.detail, headers=exc.headers) from None
        except (ValueError, TypeError, AttributeError):
            raise _ServiceError(401, "Invalid authentication token.", headers={"WWW-Authenticate": "Bearer"}) from None
        except Exception:
            raise _ServiceError(503, "Monitoring authentication is unavailable") from None

    @overload
    async def _query(self, method: Literal["get_all"], project_id: None = None) -> list[ScheduleResponse]: ...

    @overload
    async def _query(self, method: Literal["get"], project_id: str) -> ScheduleResponse: ...

    @overload
    async def _query(self, method: Literal["exists"], project_id: str) -> bool: ...

    async def _query(self, method: str, project_id: str | None = None):
        actor = await self._actor()
        owner = actor.owner_filter

        def query():
            if self._repository is None:
                from app.repository.planning_repository import PlanningRepository

                self._repository = PlanningRepository()
            repository = self._repository
            if project_id is None:
                return repository.get_all(owner_user_id=owner)
            return getattr(repository, method)(project_id, owner_user_id=owner)

        try:
            result = await asyncio.wait_for(asyncio.to_thread(query), self.timeout)
        except Exception:
            raise _ServiceError(503, "Monitoring project storage is unavailable") from None
        if project_id is not None and not result:
            raise _ServiceError(404, "Monitoring project not found")
        return result

    def _principal(self, project_ids):
        # Managed subjects are UUIDs, not usernames; bootstrap usernames may be Unicode.
        assert self.actor is not None
        return MonitoringPrincipal(
            subject=self.actor.user_id or "bootstrap-admin",
            project_ids=frozenset(project_ids),
            permissions=frozenset({"monitoring:read", "monitoring:generate"}),
        )

    async def principal(self, project_id: str | None):
        if project_id is not None:
            await self._query("exists", project_id)
            return self._principal({project_id})
        schedules = await self._query("get_all")
        project_ids = set()
        for schedule in schedules:
            try:
                await self.recheck(schedule.project_id)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
            else:
                try:
                    _identifier.validate_python(schedule.project_id)
                except ValidationError:
                    # Planning permits broader IDs. One legacy ID must not make
                    # every compatible project inaccessible through /session.
                    self.unsupported_project_count += 1
                else:
                    project_ids.add(schedule.project_id)
        await self._actor()
        return self._principal(project_ids)

    async def recheck(self, project_id: str):
        await self._query("exists", project_id)

    async def get_plan(self, project_id: str):
        schedule = await self._query("get", project_id)
        return adapt_planning_schedule(schedule.model_dump(mode="json"), date.today())
