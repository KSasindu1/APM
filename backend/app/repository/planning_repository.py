from __future__ import annotations

import threading

from postgrest.exceptions import APIError
from supabase import Client, create_client

from app.core.config import get_settings
from app.model.planning import ScheduleResponse


class ProjectScheduleExists(Exception):
    pass


class PlanningRepository:
    """
    Supabase-backed schedule repository.

    The complete ScheduleResponse is stored inside
    the JSONB `schedule` column. A None owner scope is reserved
    for trusted admin/service callers.
    """

    TABLE_NAME = "project_schedules"

    def __init__(self) -> None:
        self._client: Client | None = None
        self._lock = threading.Lock()

    @property
    def client(self) -> Client:

        if self._client is None:

            with self._lock:

                if self._client is None:

                    settings = get_settings()

                    if not settings.supabase_url:
                        raise RuntimeError(
                            "SUPABASE_URL is not configured."
                        )

                    if not settings.supabase_secret_key:
                        raise RuntimeError(
                            "SUPABASE_SECRET_KEY is not configured."
                        )

                    self._client = create_client(
                        settings.supabase_url,
                        settings.supabase_secret_key,
                    )

        return self._client

    @staticmethod
    def _handle_supabase_error(
        exc: Exception,
    ) -> None:

        if isinstance(exc, APIError):

            code = getattr(
                exc,
                "code",
                None,
            )

            if code in ("PGRST205", "42P01"):

                raise RuntimeError(
                    "Supabase table "
                    "'public.project_schedules' was not found. "
                    "Apply backend/sql/schema.sql and "
                    "backend/sql/auth_accounts.sql before "
                    "backend/sql/project_ownership.sql."
                ) from exc

            if code in ("PGRST204", "42703"):

                raise RuntimeError(
                    "Supabase project_schedules schema is incomplete. "
                    "Apply backend/sql/project_ownership.sql after "
                    "backend/sql/auth_accounts.sql and the existing "
                    "project_schedules schema."
                ) from exc

        raise exc

    def create(
        self,
        schedule: ScheduleResponse,
        *,
        owner_user_id: str | None,
    ) -> ScheduleResponse:

        payload = {
            "project_id": schedule.project_id,
            "project_name": schedule.project_name,
            "schedule": schedule.model_dump(
                mode="json"
            ),
            "owner_user_id": owner_user_id,
        }

        try:

            response = (
                self.client
                .table(self.TABLE_NAME)
                .insert(payload)
                .execute()
            )

        except Exception as exc:

            if getattr(exc, "code", None) == "23505":

                raise ProjectScheduleExists(
                    "A schedule with this project ID already exists."
                ) from exc

            self._handle_supabase_error(exc)
            raise

        if not response.data:

            raise RuntimeError(
                "Supabase did not return the created schedule."
            )

        return schedule

    def save(
        self,
        schedule: ScheduleResponse,
    ) -> ScheduleResponse:
        """Trusted service upsert; never change an existing owner."""

        payload = {
            "project_id": schedule.project_id,
            "project_name": schedule.project_name,
            "schedule": schedule.model_dump(
                mode="json"
            ),
        }

        try:

            response = (
                self.client
                .table(self.TABLE_NAME)
                .upsert(
                    payload,
                    on_conflict="project_id",
                )
                .execute()
            )

        except Exception as exc:

            self._handle_supabase_error(exc)
            raise

        if response.data is None:

            raise RuntimeError(
                "Supabase did not return the saved schedule."
            )

        return schedule

    def get(
        self,
        project_id: str,
        *,
        owner_user_id: str | None = None,
    ) -> ScheduleResponse | None:

        try:

            query = (
                self.client
                .table(self.TABLE_NAME)
                .select("schedule")
                .eq(
                    "project_id",
                    project_id,
                )
            )

            if owner_user_id is not None:

                query = query.eq(
                    "owner_user_id",
                    owner_user_id,
                )

            response = query.limit(1).execute()

        except Exception as exc:

            self._handle_supabase_error(exc)
            raise

        if not response.data:
            return None

        raw_schedule = response.data[0].get(
            "schedule"
        )

        if not raw_schedule:
            return None

        return ScheduleResponse.model_validate(
            raw_schedule
        )

    def get_all(
        self,
        *,
        owner_user_id: str | None = None,
    ) -> list[ScheduleResponse]:

        try:

            query = (
                self.client
                .table(self.TABLE_NAME)
                .select("schedule")
            )

            if owner_user_id is not None:

                query = query.eq(
                    "owner_user_id",
                    owner_user_id,
                )

            response = (
                query
                .order(
                    "created_at",
                    desc=True,
                )
                .execute()
            )

        except Exception as exc:

            self._handle_supabase_error(exc)
            raise

        results: list[
            ScheduleResponse
        ] = []

        for row in response.data or []:

            raw_schedule = row.get(
                "schedule"
            )

            if raw_schedule:

                results.append(
                    ScheduleResponse.model_validate(
                        raw_schedule
                    )
                )

        return results

    def update(
        self,
        project_id: str,
        schedule: ScheduleResponse,
        *,
        owner_user_id: str | None = None,
    ) -> ScheduleResponse | None:

        if schedule.project_id != project_id:

            raise ValueError(
                "Schedule project ID does not match "
                "the requested project ID."
            )

        payload = {
            "project_name": schedule.project_name,
            "schedule": schedule.model_dump(
                mode="json"
            ),
        }

        try:

            query = (
                self.client
                .table(self.TABLE_NAME)
                .update(payload)
                .eq(
                    "project_id",
                    project_id,
                )
            )

            if owner_user_id is not None:

                query = query.eq(
                    "owner_user_id",
                    owner_user_id,
                )

            response = query.execute()

        except Exception as exc:

            self._handle_supabase_error(exc)
            raise

        if not response.data:
            return None

        return schedule

    def delete(
        self,
        project_id: str,
        *,
        owner_user_id: str | None = None,
    ) -> bool:

        try:

            query = (
                self.client
                .table(self.TABLE_NAME)
                .delete()
                .eq(
                    "project_id",
                    project_id,
                )
            )

            if owner_user_id is not None:

                query = query.eq(
                    "owner_user_id",
                    owner_user_id,
                )

            response = query.execute()

        except Exception as exc:

            self._handle_supabase_error(exc)
            raise

        return bool(response.data)

    def assign_owner(
        self,
        project_id: str,
        owner_user_id: str,
    ) -> bool:
        """Assign ownership only on behalf of a verified admin caller."""

        try:

            response = (
                self.client
                .table(self.TABLE_NAME)
                .update({
                    "owner_user_id": owner_user_id,
                })
                .eq(
                    "project_id",
                    project_id,
                )
                .execute()
            )

        except Exception as exc:

            self._handle_supabase_error(exc)
            raise

        return bool(response.data)

    def exists(
        self,
        project_id: str,
        *,
        owner_user_id: str | None = None,
    ) -> bool:

        return self.get(
            project_id,
            owner_user_id=owner_user_id,
        ) is not None