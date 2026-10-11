"""Shared or standalone Monitoring routes; imports never start other agents or models."""

import asyncio
import json
import math
import os
import re
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Literal, cast
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import Field, JsonValue, SecretStr, StrictBool, ValidationError, model_validator

from .agent import MonitoringAccessDenied, MonitoringAgent, MonitoringTokenAuthenticator, require_access
from .providers import (
    ActualProgressProvider, PlanningAgentAPIProvider, PlanningMessageProvider, RiskAgentAPIProvider,
)
from .schemas import (
    CoordinatorReportMessage, Identifier, MonitoringObservationRepository, MonitoringPrincipal,
    MonitoringReport, MonitoringReportRepository, MonitoringRequest, MonitoringSettings,
    MonitoringSummarizer, ObservationReceipt, ObservationRequest, PlanningProvider, PlanningSnapshot,
    RiskKnowledgeProvider,
)
from .storage import (
    SupabaseMonitoringObservationRepository, SupabaseMonitoringReportRepository, SupabaseMonitoringSettings,
)
from .summarizer import GROQ_BASE_URL, GroqMonitoringSummarizer


_PREFIX = "/api/monitoring"
_DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
_BEARER = re.compile(r"[A-Za-z0-9._~+/-]{32,}=*")
PlanningProviderFactory = Callable[[httpx.AsyncClient], PlanningProvider]
RiskProviderFactory = Callable[[httpx.AsyncClient], RiskKnowledgeProvider]


class MonitoringAPIConfigurationError(ValueError):
    """Configuration error whose text never includes environment values."""


class _ServiceError(HTTPException):
    """Only service-owned safe HTTP errors may cross the source boundary."""


def _server_root(value: str, name: str) -> str:
    try:
        if not isinstance(value, str):
            raise TypeError("Server root must be a string")
        parsed = urlsplit(value)
        url = httpx.URL(value)
        valid = (
            value == value.strip() and not any(ord(c) < 33 for c in value)
            and url.host and not url.userinfo and parsed.netloc
            and parsed.path in ("", "/") and "?" not in value and "#" not in value
            and (url.scheme == "https" or (
                url.scheme == "http" and url.host in ("localhost", "127.0.0.1")
            )) and (url.port is None or 1 <= url.port <= 65535)
        )
        if valid:
            return str(url).rstrip("/")
    except (ValueError, TypeError, httpx.InvalidURL):
        pass
    raise MonitoringAPIConfigurationError(
        f"{name} must be an HTTPS server root or an explicit localhost/127.0.0.1 HTTP root, "
        "without credentials, paths, queries, or fragments"
    ) from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _configured_authenticator(secret: SecretStr | None) -> MonitoringTokenAuthenticator | None:
    if secret is None:
        return None
    try:
        raw = json.loads(secret.get_secret_value(), object_pairs_hook=_unique_object)
        if not isinstance(raw, dict) or not raw:
            raise ValueError("Identity object required")
        identities = {}
        for token, identity in raw.items():
            if not _BEARER.fullmatch(token) or not isinstance(identity, dict):
                raise ValueError("Invalid identity")
            if set(identity) != {"subject", "project_ids", "permissions"}:
                raise ValueError("Invalid identity fields")
            if not isinstance(identity["subject"], str):
                raise TypeError("Invalid subject")
            if identity["subject"] != identity["subject"].strip():
                raise ValueError("Invalid subject")
            for name in ("project_ids", "permissions"):
                values = identity[name]
                if (not isinstance(values, list) or not all(isinstance(v, str) and v == v.strip() for v in values)
                        or len(set(values)) != len(values)):
                    raise ValueError("Invalid identity scope")
            identities[token] = MonitoringPrincipal.model_validate(identity)
        return MonitoringTokenAuthenticator(identities)
    except (ValueError, TypeError, ValidationError):
        # Invalid mappings are unavailable, not permissive defaults. Never surface
        # Pydantic errors here: their input data can contain server secrets.
        return None


