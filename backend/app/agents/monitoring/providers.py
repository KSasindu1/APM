"""Real agent API and message adapters. No example projects or fallback data."""

import hashlib
import json
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import date
from typing import Literal, overload
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from .schemas import ActualProjectSnapshot, Identifier, PlannedTask, PlanningSnapshot, ProjectRisk, RiskSnapshot


class PlanningAgentAPIError(ValueError):
    pass


class RiskAgentAPIError(ValueError):
    pass


class RiskAgentAvailabilityError(RiskAgentAPIError):
    """Transport, timeout, rate-limit, or server availability failure."""


class RiskAgentIntegrityError(RiskAgentAPIError):
    """Invalid response, project binding, or non-availability HTTP failure."""


_CREDENTIAL_FIELDS = {
    "principal", "credentials", "credential", "authorization", "authentication", "auth",
    "password", "passwd", "pwd", "secret", "secrets", "token", "tokens", "key", "keys",
    "bearer", "cookie", "cookies", "set_cookie", "auth_header", "authorization_header",
    "api_key", "api_keys", "access_key", "access_key_id", "private_key", "secret_key",
    "service_key", "client_secret", "access_token", "refresh_token", "id_token",
}
_CREDENTIAL_SUFFIXES = tuple("_" + name for name in _CREDENTIAL_FIELDS)
_COMPACT_CREDENTIAL_FIELDS = {name.replace("_", "") for name in _CREDENTIAL_FIELDS}


def _credential_field(key: str) -> bool:
    normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized)
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")
    return (normalized.replace("_", "") in _COMPACT_CREDENTIAL_FIELDS
            or normalized.endswith(_CREDENTIAL_SUFFIXES))


@overload
def sanitize_risk_context(value: str) -> str: ...


@overload
def sanitize_risk_context(value: JsonValue) -> JsonValue: ...


