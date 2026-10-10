
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings


logger = logging.getLogger(__name__)


class RiskAgentClient:
    """HTTP client for the Risk & Knowledge Agent."""

    def __init__(self) -> None:
        settings = get_settings()

        self.base_url = settings.risk_agent_url.rstrip("/")
        self.timeout = settings.risk_agent_timeout

        self.plan_url = (
            f"{self.base_url}/api/risk/agent/plan"
        )

    async def send_plan(
        self,
        schedule: Any,
    ) -> dict[str, Any]:
        """
        Send a saved schedule to the Risk Agent and
        return its risk analysis response.

        No shared API key is required.
        """

        if hasattr(schedule, "model_dump"):
            payload = schedule.model_dump(mode="json")
        elif isinstance(schedule, dict):
            payload = schedule
        else:
            raise ValueError(
                "Schedule must be a dictionary or "
                "a Pydantic model."
            )

        project_id = payload.get("project_id")

        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError(
                "Schedule must contain a valid project_id."
            )

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout
            ) as client:
                response = await client.post(
                    self.plan_url,
                    json=payload,
                )

                response.raise_for_status()

                result = response.json()

                if not isinstance(result, dict):
                    raise RuntimeError(
                        "Risk Agent returned an unexpected "
                        "response format."
                    )

                return result

        except httpx.TimeoutException as exc:
            logger.warning(
                "Risk Agent timed out for project %s.",
                project_id,
            )
            raise RuntimeError(
                "Risk Agent request timed out."
            ) from exc

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Risk Agent returned HTTP %s for project %s.",
                exc.response.status_code,
                project_id,
            )
            raise RuntimeError(
                "Risk Agent returned HTTP "
                f"{exc.response.status_code}."
            ) from exc

        except httpx.RequestError as exc:
            logger.warning(
                "Cannot connect to Risk Agent for project %s: %s",
                project_id,
                exc,
            )
            raise RuntimeError(
                "Risk Agent is unavailable. Check its URL "
                "and whether the service is running."
            ) from exc

        except ValueError:
            raise

        except RuntimeError:
            raise

        except Exception as exc:
            logger.exception(
                "Unexpected error calling Risk Agent."
            )
            raise RuntimeError(
                "Unexpected error while requesting risk analysis."
            ) from exc