@dataclass(frozen=True)
class MonitoringAPISettings:
    auth_identities: SecretStr | None = field(default=None, repr=False)
    planning_base_url: str | None = None
    risk_base_url: str | None = None
    planning_api_key: SecretStr | None = field(default=None, repr=False)
    storage: SupabaseMonitoringSettings | None = field(default=None, repr=False)
    cors_origins: tuple[str, ...] = _DEFAULT_ORIGINS
    provider_timeout_seconds: float = 30
    risk_failure_policy: Literal["fail", "report_unknown"] = "fail"
    groq_api_key: SecretStr | None = field(default=None, repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    llm_enabled: bool = True

    def __post_init__(self):
        for name in ("auth_identities", "planning_api_key", "groq_api_key"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, SecretStr):
                if not isinstance(value, str):
                    raise MonitoringAPIConfigurationError(f"{name} must be a secret string") from None
                object.__setattr__(self, name, SecretStr(value))
        for name in ("planning_base_url", "risk_base_url"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _server_root(value, name))
        if self.planning_api_key is not None:
            key = self.planning_api_key.get_secret_value()
            if not key or any(ord(c) < 32 or ord(c) > 126 for c in key):
                raise MonitoringAPIConfigurationError("Planning API key must be a nonempty HTTP header value") from None
        if self.groq_api_key is not None:
            key = self.groq_api_key.get_secret_value()
            if not key or any(ord(c) < 33 or ord(c) > 126 for c in key):
                raise MonitoringAPIConfigurationError("Groq API key must be a nonempty HTTP header value") from None
        if (not isinstance(self.groq_model, str) or not 1 <= len(self.groq_model) <= 200
                or any(ord(c) < 33 or ord(c) > 126 for c in self.groq_model)):
            raise MonitoringAPIConfigurationError("Monitoring Groq model must be a nonempty model identifier") from None
        if not isinstance(self.llm_enabled, bool):
            raise MonitoringAPIConfigurationError("Monitoring LLM enabled must be a boolean") from None
        timeout = self.provider_timeout_seconds
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 300):
            raise MonitoringAPIConfigurationError("Monitoring provider timeout must be between 0 and 300 seconds") from None
        if self.risk_failure_policy not in ("fail", "report_unknown"):
            raise MonitoringAPIConfigurationError("Monitoring risk failure policy must be fail or report_unknown") from None
        if isinstance(self.cors_origins, str):
            raise MonitoringAPIConfigurationError("Monitoring CORS origins must be an explicit origin sequence") from None
        origins = tuple(_server_root(origin, "Monitoring CORS origin") for origin in self.cors_origins)
        object.__setattr__(self, "cors_origins", tuple(dict.fromkeys(origins)))

    @classmethod
    def from_env(cls, env_file: str | Path | None = None):
        """Read backend .env explicitly; process environment wins; never modify it."""
        path = Path(env_file) if env_file is not None else Path(__file__).resolve().parents[3] / ".env"
        try:
            values = dict(dotenv_values(path, interpolate=False)) if path.is_file() else {}
        except (OSError, UnicodeError):
            raise MonitoringAPIConfigurationError("Monitoring environment file could not be read") from None
        values = {key: value for key, value in values.items() if value is not None}
        values.update(os.environ)
        try:
            timeout = float(values.get("MONITORING_PROVIDER_TIMEOUT_SECONDS") or "30")
        except (ValueError, TypeError):
            raise MonitoringAPIConfigurationError("Monitoring provider timeout must be between 0 and 300 seconds") from None
        storage = None
        # Storage is optional and can be misconfigured without preventing health
        # probes or unsaved reports. Its own settings validate URL/table/key.
        if values.get("SUPABASE_URL") and values.get("SUPABASE_SECRET_KEY"):
            try:
                storage = SupabaseMonitoringSettings(
                    url=values.get("SUPABASE_URL") or "", secret_key=SecretStr(values.get("SUPABASE_SECRET_KEY") or ""),
                    reports_table=values.get("SUPABASE_MONITORING_REPORTS_TABLE") or "monitoring_reports",
                    observations_table=values.get("SUPABASE_MONITORING_OBSERVATIONS_TABLE") or "monitoring_observations",
                    timeout_seconds=timeout,
                )
            except (ValueError, TypeError, httpx.InvalidURL):
                storage = None
        origins = values.get("MONITORING_CORS_ORIGINS")
        enabled = str(values.get("MONITORING_LLM_ENABLED", "true")).lower()
        if enabled not in ("true", "false", "1", "0"):
            raise MonitoringAPIConfigurationError("MONITORING_LLM_ENABLED must be true or false") from None
        return cls(
            auth_identities=SecretStr(values.get("MONITORING_AUTH_IDENTITIES") or "") if values.get("MONITORING_AUTH_IDENTITIES") else None,
            planning_base_url=values.get("MONITORING_PLANNING_BASE_URL") or None,
            risk_base_url=values.get("MONITORING_RISK_BASE_URL") or None,
            planning_api_key=SecretStr(values.get("MONITORING_PLANNING_API_KEY") or "") if values.get("MONITORING_PLANNING_API_KEY") else None,
            storage=storage,
            cors_origins=tuple(part.strip() for part in origins.split(",")) if origins is not None else _DEFAULT_ORIGINS,
            provider_timeout_seconds=timeout,
            risk_failure_policy=cast(Literal["fail", "report_unknown"], values.get("MONITORING_RISK_FAILURE_POLICY") or "fail"),
            groq_api_key=SecretStr(values["GROQ_API_KEY"]) if values.get("GROQ_API_KEY") else None,
            groq_model=values.get("GROQ_MODEL") or "openai/gpt-oss-120b",
            llm_enabled=enabled in ("true", "1"),
        )