def sanitize_risk_context(value: JsonValue) -> JsonValue:
    """Remove credential fields without mutating source evidence or plain prose."""
    if isinstance(value, dict):
        return {key: sanitize_risk_context(item) for key, item in value.items()
                if not _credential_field(key)}
    if isinstance(value, list):
        return [sanitize_risk_context(item) for item in value]
    if isinstance(value, str) and value.lstrip().startswith(("{", "[", '"')):
        try:
            decoded = json.loads(value)
        except ValueError:
            return value
        # Transport envelopes and configured descriptions can contain JSON inside JSON.
        safe = sanitize_risk_context(decoded)
        if safe != decoded:
            return json.dumps(safe, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return value


class _ExternalModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class _ScheduledTask(_ExternalModel):
    task_id: Identifier
    name: str = Field(min_length=1, max_length=300)
    planned_start: date
    planned_end: date
    priority: Literal["low", "medium", "high", "critical"]
    dependencies: list[Identifier]
    status: Literal["PLANNED", "IN_PROGRESS", "COMPLETED", "BLOCKED"]
    is_critical: bool = Field(default=False, strict=True)

    @field_validator("priority", mode="before")
    @classmethod
    def normalize_priority(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class _ScheduleResponse(_ExternalModel):
    project_id: Identifier
    project_name: str = Field(min_length=1)
    schedule: list[_ScheduledTask] = Field(max_length=10000)
    analysis: dict[str, JsonValue]
    responsible_ai: dict[str, JsonValue] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    updated_on: date | None = None


class _PlanningMessage(_ExternalModel):
    sender: Literal["planning_agent"]
    receiver: Literal["project_monitor_report_agent"]
    action: Literal["SCHEDULE_CREATED"]
    request_id: Identifier
    status: Literal["success"] = "success"
    data: dict[str, JsonValue]


def adapt_planning_schedule(payload: dict, fetched_on: date) -> PlanningSnapshot:
    """Map a real baseline without turning source status/utilization into actual progress."""
    try:
        supplied = _ScheduleResponse.model_validate(payload)
        limitations = []
        if supplied.updated_on is None:
            limitations.append("Planning API supplies no source update date; baseline freshness is unknown")
        limitations.append("Planning API supplies no milestone definitions; milestone coverage is unavailable")
        limitations.append("Expected progress is a linear calendar-day estimate, not a supplied Planning target")
        return PlanningSnapshot(
            project_id=supplied.project_id, updated_on=supplied.updated_on, fetched_on=fetched_on,
            tasks=[PlannedTask(
                task_id=task.task_id, title=task.name,
                start_date=task.planned_start, due_date=task.planned_end,
                priority=task.priority, dependency_ids=task.dependencies,
                **({"is_critical": task.is_critical} if "is_critical" in task.model_fields_set else {}),
            ) for task in supplied.schedule],
            context=deepcopy(payload), limitations=limitations,
        )
    except (ValidationError, ValueError) as exc:
        raise PlanningAgentAPIError("Planning Agent returned an invalid schedule schema") from exc


def _timeout(value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 300:
        raise ValueError("timeout_seconds must be between 0 and 300 seconds")


async def _api_request(client: httpx.AsyncClient, method: str, path: str, *,
                       timeout: float, agent: str, error_type: type[ValueError],
                       payload: dict | None = None,
                       availability_error_type: type[ValueError] | None = None) -> dict:
    try:
        response = await client.request(method, path, json=payload, timeout=timeout,
                                        follow_redirects=False)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        failure_type = (availability_error_type if availability_error_type is not None
                        and (status in (408, 429) or 500 <= status < 600) else error_type)
        raise failure_type(f"{agent} Agent API returned HTTP {status}") from exc
    except httpx.RequestError as exc:
        failure_type = (availability_error_type if availability_error_type is not None
                        and isinstance(exc, (httpx.NetworkError, httpx.TimeoutException,
                                             httpx.RemoteProtocolError, httpx.ProxyError)) else error_type)
        raise failure_type(f"{agent} Agent API request failed or timed out") from exc
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data
    except ValueError as exc:
        raise error_type(f"{agent} Agent API returned invalid JSON or an invalid response schema") from exc


class PlanningAgentAPIProvider:
    """Read an existing schedule; never create or mutate Planning's data.

    The caller owns the HTTP client and supplies a trusted server-root base_url
    and X-API-Key header if Planning authentication is enabled.
    """

    def __init__(self, client: httpx.AsyncClient, *, timeout_seconds: float = 30,
                 today: Callable[[], date] = date.today):
        _timeout(timeout_seconds)
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.today = today

    async def get_plan(self, project_id: str) -> PlanningSnapshot:
        fetched_on = self.today()
        payload = await _api_request(
            self.client, "GET", f"/api/planning/schedule/{quote(project_id, safe='')}",
            timeout=self.timeout_seconds, agent="Planning", error_type=PlanningAgentAPIError,
        )
        plan = adapt_planning_schedule(payload, fetched_on)
        if plan.project_id != project_id:
            raise PlanningAgentAPIError("Planning Agent response project_id does not match the requested project")
        return plan


class PlanningMessageProvider:
    """Use the real SCHEDULE_CREATED message supplied by a trusted agent caller."""

    def __init__(self, payload: dict, *, received_on: date):
        self.payload = deepcopy(payload)
        self.received_on = received_on

    async def get_plan(self, project_id: str) -> PlanningSnapshot:
        try:
            message = _PlanningMessage.model_validate(self.payload)
        except ValidationError as exc:
            raise PlanningAgentAPIError("Invalid SCHEDULE_CREATED Planning message") from exc
        plan = adapt_planning_schedule(message.data, self.received_on)
        if plan.project_id != project_id:
            raise PlanningAgentAPIError("Planning message project_id does not match the requested project")
        plan.context["message_source"] = {
            "request_id": self.payload["request_id"], "sender": self.payload["sender"],
            "received_on": self.received_on.isoformat(), "message": deepcopy(self.payload),
        }
        return plan


def adapt_actual_progress(payload: dict, observed_on: date) -> ActualProjectSnapshot:
    """Bind real task observations to a project and an explicit observation date.

    No progress percentage is inferred from a status. The caller wraps the PDF's
    task update(s) in {project_id, tasks: [...]}; missing update dates use this
    observation's recording date, not a fabricated source-modification time.
    The original payload and date bases remain in context so coverage can leave
    actual_updated_on unknown when only a recording/observation date is supplied.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
        raise ValueError("Actual progress requires a project_id and tasks list")
    if not payload.get("project_id"):
        raise ValueError("Actual progress requires an explicit project_id")
    statuses = {
        "NOT_STARTED": "not_started", "PLANNED": "not_started",
        "IN_PROGRESS": "in_progress", "BLOCKED": "blocked", "COMPLETED": "completed",
    }
    updated_on = payload.get("updated_on") or observed_on
    tasks = []
    for item in payload["tasks"]:
        if not isinstance(item, dict):
            raise ValueError("Actual task observations must be JSON objects")
        status = item.get("status")
        status = status.strip().upper().replace(" ", "_") if isinstance(status, str) else status
        if not isinstance(status, str) or status not in statuses or "progress_percentage" not in item:
            raise ValueError("Actual tasks require a known status and explicit progress_percentage")
        tasks.append({
            "task_id": item.get("task_id"), "status": statuses[status],
            "progress": item["progress_percentage"], "updated_on": item.get("updated_on") or observed_on,
            "actual_start": item.get("actual_start"), "completed_on": item.get("actual_end"),
        })
    snapshot = ActualProjectSnapshot.model_validate({
        "project_id": payload["project_id"], "updated_on": updated_on,
        "observed_on": observed_on, "tasks": tasks,
        "context": {
            "source_payload": deepcopy(payload),
            "snapshot_date_basis": "source_update" if payload.get("updated_on") else "observation",
            "task_date_basis": {
                task["task_id"]: "source_update" if source.get("updated_on") else "observation"
                for task, source in zip(tasks, payload["tasks"])
            },
        },
    })
    return snapshot


class ActualProgressProvider:
    """Read caller-supplied real observations until the team provides an actual-data API."""

    def __init__(self, payload: dict, *, observed_on: date):
        self.payload = deepcopy(payload)
        self.observed_on = observed_on

    async def get_actual(self, project_id: str) -> ActualProjectSnapshot:
        snapshot = adapt_actual_progress(self.payload, self.observed_on)
        if snapshot.project_id != project_id:
            raise ValueError("Actual progress project_id does not match the requested project")
        return snapshot


class _APIRisk(_ExternalModel):
    risk_id: Identifier | None = None
    task_id: Identifier | None = None
    impact_days: int | None = Field(default=None, ge=0)
    risk: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=2000)
    risk_level: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidence: str | list[str]
    source: str
    affected_area: str
    recommended_action: str

    @field_validator("risk_level", mode="before")
    @classmethod
    def normalize_level(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class _RiskResponse(_ExternalModel):
    project_id: Identifier
    risks: list[_APIRisk] = Field(max_length=1000)
    summary: str
    evidence_used: list[JsonValue]
    knowledge_sources: list[JsonValue]


class RiskAgentAPIProvider:
    """Call Risk's analysis API independently of its internals and model files."""

    def __init__(self, client: httpx.AsyncClient, *, project_information: str = "",
                 top_k: int = 5, timeout_seconds: float = 30,
                 today: Callable[[], date] = date.today):
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20:
            raise ValueError("top_k must be an integer between 1 and 20")
        _timeout(timeout_seconds)
        self.client = client
        self.project_information = project_information
        self.top_k = top_k
        self.timeout_seconds = timeout_seconds
        self.today = today

    async def get_risks(self, project_id: str) -> RiskSnapshot:
        return await self._get_risks(project_id, self.project_information)

    async def get_risks_for_monitoring(self, project_id: str,
                                       project_information: str) -> RiskSnapshot:
        """Retain configured context alongside Monitoring's validated JSON observations."""
        try:
            context = json.loads(project_information)
            if not isinstance(context, dict) or context.get("project_id") != project_id:
                raise ValueError("Context must match the requested project")
        except (TypeError, ValueError) as exc:
            raise RiskAgentIntegrityError("Monitoring Risk context must be a project-bound JSON object") from exc
        if self.project_information:
            context["configured_project_information"] = self.project_information
        information = json.dumps(context, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return await self._get_risks(project_id, information)

    async def _get_risks(self, project_id: str, project_information: str) -> RiskSnapshot:
        observed_on = self.today()
        project_information = sanitize_risk_context(project_information)
        data = await _api_request(
            self.client, "POST", "/api/risk/analyse", timeout=self.timeout_seconds,
            agent="Risk", error_type=RiskAgentIntegrityError,
            availability_error_type=RiskAgentAvailabilityError, payload={
                "project_id": project_id, "source_agent": "Monitoring Agent",
                "query": "Identify current project risks relevant to project monitoring.",
                "project_information": project_information, "top_k": self.top_k,
            },
        )
        try:
            payload = _RiskResponse.model_validate(data)
        except ValidationError as exc:
            raise RiskAgentIntegrityError("Risk Agent API returned invalid JSON or an invalid response schema") from exc
        if payload.project_id != project_id:
            raise RiskAgentIntegrityError("Risk Agent API response project_id does not match the requested project")
        risks = []
        for item in payload.risks:
            identity = json.dumps([item.risk, item.description, item.source, item.affected_area],
                                  ensure_ascii=False, separators=(",", ":"))
            risk_id = item.risk_id or "risk-api-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
            risks.append(ProjectRisk(
                risk_id=risk_id, risk=item.risk, description=item.description,
                level=item.risk_level, confidence=item.confidence, evidence=item.evidence,
                source=item.source, affected_area=item.affected_area,
                recommended_action=item.recommended_action, impact_days=item.impact_days,
                task_ids=[item.task_id] if item.task_id else [],
            ))
        try:
            return RiskSnapshot(
                project_id=payload.project_id, updated_on=observed_on, observed_on=observed_on, risks=risks,
                summary=payload.summary, evidence_used=payload.evidence_used,
                knowledge_sources=payload.knowledge_sources,
            )
        except ValidationError as exc:
            raise RiskAgentIntegrityError("Risk Agent API returned an invalid risk snapshot") from exc
