from __future__ import annotations

import threading

from postgrest.exceptions import APIError
from supabase import Client, create_client

from app.core.config import get_settings
from app.model.planning import ScheduleResponse


class PlanningRepository:
    """
    Supabase-backed schedule repository.

    The complete ScheduleResponse is stored inside
    the JSONB `schedule` column.
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

            if code == "PGRST205":

                raise RuntimeError(
                    "Supabase table "
                    "'public.project_schedules' was not found. "
                    "Run supabase.sql in the Supabase SQL Editor."
                ) from exc

        raise exc

    def save(
        self,
        schedule: ScheduleResponse,
    ) -> ScheduleResponse:

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
    ) -> ScheduleResponse | None:

        try:

            response = (
                self.client
                .table(self.TABLE_NAME)
                .select("schedule")
                .eq(
                    "project_id",
                    project_id,
                )
                .limit(1)
                .execute()
            )

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
    ) -> list[ScheduleResponse]:

        try:

            response = (
                self.client
                .table(self.TABLE_NAME)
                .select("schedule")
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
    ) -> ScheduleResponse | None:

        if schedule.project_id != project_id:

            raise ValueError(
                "Schedule project ID does not match "
                "the requested project ID."
            )

        existing = self.get(
            project_id
        )

        if existing is None:
            return None

        self.save(schedule)

        return schedule

    def delete(
        self,
        project_id: str,
    ) -> bool:

        existing = self.get(
            project_id
        )

        if existing is None:
            return False

        try:

            response = (
                self.client
                .table(self.TABLE_NAME)
                .delete()
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
    ) -> bool:

        return self.get(
            project_id
        ) is not None