class GenerateReportRequest(MonitoringRequest):
    as_of: date = Field(...)
    planning_message: dict[str, JsonValue] | None = None
    received_on: date | None = None
    actual_progress: dict[str, JsonValue] | None = None
    observed_on: date | None = None
    observation_id: Identifier | None = None
    include_risks: StrictBool = True
    save_report: StrictBool = False

    @model_validator(mode="after")
    def paired_observations(self):
        if self.observation_id is not None and (self.actual_progress is not None or self.observed_on is not None):
            raise ValueError("A stored observation cannot be combined with inline actuals or observed_on")
        for payload, observed in ((self.planning_message, self.received_on),
                                  (self.actual_progress, self.observed_on)):
            if (payload is None) != (observed is None):
                raise ValueError("Each supplied observation requires its corresponding date")
            if observed is not None and observed > self.as_of:
                raise ValueError("Supplied observations cannot postdate as_of")
        return self


class _SafeMonitoringRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                if request.method == "POST":
                    chunks, size = [], 0
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > 1_048_576:
                            raise HTTPException(413, "Monitoring request exceeds the 1 MiB limit")
                        chunks.append(chunk)
                    request._body = b"".join(chunks)
                response = await handler(request)
            except HTTPException as exc:
                response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                                        headers=exc.headers)
            except RequestValidationError:
                # ValidationError inputs/context can echo extra identity fields,
                # bearer tokens, or keys; don't return them or their repr.
                response = JSONResponse(status_code=422, content={"detail": "Invalid Monitoring request"})
            response.headers["Cache-Control"] = "no-store"
            return response

        return safe_handler


def _timed_out(exc: BaseException | None) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


@asynccontextmanager
async def _safe_source_errors():
    try:
        yield
    except _ServiceError:
        raise
    except MonitoringAccessDenied:
        raise HTTPException(403, "Insufficient Monitoring project access") from None
    except Exception as exc:
        if _timed_out(exc):
            raise HTTPException(504, "Monitoring source or storage request timed out") from None
        if type(exc) is ValueError and str(exc) == "Actual snapshot contains unknown task IDs":
            raise HTTPException(422, "Actual observations reference tasks outside the Planning baseline") from None
        raise HTTPException(502, "Monitoring source or storage request failed") from None


class _UnavailableSummarizer:
    async def select_facts(self, facts):
        raise RuntimeError("Monitoring LLM is not configured")


