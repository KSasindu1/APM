
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


class RiskAgentClient:
    """HTTP client for the Risk & Knowledge Agent."""

    def __init__(self) -> None:
        self.base_url = os.getenv(
            "RISK_AGENT_URL",
            "http://localhost:8001",
        ).rstrip("/")

        self.timeout = float(
            os.getenv("RISK_AGENT_TIMEOUT", "60")
        )

    def send_plan(self, payload: dict) -> dict:
        """Send a project plan for risk assessment."""

        url = f"{self.base_url}/api/risk/agent/plan"

        try:
            response = httpx.post(
                url,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()

            result = response.json()

            if not isinstance(result, dict):
                raise RuntimeError(
                    "Risk Agent returned an invalid JSON object."
                )

            logger.info(
                "Risk Agent responded for project %s",
                payload.get("project_id"),
            )

            return result

        except httpx.HTTPStatusError as exc:
            logger.error(
                "Risk Agent HTTP error %s: %s",
                exc.response.status_code,
                exc.response.text[:1000],
            )
            raise RuntimeError(
                f"Risk Agent returned HTTP "
                f"{exc.response.status_code}."
            ) from exc

        except httpx.RequestError as exc:
            logger.error(
                "Unable to connect to Risk Agent: %s",
                exc,
            )
            raise RuntimeError(
                "Risk Agent is unreachable. Check "
                "RISK_AGENT_URL and the service status."
            ) from exc

        except ValueError as exc:
            logger.error(
                "Risk Agent returned invalid JSON: %s",
                exc,
            )
            raise RuntimeError(
                "Risk Agent returned invalid JSON."
            ) from exc