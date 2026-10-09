"""Backend-only, authorized Monitoring report persistence via Supabase REST."""

import argparse
import asyncio
from datetime import datetime
import os
from pathlib import Path
import re

import httpx
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from .schemas import MonitoringPrincipal, MonitoringReport
from .agent import require_access


class SupabaseMonitoringConfigurationError(ValueError):
    pass


class SupabaseMonitoringError(RuntimeError):
    """Safe-to-display database error; excludes credentials and response bodies."""


class SupabaseMonitoringSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    secret_key: SecretStr = Field(repr=False)
    reports_table: str = "monitoring_reports"
    timeout_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)

    def model_post_init(self, context):
        url = httpx.URL(self.url)
        if (url.scheme != "https" or not url.host or url.userinfo
                or url.path not in ("", "/") or url.query or url.fragment):
            raise ValueError("Supabase URL must be an HTTPS server root without credentials")
        if not self.secret_key.get_secret_value().strip():
            raise ValueError("A backend Supabase secret key is required")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.reports_table):
            raise ValueError("Supabase report table must be a simple public-schema table name")
        self.url = self.url.rstrip("/")

    @classmethod
    def from_env(cls, env_file: str | Path | None = None):
        """Read an explicitly selected local .env; process environment takes precedence."""
        values = dict(dotenv_values(env_file, interpolate=False)) if env_file is not None else {}
        values.update(os.environ)
        try:
            return cls(
                url=values.get("SUPABASE_URL") or "",
                secret_key=SecretStr(values.get("SUPABASE_SECRET_KEY") or ""),
                reports_table=values.get("SUPABASE_MONITORING_REPORTS_TABLE") or "monitoring_reports",
            )
        except (ValidationError, ValueError, httpx.InvalidURL):
            # Pydantic errors can include input values; never display environment inputs.
            raise SupabaseMonitoringConfigurationError(
                "Configure a valid SUPABASE_URL, nonempty SUPABASE_SECRET_KEY, "
                "and SUPABASE_MONITORING_REPORTS_TABLE in the backend environment"
            ) from None


class _StoredReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    project_id: str
    request_id: str
    generated_at: datetime
    report: MonitoringReport


class SupabaseMonitoringReportRepository:
    """Caller owns the dedicated HTTP client and provides a verified principal.

    Privileged Supabase credentials bypass RLS, so every report operation checks
    project authorization here before making a network request.
    """

    def __init__(self, client: httpx.AsyncClient, settings: SupabaseMonitoringSettings):
        self.client = client
        self.settings = settings
        self._url = f"{settings.url}/rest/v1/{settings.reports_table}"

    async def _request(self, method: str, *, params: dict | None = None,
                       row: dict | None = None) -> httpx.Response:
        key = self.settings.secret_key.get_secret_value()
        headers = {"apikey": key, "Accept-Profile": "public", "Content-Profile": "public"}
        # New sb_secret_ keys are not JWTs. Supabase uses their apikey header;
        # legacy service_role JWTs additionally use the Bearer authorization header.
        if not key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {key}"
        if method == "POST":
            headers["Prefer"] = "return=minimal"
        try:
            response = await self.client.request(
                method, self._url, params=params, json=row, headers=headers,
                timeout=self.settings.timeout_seconds, follow_redirects=False,
            )
        except httpx.RequestError:
            raise SupabaseMonitoringError("Supabase report request failed or timed out") from None
        if not 200 <= response.status_code < 300:
            hint = {
                401: "Check the backend Supabase credential.",
                403: "Check database access and the backend credential.",
                404: "Check that the reports table exists and is exposed in the public schema.",
                409: "A report with this ID may already exist.",
            }.get(response.status_code, "Check the Supabase project and reports table configuration.")
            raise SupabaseMonitoringError(f"Supabase returned HTTP {response.status_code}. {hint}")
        return response

    @staticmethod
    def _json_rows(response: httpx.Response) -> list:
        try:
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("Expected a row list")
            return rows
        except ValueError:
            raise SupabaseMonitoringError("Supabase returned invalid report JSON") from None

    @staticmethod
    def _validate_row(row: dict, project_id: str, report_id: str | None = None) -> MonitoringReport:
        try:
            stored = _StoredReport.model_validate(row)
        except ValidationError:
            raise SupabaseMonitoringError("Supabase returned an invalid stored monitoring report") from None
        report = stored.report
        if (stored.project_id != project_id or report.project_id != project_id
                or stored.report_id != report.report_id
                or stored.request_id != report.request_id
                or stored.generated_at != report.generated_at
                or (report_id is not None and stored.report_id != report_id)):
            raise SupabaseMonitoringError("Stored monitoring report metadata does not match the requested project/report")
        return report

    async def save(self, report: MonitoringReport, principal: MonitoringPrincipal) -> None:
        require_access(principal, report.project_id, "monitoring:generate")
        if report.requested_by != principal.subject:
            raise PermissionError("Report author must match the verified monitoring principal")
        payload = report.model_dump(mode="json")
        await self._request("POST", row={
            "report_id": report.report_id,
            "project_id": report.project_id,
            "request_id": report.request_id,
            "generated_at": payload["generated_at"],
            "report": payload,
        })

    async def get(self, project_id: str, report_id: str,
                  principal: MonitoringPrincipal) -> MonitoringReport | None:
        require_access(principal, project_id, "monitoring:read")
        response = await self._request("GET", params={
            "select": "report_id,project_id,request_id,generated_at,report",
            "project_id": f"eq.{project_id}", "report_id": f"eq.{report_id}", "limit": "1",
        })
        rows = self._json_rows(response)
        if len(rows) > 1:
            raise SupabaseMonitoringError("Supabase returned multiple rows for one report")
        return self._validate_row(rows[0], project_id, report_id) if rows else None

    async def list(self, project_id: str, principal: MonitoringPrincipal,
                   limit: int = 20) -> list[MonitoringReport]:
        require_access(principal, project_id, "monitoring:read")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Report limit must be an integer between 1 and 100")
        response = await self._request("GET", params={
            "select": "report_id,project_id,request_id,generated_at,report",
            "project_id": f"eq.{project_id}", "order": "generated_at.desc,report_id.desc",
            "limit": str(limit),
        })
        rows = self._json_rows(response)
        if len(rows) > limit:
            raise SupabaseMonitoringError("Supabase returned more reports than requested")
        return [self._validate_row(row, project_id) for row in rows]

    async def check_connection(self) -> None:
        """Backend operator diagnostic only: read at most one ID, without changing data."""
        response = await self._request("GET", params={"select": "report_id", "limit": "1"})
        self._json_rows(response)


async def _check(env_file: str) -> None:
    settings = SupabaseMonitoringSettings.from_env(env_file)
    async with httpx.AsyncClient() as client:
        repository = SupabaseMonitoringReportRepository(client, settings)
        await repository.check_connection()
    print(f"Supabase connection check passed: public.{settings.reports_table} is readable.")
    print("No rows were inserted, updated, or deleted. This does not verify write permissions or RLS settings.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Supabase Monitoring connection check")
    parser.add_argument("--env-file", default=".env", help="Path to the backend-only .env file")
    args = parser.parse_args()
    try:
        asyncio.run(_check(args.env_file))
    except (SupabaseMonitoringConfigurationError, SupabaseMonitoringError) as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