class _Runtime:
    def __init__(self, settings, authenticator, planning_factory, risk_factory, repository,
                 observation_repository, client_factory, account_mode=False, summarizer=None):
        self.settings = settings
        self.account_mode = account_mode
        if account_mode:
            self.authenticator = None
        else:
            self.authenticator = (
                authenticator if authenticator is not None else _configured_authenticator(settings.auth_identities)
            )
        self.planning_factory = planning_factory
        self.risk_factory = risk_factory
        self.repository = repository
        self.observation_repository = observation_repository
        self.client_factory = client_factory
        self.summary_provider = summarizer
        options = {"provider_timeout_seconds": settings.provider_timeout_seconds}
        if "risk_failure_policy" in MonitoringSettings.model_fields:
            options["risk_failure_policy"] = settings.risk_failure_policy
        elif settings.risk_failure_policy != "fail":
            raise MonitoringAPIConfigurationError("This Monitoring core does not support degraded Risk reports") from None
        self.agent_settings = MonitoringSettings(**options)

    @property
    def capabilities(self):
        return {
            "auth_configured": self.account_mode or self.authenticator is not None,
            "planning_configured": self.planning_factory is not None or self.settings.planning_base_url is not None,
            "risk_configured": self.risk_factory is not None or self.settings.risk_base_url is not None,
            "storage_configured": self.repository is not None or self.settings.storage is not None,
            "observations_storage_configured": self.observation_repository is not None or self.settings.storage is not None,
            "llm_configured": self.settings.llm_enabled and (
                self.summary_provider is not None or self.settings.groq_api_key is not None),
        }

    async def principal(self, request: Request,
                        authorization: Annotated[str | None, Header()] = None) -> MonitoringPrincipal:
        if self.account_mode:
            from .account_access import MonitoringAccountAccess

            access = MonitoringAccountAccess(authorization, self.settings.provider_timeout_seconds)
            request.state.monitoring_access = access
            project_id = request.path_params.get("project_id")
            if request.method == "POST" and request.url.path == _PREFIX + "/reports":
                try:
                    project_id = GenerateReportRequest.model_validate(await request.json()).project_id
                except (ValueError, TypeError):
                    # Authenticate even malformed requests, but never enumerate schedules.
                    await access._actor()
                    raise HTTPException(422, "Invalid Monitoring request") from None
            return await access.principal(project_id)
        if self.authenticator is None:
            raise HTTPException(503, "Monitoring authentication is not configured")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not _BEARER.fullmatch(token):
            raise HTTPException(401, "Invalid Monitoring bearer token", headers={"WWW-Authenticate": "Bearer"})
        try:
            principal = self.authenticator.authenticate(token)
            if principal is not None:
                return MonitoringPrincipal.model_validate(principal)
        except Exception:
            raise HTTPException(503, "Monitoring authentication is unavailable") from None
        raise HTTPException(401, "Invalid Monitoring bearer token", headers={"WWW-Authenticate": "Bearer"})

    @staticmethod
    def authorize(principal, project_id, permission):
        try:
            require_access(principal, project_id, permission)
        except MonitoringAccessDenied:
            raise HTTPException(403, "Insufficient Monitoring project access") from None

    async def client(self, stack, *, base_url=None, headers=None):
        options = {
            "timeout": self.settings.provider_timeout_seconds,
            "follow_redirects": False, "trust_env": False,
        }
        if base_url is not None:
            options["base_url"] = base_url
        if headers:
            options["headers"] = headers
        return await stack.enter_async_context(self.client_factory(**options))

    async def recheck(self, request, project_id):
        if self.account_mode:
            try:
                await request.state.monitoring_access.recheck(project_id)
            except HTTPException as exc:
                raise _ServiceError(exc.status_code, exc.detail, headers=exc.headers) from None

    async def storage_call(self, operation):
        try:
            return await asyncio.wait_for(operation, self.settings.provider_timeout_seconds)
        except Exception:
            if self.account_mode:
                raise _ServiceError(503, "Monitoring storage is unavailable") from None
            raise

    async def planning(self, stack, request: Request):
        if self.account_mode:
            return request.state.monitoring_access
        if not self.capabilities["planning_configured"]:
            raise _ServiceError(503, "Monitoring live Planning source is not configured")
        headers = {}
        if self.settings.planning_api_key is not None:
            headers["X-API-Key"] = self.settings.planning_api_key.get_secret_value()
        client = await self.client(stack, base_url=self.settings.planning_base_url, headers=headers)
        return (self.planning_factory(client) if self.planning_factory is not None else
                PlanningAgentAPIProvider(client, timeout_seconds=self.settings.provider_timeout_seconds))

    def guard_planning(self, provider, request):
        if not self.account_mode:
            return provider
        runtime = self

        class AccountPlanning:
            async def get_plan(self, project_id):
                await runtime.recheck(request, project_id)
                plan = await provider.get_plan(project_id)
                await runtime.recheck(request, project_id)
                return plan

        return AccountPlanning()

    async def risk(self, stack):
        client = await self.client(stack, base_url=self.settings.risk_base_url)
        return (self.risk_factory(client) if self.risk_factory is not None else
                RiskAgentAPIProvider(client, timeout_seconds=self.settings.provider_timeout_seconds))

    async def summarizer(self, stack, request, project_id):
        if not self.settings.llm_enabled:
            return None
        provider = self.summary_provider
        if provider is None:
            if self.settings.groq_api_key is None:
                return _UnavailableSummarizer()
            client = await self.client(stack, base_url=GROQ_BASE_URL)
            provider = GroqMonitoringSummarizer(
                client, api_key=self.settings.groq_api_key, model=self.settings.groq_model,
                timeout_seconds=self.settings.provider_timeout_seconds,
            )
        runtime = self

        class AuthorizedSummarizer:
            async def select_facts(self, facts):
                # Planning/Risk may have taken time; recheck before external disclosure.
                await runtime.recheck(request, project_id)
                return await provider.select_facts(facts)

        return AuthorizedSummarizer()

    async def reports(self, stack):
        if self.repository is not None:
            return self.repository
        if self.settings.storage is None:
            raise _ServiceError(503, "Monitoring report storage is not configured")
        client = await self.client(stack)
        storage = self.settings.storage.model_copy(update={"timeout_seconds": self.settings.provider_timeout_seconds})
        return SupabaseMonitoringReportRepository(client, storage)

    async def observations(self, stack):
        if self.observation_repository is not None:
            return self.observation_repository
        if self.settings.storage is None:
            raise _ServiceError(503, "Monitoring observation storage is not configured")
        client = await self.client(stack)
        storage = self.settings.storage.model_copy(update={"timeout_seconds": self.settings.provider_timeout_seconds})
        return SupabaseMonitoringObservationRepository(client, storage)

    @staticmethod
    def checked_observation(supplied, project_id, observation_id=None):
        receipt = ObservationReceipt.model_validate(supplied)
        if (receipt.project_id != project_id or not receipt.persisted
                or observation_id is not None and receipt.observation_id != observation_id):
            raise RuntimeError("Stored observation does not match the authorized project/observation")
        return receipt

    async def get_observation(self, stack, project_id, observation_id, principal):
        self.authorize(principal, project_id, "monitoring:read")
        repository = await self.observations(stack)
        supplied = await self.storage_call(repository.get(project_id, observation_id, principal))
        if supplied is None:
            raise _ServiceError(404, "Monitoring observation not found")
        return self.checked_observation(supplied, project_id, observation_id)

    @staticmethod
    def checked_report(supplied, project_id, report_id=None):
        report = MonitoringReport.model_validate(supplied)
        if report.project_id != project_id or (report_id is not None and report.report_id != report_id):
            raise RuntimeError("Stored report does not match the authorized project/report")
        return report

    async def get_report(self, project_id, report_id, principal):
        async with _safe_source_errors(), AsyncExitStack() as stack:
            repository = await self.reports(stack)
            supplied = await self.storage_call(repository.get(project_id, report_id, principal))
            if supplied is None:
                raise _ServiceError(404, "Monitoring report not found")
            return self.checked_report(supplied, project_id, report_id)


def create_router(
    settings: MonitoringAPISettings | None = None, *,
    authenticator: MonitoringTokenAuthenticator | None = None,
    planning_provider_factory: PlanningProviderFactory | None = None,
    risk_provider_factory: RiskProviderFactory | None = None,
    repository: MonitoringReportRepository | None = None,
    observation_repository: MonitoringObservationRepository | None = None,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    summarizer: MonitoringSummarizer | None = None,
    _account_mode: bool = False,
) -> APIRouter:
    """Reusable routes; inject server-verified auth/providers, never body identity."""
    runtime = _Runtime(settings or MonitoringAPISettings(), authenticator, planning_provider_factory,
                       risk_provider_factory, repository, observation_repository, client_factory, _account_mode, summarizer)
    router = APIRouter(prefix=_PREFIX, tags=["Monitoring"], route_class=_SafeMonitoringRoute)
    principal_dependency = Depends(runtime.principal)

    @router.get("/health")
    async def health():
        capabilities = runtime.capabilities
        ready = capabilities["auth_configured"] and capabilities["planning_configured"]
        return {
            "agent": "Monitoring & Reporting Agent",
            "status": "ready" if ready else "configuration_required",
            "capabilities": capabilities,
            "live_sources_verified": False,
        }

    @router.get("/session")
    async def session(request: Request, principal: MonitoringPrincipal = principal_dependency):
        data = {"subject": principal.subject, "project_ids": sorted(principal.project_ids),
                "permissions": sorted(principal.permissions)}
        if runtime.account_mode:
            unsupported = request.state.monitoring_access.unsupported_project_count
            if unsupported:
                data["unsupported_project_count"] = unsupported
        return data

    @router.get("/projects/{project_id}/baseline", response_model=PlanningSnapshot)
    async def baseline(request: Request, project_id: Identifier, principal: MonitoringPrincipal = principal_dependency):
        runtime.authorize(principal, project_id, "monitoring:read")
        async with _safe_source_errors(), AsyncExitStack() as stack:
            provider = runtime.guard_planning(await runtime.planning(stack, request), request)
            plan = PlanningSnapshot.model_validate(await asyncio.wait_for(
                provider.get_plan(project_id), runtime.settings.provider_timeout_seconds,
            ))
            if plan.project_id != project_id:
                raise RuntimeError("Planning response project mismatch")
            await runtime.recheck(request, project_id)
            return plan

    @router.post("/projects/{project_id}/observations", response_model=ObservationReceipt)
    async def intake(request: Request, project_id: Identifier, body: ObservationRequest,
                     principal: MonitoringPrincipal = principal_dependency):
        runtime.authorize(principal, project_id, "monitoring:generate")
        today = date.today()
        planning = None
        try:
            if body.observed_on > today or body.received_on is not None and body.received_on > today:
                raise ValueError("Observations and Planning receipts cannot be future-dated")
            actual = ActualProgressProvider(body.actual_progress.canonical_payload(), observed_on=body.observed_on)
            snapshot = await actual.get_actual(project_id)
            if body.planning_message is not None:
                if body.received_on is None:
                    raise ValueError("Planning receipt date required")
                planning = PlanningMessageProvider(body.planning_message, received_on=body.received_on)
                plan = await planning.get_plan(project_id)
                if plan.updated_on is not None and plan.updated_on > today:
                    raise ValueError("Planning update cannot be future-dated")
        except (ValueError, TypeError):
            raise HTTPException(422, "Invalid Planning message or actual-progress observations") from None
        if body.save_observation and not runtime.capabilities["observations_storage_configured"]:
            raise HTTPException(503, "Monitoring observation storage is not configured")
        async with _safe_source_errors(), AsyncExitStack() as stack:
            planning = planning if planning is not None else await runtime.planning(stack, request)
            planning = runtime.guard_planning(planning, request)
            plan = PlanningSnapshot.model_validate(await asyncio.wait_for(
                planning.get_plan(project_id), runtime.settings.provider_timeout_seconds,
            ))
            MonitoringAgent._validate_snapshots(
                MonitoringRequest(project_id=project_id, request_id=body.request_id, as_of=today),
                plan, snapshot, None,
            )
            limitations = [
                "Source is a caller-claimed label, not an authenticated sender or signature",
                "Submitted execution measurements are caller-supplied, not independently verified",
                "accepted_at is server acceptance time, not an observation or source-update date",
            ]
            if not body.save_observation:
                limitations.append("Validation-only receipt; not durable or available through observation history")
            if "updated_on" not in body.actual_progress.canonical_payload():
                limitations.append("No source snapshot update date supplied; observed_on is the recording date only")
            receipt = ObservationReceipt(
                observation_id=str(uuid4()), request_id=body.request_id, project_id=project_id,
                source=body.source, observed_on=body.observed_on, accepted_at=datetime.now(timezone.utc),
                submitted_by=principal.subject, actual_progress=body.actual_progress.canonical_payload(),
                persisted=body.save_observation, limitations=limitations,
            )
            if body.save_observation:
                repository = await runtime.observations(stack)
                await runtime.recheck(request, project_id)
                await runtime.storage_call(repository.save(receipt, principal))
            await runtime.recheck(request, project_id)
            return receipt

    @router.get("/projects/{project_id}/observations", response_model=list[ObservationReceipt])
    async def list_observations(request: Request, project_id: Identifier, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                                principal: MonitoringPrincipal = principal_dependency):
        runtime.authorize(principal, project_id, "monitoring:read")
        async with _safe_source_errors(), AsyncExitStack() as stack:
            repository = await runtime.observations(stack)
            receipts = await runtime.storage_call(repository.list(project_id, principal, limit))
            if not isinstance(receipts, list) or len(receipts) > limit:
                raise RuntimeError("Invalid stored observation list")
            checked = [runtime.checked_observation(receipt, project_id) for receipt in receipts]
            if len({receipt.observation_id for receipt in checked}) != len(checked):
                raise RuntimeError("Duplicate stored observation IDs")
            await runtime.recheck(request, project_id)
            return checked

    @router.get("/projects/{project_id}/observations/{observation_id}", response_model=ObservationReceipt)
    async def get_observation(request: Request, project_id: Identifier, observation_id: Identifier,
                              principal: MonitoringPrincipal = principal_dependency):
        runtime.authorize(principal, project_id, "monitoring:read")
        async with _safe_source_errors(), AsyncExitStack() as stack:
            receipt = await runtime.get_observation(stack, project_id, observation_id, principal)
            await runtime.recheck(request, project_id)
            return receipt

    @router.post("/reports", response_model=MonitoringReport)
    async def generate(request: Request, body: GenerateReportRequest,
                       principal: MonitoringPrincipal = principal_dependency):
        runtime.authorize(principal, body.project_id, "monitoring:generate")
        if body.observation_id is not None:
            runtime.authorize(principal, body.project_id, "monitoring:read")
        if body.as_of > date.today():
            raise HTTPException(422, "Monitoring report as_of cannot be in the future")
        # Local, untrusted envelopes and observations are checked before any
        # provider factory/client/database is opened. Sender is structural only.
        planning = actual = None
        try:
            if body.planning_message is not None:
                if body.received_on is None:
                    raise ValueError("Planning receipt date required")
                planning = PlanningMessageProvider(body.planning_message, received_on=body.received_on)
                plan = await planning.get_plan(body.project_id)
                if plan.updated_on is not None and plan.updated_on > body.as_of:
                    raise ValueError("Planning update postdates report")
            if body.actual_progress is not None:
                if body.observed_on is None:
                    raise ValueError("Actual observation date required")
                if body.actual_progress.get("project_id") != body.project_id:
                    raise ValueError("Actual project mismatch")
                actual = ActualProgressProvider(body.actual_progress, observed_on=body.observed_on)
                await actual.get_actual(body.project_id)
        except (ValueError, TypeError):
            raise HTTPException(422, "Invalid Planning message or actual-progress observations") from None
        if body.include_risks and not runtime.capabilities["risk_configured"]:
            raise HTTPException(503, "Monitoring Risk source is not configured; explicitly omit risks for partial coverage")
        if body.save_report and not runtime.capabilities["storage_configured"]:
            raise HTTPException(503, "Monitoring report storage is not configured")
        live_planning = planning is None and runtime.planning_factory is None
        live_risk = body.include_risks and runtime.risk_factory is None
        if body.as_of < date.today() and (live_planning or live_risk):
            raise HTTPException(422, "Current live observations cannot be used for a historical as_of")
        agent_request = MonitoringRequest(project_id=body.project_id, request_id=body.request_id,
                                          as_of=body.as_of, query=body.query)
        async with _safe_source_errors(), AsyncExitStack() as stack:
            receipt = None
            if body.observation_id is not None:
                receipt = await runtime.get_observation(stack, body.project_id, body.observation_id, principal)
                await runtime.recheck(request, body.project_id)
                if receipt.observed_on > body.as_of:
                    raise _ServiceError(422, "Stored observation cannot postdate as_of")
                actual = ActualProgressProvider(receipt.actual_progress, observed_on=receipt.observed_on)
                # The receipt validator already checks all genuine source dates
                # against observed_on; acceptance time is deliberately not as_of.
                await actual.get_actual(body.project_id)
            planning = planning if planning is not None else await runtime.planning(stack, request)
            planning = runtime.guard_planning(planning, request)
            risk = await runtime.risk(stack) if body.include_risks else None
            repository = await runtime.reports(stack) if body.save_report else None
            summary_provider = await runtime.summarizer(stack, request, body.project_id)
            agent = MonitoringAgent(planning=planning, actual=actual, risk=risk, summarizer=summary_provider,
                                    repository=repository if receipt is None and not runtime.account_mode else None,
                                    settings=runtime.agent_settings)
            report = runtime.checked_report(await agent.generate(agent_request, principal), body.project_id)
            if receipt is not None:
                # Attach verified receipt provenance before persistence/export,
                # without forwarding the submitter identity to the Risk service.
                report.actual_context["observation_receipt"] = {
                    **receipt.model_dump(mode="json", exclude={"actual_progress", "limitations"}),
                    "source_is_caller_claimed": True, "submitted_by_is_server_verified": True,
                    "accepted_at_basis": "server_acceptance_not_observation_or_source_update",
                }
                report.limitations.extend(item for item in receipt.limitations if item not in report.limitations)
            if repository is not None and (receipt is not None or runtime.account_mode):
                # Shared generation must not let the agent persist before fresh authorization.
                await runtime.recheck(request, body.project_id)
                await runtime.storage_call(repository.save(report, principal))
            await runtime.recheck(request, body.project_id)
            return report

    @router.get("/projects/{project_id}/reports", response_model=list[MonitoringReport])
    async def list_reports(request: Request, project_id: Identifier, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                           principal: MonitoringPrincipal = principal_dependency):
        runtime.authorize(principal, project_id, "monitoring:read")
        async with _safe_source_errors(), AsyncExitStack() as stack:
            repository = await runtime.reports(stack)
            reports = await runtime.storage_call(repository.list(project_id, principal, limit))
            if not isinstance(reports, list) or len(reports) > limit:
                raise RuntimeError("Invalid stored report list")
            checked = [runtime.checked_report(report, project_id) for report in reports]
            await runtime.recheck(request, project_id)
            return checked

    @router.get("/projects/{project_id}/reports/{report_id}", response_model=MonitoringReport)
    async def get_report(request: Request, project_id: Identifier, report_id: Identifier,
                         principal: MonitoringPrincipal = principal_dependency):
        runtime.authorize(principal, project_id, "monitoring:read")
        report = await runtime.get_report(project_id, report_id, principal)
        await runtime.recheck(request, project_id)
        return report

    @router.get("/projects/{project_id}/reports/{report_id}/coordinator-message",
                response_model=CoordinatorReportMessage)
    async def coordinator_message(request: Request, project_id: Identifier, report_id: Identifier,
                                  principal: MonitoringPrincipal = principal_dependency):
        runtime.authorize(principal, project_id, "monitoring:read")
        report = await runtime.get_report(project_id, report_id, principal)
        await runtime.recheck(request, project_id)
        return CoordinatorReportMessage(correlation_id=report.request_id, project_id=project_id, report=report)

    return router


def create_shared_router(
    app: FastAPI, settings: MonitoringAPISettings | None = None, *,
    planning_api_key: str | SecretStr | None = None,
) -> APIRouter:
    """Account-only access, owner-filtered Planning, and the public in-process Risk API.

    planning_api_key is retained for call-site compatibility; browser access does not use it.
    """
    settings = settings if settings is not None else MonitoringAPISettings.from_env()
    internal_root = "http://127.0.0.1"
    settings = replace(
        settings, planning_base_url=internal_root, risk_base_url=internal_root,
        planning_api_key=planning_api_key if planning_api_key is not None else settings.planning_api_key,
    )

    def client_factory(**options):
        # Only the internal Risk origin uses ASGI; Groq and storage stay external.
        if options.get("base_url") == internal_root:
            options["transport"] = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        return httpx.AsyncClient(**options)

    return create_router(settings, client_factory=client_factory, _account_mode=True)


def create_app(
    settings: MonitoringAPISettings | None = None, *, env_file: str | Path | None = None,
    authenticator: MonitoringTokenAuthenticator | None = None,
    planning_provider_factory: PlanningProviderFactory | None = None,
    risk_provider_factory: RiskProviderFactory | None = None,
    repository: MonitoringReportRepository | None = None,
    observation_repository: MonitoringObservationRepository | None = None,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    summarizer: MonitoringSummarizer | None = None,
) -> FastAPI:
    """Create an isolated service, or pass settings/dependencies for offline use."""
    settings = settings if settings is not None else MonitoringAPISettings.from_env(env_file)
    service = FastAPI(title="Monitoring & Reporting Agent", version="1.0")
    service.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
        allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
    )
    service.include_router(create_router(
        settings, authenticator=authenticator, planning_provider_factory=planning_provider_factory,
        risk_provider_factory=risk_provider_factory, repository=repository,
        observation_repository=observation_repository, client_factory=client_factory, summarizer=summarizer,
    ))

    return service


app = create_app()
