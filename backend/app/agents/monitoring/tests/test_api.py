"""Shared and standalone contracts: synthetic identities and strictly offline sources."""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import traceback
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import dotenv

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agents.monitoring.agent import MonitoringAgent, MonitoringTokenAuthenticator
_read_test_dotenv = dotenv.dotenv_values


def _offline_dotenv_values(path, **options):
    # Import-time standalone app configuration must not read operator files.
    if Path(path).name in {".env", ".env.example"}:
        return {}
    return _read_test_dotenv(path, **options)


with patch("dotenv.dotenv_values", _offline_dotenv_values):
    from app.agents.monitoring.api import (
        MonitoringAPIConfigurationError, MonitoringAPISettings, create_app, create_router, create_shared_router,
    )
from app.agents.monitoring.providers import (
    PlanningAgentAPIError, RiskAgentAvailabilityError, adapt_planning_schedule,
)
from app.agents.monitoring.schemas import MonitoringPrincipal, MonitoringReport, RiskSnapshot
from app.agents.monitoring.storage import SupabaseMonitoringSettings


ROOT = "/api/monitoring"
TODAY = date.today()
TOKEN = "offline-scoped-monitoring-token-" + "x" * 40
KEY = "offline-planning-header-not-a-real-key"
DB_KEY = "sb_secret_offline_not_a_real_credential"
HEADERS = {"Authorization": "Bearer " + TOKEN}
PRINCIPAL = MonitoringPrincipal(
    subject="offline-manager", project_ids=frozenset({"P002", "P001"}),
    permissions=frozenset({"monitoring:read", "monitoring:generate"}),
)


@pytest.fixture
def auth():
    return MonitoringTokenAuthenticator({TOKEN: PRINCIPAL})


@pytest.fixture
def schedule():
    return {
        "project_id": "P001", "project_name": "Offline API contract",
        "schedule": [{
            "task_id": "T001", "name": "Integration", "priority": "HIGH", "is_critical": True,
            "planned_start": (TODAY - timedelta(days=10)).isoformat(),
            "planned_end": (TODAY + timedelta(days=2)).isoformat(),
            "dependencies": [], "status": "IN_PROGRESS",
        }],
        "analysis": {"deadline_met": True, "critical_path": ["T001"]},
        "responsible_ai": {"human_review_required": True}, "warnings": [],
    }


@pytest.fixture
def observations():
    return {"project_id": "P001", "tasks": [{
        "task_id": "T001", "status": "IN_PROGRESS", "progress_percentage": 40,
        "actual_start": (TODAY - timedelta(days=9)).isoformat(),
    }]}


def request_body(**changes):
    return {"project_id": "P001", "request_id": "offline-request", "as_of": TODAY.isoformat(),
            "include_risks": False, **changes}


def message(schedule):
    return {"sender": "planning_agent", "receiver": "project_monitor_report_agent",
            "action": "SCHEDULE_CREATED", "request_id": "planning-request", "status": "success",
            "data": deepcopy(schedule)}


class OfflinePlanning:
    def __init__(self, schedule):
        self.plan = adapt_planning_schedule(schedule, TODAY)
        self.calls = []
        self.error: Exception | None = None

    async def get_plan(self, project_id):
        self.calls.append(project_id)
        if self.error is not None:
            raise self.error
        return self.plan.model_copy(deep=True)


class OfflineRisk:
    def __init__(self):
        self.calls = []
        self.error: Exception | None = None
        self.snapshot = RiskSnapshot(project_id="P001", updated_on=TODAY, risks=[])

    async def get_risks_for_monitoring(self, project_id, project_information):
        self.calls.append((project_id, json.loads(project_information)))
        if self.error is not None:
            raise self.error
        return self.snapshot

    async def get_risks(self, project_id):
        raise AssertionError("The updated core should send validated Monitoring context")


class MemoryRepository:
    def __init__(self):
        self.reports = {}
        self.calls = []
        self.error: Exception | None = None

    async def save(self, report, principal):
        self.calls.append(("save", report.project_id, principal.subject))
        if self.error is not None:
            raise self.error
        self.reports[report.report_id] = report.model_copy(deep=True)

    async def get(self, project_id, report_id, principal):
        self.calls.append(("get", project_id, report_id, principal.subject))
        if self.error is not None:
            raise self.error
        return self.reports.get(report_id)

    async def list(self, project_id, principal, limit=20):
        self.calls.append(("list", project_id, principal.subject, limit))
        if self.error is not None:
            raise self.error
        return list(self.reports.values())[:limit]


@pytest.fixture
def harness(auth, schedule):
    planning, risk, repository = OfflinePlanning(schedule), OfflineRisk(), MemoryRepository()
    factories = []

    def plan_factory(client):
        factories.append(("planning", client))
        return planning

    def risk_factory(client):
        factories.append(("risk", client))
        return risk

    service = create_app(
        MonitoringAPISettings(), authenticator=auth, planning_provider_factory=plan_factory,
        risk_provider_factory=risk_factory, repository=repository,
    )
    with TestClient(service) as client:
        yield client, planning, risk, repository, factories


@pytest.fixture
def clean_environment(monkeypatch):
    # Delete names only; don't inspect or display any existing credential values.
    for name in tuple(os.environ):
        if name.startswith(("MONITORING_", "SUPABASE_")):
            monkeypatch.delenv(name)


def identity_json(**changes):
    identity = {"subject": "offline-manager", "project_ids": ["P002", "P001"],
                "permissions": ["monitoring:generate", "monitoring:read"], **changes}
    return json.dumps({TOKEN: identity})


def assert_redacted(response, *secrets):
    for secret in (TOKEN, KEY, DB_KEY, *secrets):
        assert secret not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_public_health_without_auth_or_configuration():
    with TestClient(create_app(MonitoringAPISettings())) as client:
        response = client.get(ROOT + "/health")
        assert response.status_code == 200
        assert response.json() == {
            "agent": "Monitoring & Reporting Agent", "status": "configuration_required",
            "capabilities": {"auth_configured": False, "planning_configured": False,
                             "risk_configured": False, "storage_configured": False,
                             "observations_storage_configured": False, "llm_configured": False},
            "live_sources_verified": False,
        }
        assert_redacted(response)


def test_health_ready_means_configuration_not_live_connectivity(harness):
    client, planning, risk, repository, factories = harness
    planning.error = RuntimeError("offline unavailable")
    response = client.get(ROOT + "/health")
    assert response.json()["status"] == "ready"
    assert response.json()["capabilities"] == {
        **dict.fromkeys(["auth_configured", "planning_configured", "risk_configured", "storage_configured"], True),
        "observations_storage_configured": False, "llm_configured": False,
    }
    assert response.json()["capabilities"]["observations_storage_configured"] is False
    assert response.json()["live_sources_verified"] is False
    assert factories == planning.calls == risk.calls == repository.calls == []


def test_optional_sources_do_not_block_readiness(auth, schedule):
    with TestClient(create_app(MonitoringAPISettings(), authenticator=auth,
                              planning_provider_factory=lambda client: OfflinePlanning(schedule))) as client:
        payload = client.get(ROOT + "/health").json()
        assert payload["status"] == "ready"
        assert payload["capabilities"]["risk_configured"] is False
        assert payload["capabilities"]["storage_configured"] is False


PRIVATE_GETS = [
    "/session", "/projects/P001/baseline", "/projects/P001/reports",
    "/projects/P001/reports/report-1", "/projects/P001/reports/report-1/coordinator-message",
]


@pytest.mark.parametrize("path", PRIVATE_GETS + ["/reports"])
def test_private_routes_fail_closed_without_auth(path):
    with TestClient(create_app(MonitoringAPISettings())) as client:
        response = (client.post(ROOT + path, json=request_body()) if path == "/reports"
                    else client.get(ROOT + path))
        assert response.status_code == 503
        assert "authentication" in response.json()["detail"]
        assert_redacted(response)


@pytest.mark.parametrize("authorization", [None, "Basic " + TOKEN, "Bearer short", "Bearer " + "z" * 64,
                                            "Bearer  " + TOKEN, "Bearer " + TOKEN + " extra"])
def test_invalid_auth_is_401_with_challenge_and_no_sources(harness, authorization):
    client, planning, risk, repository, factories = harness
    headers = {"Authorization": authorization} if authorization is not None else {}
    response = client.post(ROOT + "/reports", headers=headers, json=request_body())
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert factories == planning.calls == risk.calls == repository.calls == []
    assert_redacted(response)


def test_session_is_sorted_verified_scope_only(harness):
    client, _, _, repository, factories = harness
    response = client.get(ROOT + "/session", headers={"Authorization": "bEaReR " + TOKEN})
    assert response.json() == {"subject": "offline-manager", "project_ids": ["P001", "P002"],
                               "permissions": ["monitoring:generate", "monitoring:read"]}
    assert repository.calls == factories == []
    assert_redacted(response)


@pytest.mark.parametrize("permissions,project_ids,path,method", [
    ([], ["P001"], "/projects/P001/baseline", "GET"),
    (["monitoring:generate"], ["P001"], "/projects/P001/baseline", "GET"),
    (["monitoring:generate"], ["P001"], "/projects/P001/reports", "GET"),
    (["monitoring:generate"], ["P001"], "/projects/P001/reports/report-1", "GET"),
    (["monitoring:generate"], ["P001"], "/projects/P001/reports/report-1/coordinator-message", "GET"),
    (["monitoring:read"], ["P001"], "/reports", "POST"),
    (["monitoring:read", "monitoring:generate"], ["P002"], "/reports", "POST"),
    (["monitoring:read"], ["P002"], "/projects/P001/baseline", "GET"),
    (["monitoring:read"], ["P002"], "/projects/P001/reports", "GET"),
    (["monitoring:read"], ["P002"], "/projects/P001/reports/report-1", "GET"),
    (["monitoring:read"], ["P002"], "/projects/P001/reports/report-1/coordinator-message", "GET"),
])
def test_scope_checked_before_provider_factory_or_repository(auth, permissions, project_ids, path, method):
    calls = []
    principal = MonitoringPrincipal(subject="scoped-user", project_ids=frozenset(project_ids),
                                    permissions=frozenset(permissions))
    repository = MemoryRepository()
    service = create_app(
        MonitoringAPISettings(), authenticator=MonitoringTokenAuthenticator({TOKEN: principal}),
        planning_provider_factory=lambda client: pytest.fail("Unauthorized Planning factory reached"),
        risk_provider_factory=lambda client: pytest.fail("Unauthorized Risk factory reached"), repository=repository,
    )
    with TestClient(service) as client:
        response = client.request(method, ROOT + path, headers=HEADERS,
                                  json=request_body(include_risks=True, save_report=True) if method == "POST" else None)
        assert response.status_code == 403
        assert calls == repository.calls == []
        assert_redacted(response)


def test_baseline_returns_canonical_plan_not_inferred_actual(harness):
    client, planning, risk, repository, factories = harness
    response = client.get(ROOT + "/projects/P001/baseline", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == planning.plan.model_dump(mode="json")
    assert "actual_progress" not in response.json()
    assert response.json()["tasks"][0]["expected_progress"] is None
    assert response.json()["tasks"][0]["is_critical"] is True
    assert planning.calls == ["P001"]
    assert risk.calls == repository.calls == []
    assert factories[0][1].is_closed


def test_partial_generation_direct_report_without_invented_measurements_or_autosave(harness):
    client, _, risk, repository, _ = harness
    response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body())
    assert response.status_code == 200
    report = response.json()
    assert report["project_id"] == "P001" and report["request_id"] == "offline-request"
    assert report["requested_by"] == PRINCIPAL.subject
    assert report["metrics"]["actual_progress"] is None
    assert report["tasks"][0]["actual_progress"] is None
    assert report["coverage"]["actual_status"] == "missing"
    assert report["coverage"]["risk_status"] == "not_supplied"
    assert report["status"] == "AT_RISK"
    assert "report" not in report and report["human_review_required"] is True
    assert risk.calls == repository.calls == []
    assert_redacted(response)


def test_generation_passes_validated_real_observations_and_planning_context_to_risk(harness, observations):
    client, _, risk, repository, _ = harness
    response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
        actual_progress=observations, observed_on=TODAY.isoformat(), include_risks=True,
    ))
    assert response.status_code == 200
    report = response.json()
    assert report["metrics"]["actual_progress"] == 40
    assert report["coverage"]["actual_status"] == "complete"
    assert report["coverage"]["actual_updated_on"] is None
    assert report["coverage"]["actual_observed_on"] == TODAY.isoformat()
    assert report["coverage"]["risk_status"] == "available"
    project_id, context = risk.calls[0]
    assert project_id == context["project_id"] == "P001"
    assert context["planned_baseline"]["tasks"][0]["task_id"] == "T001"
    assert context["observed_actual_measurements"]["tasks"][0]["progress"] == 40
    assert context["as_of"] == TODAY.isoformat()
    assert context["source_dates"]["planning_updated_on"] is None
    assert context["source_dates"]["actual_updated_on"] is None
    assert TOKEN not in json.dumps(context)
    assert repository.calls == []


def test_genuine_message_works_without_live_planning_configuration(auth, schedule, observations):
    with TestClient(create_app(MonitoringAPISettings(), authenticator=auth)) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
            planning_message=message(schedule), received_on=TODAY.isoformat(),
            actual_progress=observations, observed_on=TODAY.isoformat(),
        ))
        assert response.status_code == 200
        context = response.json()["planning_context"]
        assert {key: context[key] for key in schedule} == schedule
        assert context["message_source"]["message"] == message(schedule)
        assert context["message_source"]["received_on"] == TODAY.isoformat()
        assert response.json()["metrics"]["actual_progress"] == 40
        assert response.json()["coverage"]["planning_observed_on"] == TODAY.isoformat()


@pytest.mark.parametrize("changes", [
    {"project_id": "../P001"}, {"request_id": "bad/id"}, {"project_id": "*"},
    {"project_id": "P" * 101}, {"request_id": ""}, {"as_of": "not-a-date"},
    {"query": ""}, {"query": "x" * 501}, {"include_risks": "false"}, {"save_report": 1},
    {"principal": {"subject": "attacker", "permissions": ["monitoring:generate"]}},
    {"requested_by": "attacker"}, {"planning_base_url": "http://internal.example.test"},
    {"provider_url": "https://untrusted.example.test"}, {"api_key": KEY},
    {"actual_progress": {}, "observed_on": None},
    {"actual_progress": {}, "observed_on": (TODAY + timedelta(days=1)).isoformat()},
    {"observed_on": TODAY.isoformat()}, {"received_on": TODAY.isoformat()},
    {"planning_message": {}, "received_on": None},
    {"planning_message": {}, "received_on": (TODAY + timedelta(days=1)).isoformat()},
    {"actual_progress": {"project_id": "P002", "tasks": []}, "observed_on": TODAY.isoformat()},
    {"actual_progress": {"project_id": "P001", "tasks": [None]}, "observed_on": TODAY.isoformat()},
    {"actual_progress": {"project_id": "P001", "tasks": [{"task_id": "T001", "status": "COMPLETED",
                         "progress_percentage": 100}]}, "observed_on": TODAY.isoformat()},
    {"actual_progress": {"project_id": "P001", "tasks": [],
                         "updated_on": (TODAY + timedelta(days=1)).isoformat()}, "observed_on": TODAY.isoformat()},
])
def test_invalid_input_rejected_before_any_sources_or_storage(harness, changes):
    client, planning, risk, repository, factories = harness
    response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(**changes))
    assert response.status_code == 422
    assert factories == planning.calls == risk.calls == repository.calls == []
    assert_redacted(response)


@pytest.mark.parametrize("mutation", ["sender", "receiver", "action", "status", "project", "future_update"])
def test_invalid_message_envelope_is_user_error_before_sources(harness, schedule, mutation):
    client, planning, risk, repository, factories = harness
    envelope = message(schedule)
    if mutation == "project":
        envelope["data"]["project_id"] = "P002"
    elif mutation == "future_update":
        envelope["data"]["updated_on"] = (TODAY + timedelta(days=1)).isoformat()
    else:
        envelope[mutation] = "wrong"
    response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
        planning_message=envelope, received_on=TODAY.isoformat(), include_risks=True, save_report=True,
    ))
    assert response.status_code == 422
    assert factories == planning.calls == risk.calls == repository.calls == []


def test_missing_required_as_of_and_malformed_json_are_redacted(harness):
    client, _, _, repository, factories = harness
    body = request_body()
    del body["as_of"]
    assert client.post(ROOT + "/reports", json=body, headers=HEADERS).status_code == 422
    response = client.post(ROOT + "/reports", content='{"secret":"' + TOKEN,
                           headers={**HEADERS, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert_redacted(response)
    assert factories == repository.calls == []


def test_unknown_actual_task_is_422_before_risk_or_save(harness, observations):
    client, planning, risk, repository, _ = harness
    observations["tasks"][0]["task_id"] = "T999"
    response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
        actual_progress=observations, observed_on=TODAY.isoformat(), include_risks=True, save_report=True,
    ))
    assert response.status_code == 422
    assert planning.calls == ["P001"]
    assert risk.calls == repository.calls == []


@pytest.mark.parametrize("operation", ["baseline", "risk", "storage-save", "storage-read"])
def test_absent_source_configuration_is_clear_503_before_live_access(auth, schedule, operation):
    calls = []
    factory = None if operation == "baseline" else lambda client: pytest.fail("Unconfigured source triggered Planning")
    with TestClient(create_app(MonitoringAPISettings(), authenticator=auth, planning_provider_factory=factory)) as client:
        if operation == "baseline":
            response = client.get(ROOT + "/projects/P001/baseline", headers=HEADERS)
        elif operation == "storage-read":
            response = client.get(ROOT + "/projects/P001/reports", headers=HEADERS)
        else:
            response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
                include_risks=operation == "risk", save_report=operation == "storage-save",
            ))
        assert response.status_code == 503
        assert "configured" in response.json()["detail"]
        assert calls == []


def test_save_is_explicit_and_export_only_uses_saved_reports(harness):
    client, _, _, repository, _ = harness
    unsaved = client.post(ROOT + "/reports", headers=HEADERS, json=request_body()).json()
    assert repository.calls == []
    assert client.get(ROOT + "/projects/P001/reports/" + unsaved["report_id"], headers=HEADERS).status_code == 404
    saved_response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(save_report=True))
    assert saved_response.status_code == 200
    saved = saved_response.json()
    assert repository.calls[-1] == ("save", "P001", PRINCIPAL.subject)
    report_path = ROOT + "/projects/P001/reports/" + saved["report_id"]
    assert client.get(report_path, headers=HEADERS).json() == saved
    listed = client.get(ROOT + "/projects/P001/reports?limit=1", headers=HEADERS)
    assert listed.json() == [saved]
    assert repository.calls[-1] == ("list", "P001", PRINCIPAL.subject, 1)
    exported = client.get(report_path + "/coordinator-message", headers=HEADERS)
    assert exported.json() == {
        "message_type": "monitoring.report.ready", "sender": "monitoring_reporting", "recipient": "coordinator",
        "correlation_id": saved["request_id"], "project_id": "P001", "report": saved,
    }
    assert len([call for call in repository.calls if call[0] == "save"]) == 1


def test_storage_failure_prevents_success_and_publication(harness, monkeypatch):
    client, _, _, repository, _ = harness
    captured = []
    original = MonitoringAgent.generate

    async def inspect_agent(agent, request, principal):
        captured.append(agent)
        return await original(agent, request, principal)

    monkeypatch.setattr(MonitoringAgent, "generate", inspect_agent)
    repository.error = RuntimeError("database exception " + DB_KEY)
    response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(save_report=True))
    assert response.status_code == 502
    assert repository.reports == {}
    assert captured[0].coordinator is None
    assert captured[0].summarizer is not None
    assert_redacted(response)


@pytest.mark.parametrize("suffix", ["", "/coordinator-message"])
def test_missing_report_is_404(harness, suffix):
    client, planning, risk, _, factories = harness
    response = client.get(ROOT + "/projects/P001/reports/missing" + suffix, headers=HEADERS)
    assert response.status_code == 404
    assert planning.calls == risk.calls == factories == []


@pytest.mark.parametrize("operation", ["get", "list", "export"])
def test_stored_project_mismatch_is_not_returned(harness, operation):
    client, _, _, _, _ = harness
    report = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(save_report=True)).json()
    path = ROOT + "/projects/P002/reports"
    if operation != "list":
        path += "/" + report["report_id"]
    if operation == "export":
        path += "/coordinator-message"
    response = client.get(path, headers=HEADERS)
    assert response.status_code == 502
    assert "report_id" not in response.json()
    assert_redacted(response)


def test_stored_report_id_mismatch_is_not_returned(harness):
    client, _, _, repository, _ = harness
    report = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(save_report=True)).json()
    repository.reports["different-id"] = repository.reports[report["report_id"]]
    response = client.get(ROOT + "/projects/P001/reports/different-id", headers=HEADERS)
    assert response.status_code == 502


def test_stored_legacy_coverage_none_remains_unknown(harness):
    client, _, _, repository, _ = harness
    report = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(save_report=True)).json()
    payload = repository.reports[report["report_id"]].model_dump(mode="json")
    del payload["coverage"]
    repository.reports[report["report_id"]] = MonitoringReport.model_validate(payload)
    path = ROOT + "/projects/P001/reports/" + report["report_id"]
    assert client.get(path, headers=HEADERS).json()["coverage"] is None
    assert client.get(ROOT + "/projects/P001/reports", headers=HEADERS).json()[0]["coverage"] is None
    assert client.get(path + "/coordinator-message", headers=HEADERS).json()["report"]["coverage"] is None


@pytest.mark.parametrize("limit", ["0", "101", "-1", "bad", "1.5"])
def test_list_limit_is_bound_before_repository(harness, limit):
    client, _, _, repository, factories = harness
    response = client.get(ROOT + "/projects/P001/reports?limit=" + limit, headers=HEADERS)
    assert response.status_code == 422
    assert repository.calls == factories == []


@pytest.mark.parametrize("path", ["/projects/bad%20id/baseline", "/projects/*/reports",
                                  "/projects/P001/reports/bad%20id", "/projects/P001/reports/*/coordinator-message"])
def test_invalid_path_ids_are_rejected_before_sources(harness, path):
    client, planning, risk, repository, factories = harness
    assert client.get(ROOT + path, headers=HEADERS).status_code == 422
    assert factories == planning.calls == risk.calls == repository.calls == []


@pytest.mark.parametrize("failure,status", [
    (PlanningAgentAPIError("source schema " + KEY), 502), (RuntimeError("internal " + DB_KEY), 502),
    (ValueError("upstream " + TOKEN), 502), (TimeoutError("timed out " + KEY), 504),
    (httpx.ReadTimeout("timed out " + KEY), 504),
])
def test_source_errors_are_redacted_and_timeout_is_504(harness, failure, status):
    client, planning, _, repository, factories = harness
    planning.error = failure
    response = client.get(ROOT + "/projects/P001/baseline", headers=HEADERS)
    assert response.status_code == status
    assert_redacted(response)
    response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body())
    assert response.status_code == status
    assert_redacted(response)
    assert repository.calls == []
    assert all(client.is_closed for _, client in factories)


@pytest.mark.parametrize("policy,status", [("fail", 502), ("report_unknown", 200)])
def test_risk_failure_policy_preserves_truthful_coverage(auth, schedule, policy, status):
    planning, risk = OfflinePlanning(schedule), OfflineRisk()
    risk.error = RiskAgentAvailabilityError("unsafe " + KEY)
    with TestClient(create_app(
        MonitoringAPISettings(risk_failure_policy=policy), authenticator=auth,
        planning_provider_factory=lambda client: planning, risk_provider_factory=lambda client: risk,
    )) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(include_risks=True))
        assert response.status_code == status
        assert_redacted(response)
        if policy == "report_unknown":
            assert response.json()["coverage"]["risk_status"] == "unavailable"
            assert response.json()["status"] == "AT_RISK"
            assert any(warning["code"] == "RISK_UNAVAILABLE" for warning in response.json()["warnings"])


def mock_clients(handler):
    clients = []

    def factory(**options):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), **options)
        clients.append(client)
        return client

    return factory, clients


@pytest.fixture
def risk_response():
    return {
        "project_id": "P001", "risks": [{
            "risk_id": "R001", "task_id": "T001", "risk": "Integration dependency",
            "description": "Vendor contract review is pending", "risk_level": "HIGH", "confidence": 0.9,
            "evidence": ["Recorded review note"], "source": "review.pdf", "affected_area": "Integration",
            "recommended_action": "Confirm contract", "impact_days": 2,
        }],
        "summary": "One high integration risk", "evidence_used": ["Recorded review note"],
        "knowledge_sources": ["review.pdf"],
    }


def test_real_adapters_use_isolated_request_owned_clients_and_context(auth, schedule, observations, risk_response):
    sent = []

    def handler(request):
        sent.append(request)
        if request.url.host == "planning.example.test":
            return httpx.Response(200, json=schedule)
        assert request.url.host == "risk.example.test"
        return httpx.Response(200, json=risk_response)

    factory, clients = mock_clients(handler)
    settings = MonitoringAPISettings(
        planning_base_url="https://planning.example.test", risk_base_url="https://risk.example.test",
        planning_api_key=SecretStr(KEY), provider_timeout_seconds=7.5,
    )
    with TestClient(create_app(settings, authenticator=auth, client_factory=factory)) as client:
        for _ in range(2):
            response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
                actual_progress=observations, observed_on=TODAY.isoformat(), include_risks=True,
            ))
            assert response.status_code == 200
            report = response.json()
            assert report["risks"][0]["risk_id"] == "R001"
            assert report["risk_summary"] == risk_response["summary"]
            assert report["risk_evidence_used"] == risk_response["evidence_used"]
            assert report["planning_context"] == schedule
            assert_redacted(response)
    assert len(clients) == 4 and len({id(client) for client in clients}) == 4
    assert all(client.is_closed for client in clients)
    for planning_request, risk_request in (sent[:2], sent[2:]):
        assert planning_request.method == "GET"
        assert planning_request.url.path == "/api/planning/schedule/P001"
        assert planning_request.headers["x-api-key"] == KEY
        assert "authorization" not in planning_request.headers
        assert risk_request.method == "POST" and risk_request.url.path == "/api/risk/analyse"
        assert "x-api-key" not in risk_request.headers and "authorization" not in risk_request.headers
        payload = json.loads(risk_request.content)
        context = json.loads(payload["project_information"])
        assert payload["project_id"] == context["project_id"] == "P001"
        assert context["observed_actual_measurements"]["tasks"][0]["progress"] == 40
        assert payload["source_agent"] == "Monitoring Agent"
        assert payload["top_k"] == 5
        assert TOKEN not in risk_request.content.decode() and KEY not in risk_request.content.decode()
        assert risk_request.extensions["timeout"]["read"] == 7.5


@pytest.mark.parametrize("failure,status", [("redirect", 502), ("timeout", 504), ("bad-json", 502),
                                            ("wrong-project", 502), ("http-error", 502)])
def test_real_planning_errors_redacted_no_redirects(auth, schedule, failure, status):
    sent = []

    def handler(request):
        sent.append(request)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://untrusted.example.test/" + KEY})
        if failure == "timeout":
            raise httpx.ReadTimeout("sensitive " + KEY, request=request)
        if failure == "bad-json":
            return httpx.Response(200, text="invalid " + KEY)
        if failure == "http-error":
            return httpx.Response(500, text="exception " + KEY)
        return httpx.Response(200, json={**schedule, "project_id": "P002"})

    factory, clients = mock_clients(handler)
    with TestClient(create_app(MonitoringAPISettings(planning_base_url="https://planning.example.test"),
                              authenticator=auth, client_factory=factory)) as client:
        response = client.get(ROOT + "/projects/P001/baseline", headers=HEADERS)
        assert response.status_code == status
        assert_redacted(response)
    assert len(sent) == 1
    assert all(client.is_closed for client in clients)


def test_supabase_client_is_separate_and_only_writes_on_explicit_save(auth, schedule):
    sent, rows = [], []

    def handler(request):
        sent.append(request)
        if request.url.host == "planning.example.test":
            assert "apikey" not in request.headers
            return httpx.Response(200, json=schedule)
        assert request.url.host == "storage.example.test"
        assert request.headers["apikey"] == DB_KEY
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        if request.method == "POST":
            rows.append(json.loads(request.content))
            return httpx.Response(201)
        return httpx.Response(200, json=rows)

    factory, clients = mock_clients(handler)
    settings = MonitoringAPISettings(
        planning_base_url="https://planning.example.test", planning_api_key=SecretStr(KEY),
        storage=SupabaseMonitoringSettings(url="https://storage.example.test", secret_key=SecretStr(DB_KEY)),
    )
    with TestClient(create_app(settings, authenticator=auth, client_factory=factory)) as client:
        assert client.post(ROOT + "/reports", headers=HEADERS, json=request_body()).status_code == 200
        assert len(clients) == 1 and len(sent) == 1 and rows == []
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(save_report=True))
        assert response.status_code == 200
        assert rows[0]["report"] == response.json()
        assert TOKEN not in json.dumps(rows) and DB_KEY not in json.dumps(rows)
        assert client.get(ROOT + "/projects/P001/reports", headers=HEADERS).json() == [response.json()]
    assert all(client.is_closed for client in clients)
    assert len(clients) == 4


@pytest.mark.parametrize("url", [
    "http://external.example.test", "http://localhost.evil.example.test", "ftp://planning.example.test",
    "https://user:credential@planning.example.test", "https://planning.example.test/api",
    "https://planning.example.test?secret=unsafe", "https://planning.example.test?",
    "https://planning.example.test#secret", "https://planning.example.test#",
    "//planning.example.test", "https://", "*", " https://planning.example.test",
    "https://planning.example.test\n", "http://0.0.0.0:8000", "http://127.0.0.2:8000",
])
@pytest.mark.parametrize("setting", ["planning_base_url", "risk_base_url"])
def test_untrusted_or_non_root_provider_urls_are_rejected_safely(url, setting):
    with pytest.raises(MonitoringAPIConfigurationError) as captured:
        MonitoringAPISettings(**{setting: url})
    rendered = "".join(traceback.format_exception(captured.value))
    assert "credential@" not in rendered and "secret=unsafe" not in rendered


@pytest.mark.parametrize("url", ["https://planning.example.test", "https://planning.example.test:8443/",
                                "http://localhost:8000", "http://127.0.0.1:8000/"])
def test_trusted_https_and_explicit_loopback_roots_are_accepted(url):
    settings = MonitoringAPISettings(planning_base_url=url, risk_base_url=url)
    assert settings.planning_base_url == url.rstrip("/")
    assert settings.risk_base_url == url.rstrip("/")


@pytest.mark.parametrize("origins", [("*",), ("null",), ("https://ui.example.test/path",),
                                    ("https://user:credential@ui.example.test",), "*"])
def test_wildcard_or_invalid_cors_is_rejected(origins):
    with pytest.raises(MonitoringAPIConfigurationError):
        MonitoringAPISettings(cors_origins=origins)


@pytest.mark.parametrize("origin,allowed", [("http://localhost:5173", True), ("http://127.0.0.1:5173", True),
                                         ("https://attacker.example.test", False), ("null", False)])
def test_cors_is_explicit_no_wildcard_credentials(origin, allowed):
    with TestClient(create_app(MonitoringAPISettings())) as client:
        response = client.options(ROOT + "/reports", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        })
        assert response.status_code == (200 if allowed else 400)
        assert response.headers.get("access-control-allow-origin") == (origin if allowed else None)
        assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("timeout", [0, -1, 301, float("inf"), float("nan"), True, "unsafe"])
def test_timeout_validation_is_safe(timeout):
    with pytest.raises(MonitoringAPIConfigurationError):
        MonitoringAPISettings(provider_timeout_seconds=timeout)


@pytest.mark.parametrize("raw", [
    "not JSON", "[]", "{}", json.dumps({"short": {"subject": "user", "project_ids": [], "permissions": []}}),
    identity_json(subject=3), identity_json(subject="*"), identity_json(project_ids=["*"]),
    identity_json(project_ids="P001"), identity_json(project_ids=["P001", "P001"]),
    identity_json(permissions=["*"]), identity_json(permissions=["monitoring:admin"]),
    identity_json(permissions="monitoring:read"), identity_json(permissions=["monitoring:read", "monitoring:read"]),
    identity_json(provider_url="https://attacker.example.test"),
    '{"' + TOKEN + '":{"subject":"first","subject":"second","project_ids":[],"permissions":[]}}',
])
def test_invalid_identity_mapping_fails_closed_without_echo(raw):
    settings = MonitoringAPISettings(auth_identities=SecretStr(raw), planning_base_url="https://planning.example.test")
    with TestClient(create_app(settings)) as client:
        health = client.get(ROOT + "/health")
        assert health.json()["capabilities"]["auth_configured"] is False
        assert health.json()["status"] == "configuration_required"
        response = client.get(ROOT + "/session", headers=HEADERS)
        assert response.status_code == 503
        assert_redacted(response, raw)
    assert raw not in repr(settings) and TOKEN not in repr(settings)


def test_environment_file_is_explicit_process_override_and_secrets_hidden(clean_environment, monkeypatch, tmp_path):
    path = tmp_path / "isolated.env"
    contents = "\n".join([
        "MONITORING_AUTH_IDENTITIES='" + identity_json() + "'",
        "MONITORING_PLANNING_BASE_URL=https://file-planning.example.test",
        "MONITORING_RISK_BASE_URL=https://file-risk.example.test",
        "MONITORING_PLANNING_API_KEY=" + KEY,
        "MONITORING_PROVIDER_TIMEOUT_SECONDS=12",
        "MONITORING_CORS_ORIGINS=https://ui.example.test,http://localhost:5173",
        "MONITORING_RISK_FAILURE_POLICY=report_unknown",
        "SUPABASE_URL=https://storage.example.test", "SUPABASE_SECRET_KEY=" + DB_KEY,
        "SUPABASE_MONITORING_REPORTS_TABLE=custom_reports",
        "SUPABASE_MONITORING_OBSERVATIONS_TABLE=custom_observations",
    ])
    path.write_text(contents, encoding="utf-8")
    monkeypatch.setenv("MONITORING_PLANNING_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("MONITORING_PROVIDER_TIMEOUT_SECONDS", "7.5")
    settings = MonitoringAPISettings.from_env(path)
    assert settings.planning_base_url == "http://127.0.0.1:8000"
    assert settings.risk_base_url == "https://file-risk.example.test"
    assert settings.storage is not None
    assert settings.provider_timeout_seconds == settings.storage.timeout_seconds == 7.5
    assert settings.risk_failure_policy == "report_unknown"
    assert settings.storage.reports_table == "custom_reports"
    assert settings.storage.observations_table == "custom_observations"
    assert path.read_text(encoding="utf-8") == contents
    assert TOKEN not in repr(settings) and KEY not in repr(settings) and DB_KEY not in repr(settings)
    with TestClient(create_app(env_file=path)) as client:
        assert client.get(ROOT + "/session", headers=HEADERS).json()["subject"] == "offline-manager"
        assert client.get(ROOT + "/health").json()["live_sources_verified"] is False


def test_default_env_path_is_resolved_backend_not_working_directory(clean_environment, monkeypatch):
    from app.agents.monitoring import api

    seen = []
    monkeypatch.setattr(Path, "is_file", lambda path: True)

    def read(path, **options):
        seen.append((path, options))
        return {"MONITORING_AUTH_IDENTITIES": identity_json(), "MONITORING_PLANNING_BASE_URL": "https://planning.example.test"}

    monkeypatch.setattr(api, "dotenv_values", read)
    settings = MonitoringAPISettings.from_env()
    assert seen == [(Path(api.__file__).resolve().parents[3] / ".env", {"interpolate": False})]
    assert settings.planning_base_url == "https://planning.example.test"


@pytest.mark.parametrize("values", [{}, {"SUPABASE_URL": "https://storage.example.test"},
                                   {"SUPABASE_URL": "http://storage.example.test", "SUPABASE_SECRET_KEY": DB_KEY},
                                   {"SUPABASE_URL": "https://storage.example.test", "SUPABASE_SECRET_KEY": DB_KEY,
                                    "SUPABASE_MONITORING_REPORTS_TABLE": "private.bad-table"},
                                   {"SUPABASE_URL": "https://storage.example.test", "SUPABASE_SECRET_KEY": DB_KEY,
                                    "SUPABASE_MONITORING_OBSERVATIONS_TABLE": "private.bad-table"}])
def test_absent_or_invalid_storage_does_not_crash_service(clean_environment, monkeypatch, tmp_path, values):
    monkeypatch.setenv("MONITORING_AUTH_IDENTITIES", identity_json())
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with TestClient(create_app(env_file=tmp_path / "absent.env")) as client:
        assert client.get(ROOT + "/health").json()["capabilities"]["storage_configured"] is False
        response = client.get(ROOT + "/projects/P001/reports", headers=HEADERS)
        assert response.status_code == 503
        assert_redacted(response)


def test_router_can_be_mounted_without_shared_main_and_redacts_validation(auth):
    service = FastAPI()
    service.include_router(create_router(MonitoringAPISettings(), authenticator=auth))
    with TestClient(service) as client:
        assert client.get(ROOT + "/session", headers=HEADERS).status_code == 200
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(api_key=KEY))
        assert response.status_code == 422
        assert_redacted(response)


@pytest.mark.asyncio
async def test_asgi_transport_factory_works_without_startup_network(auth):
    service = create_app(MonitoringAPISettings(), authenticator=auth)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service), base_url="http://testserver") as client:
        response = await client.get(ROOT + "/session", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["project_ids"] == ["P001", "P002"]


def test_api_import_does_not_import_shared_services_or_start_models(clean_environment):
    script = '''
import importlib.abc
import sys
class RejectSharedImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = ("app.main", "app.config", "app.services", "app.agents.planning", "app.agents.risk",
                     "torch", "transformers", "sentence_transformers", "chromadb", "groq", "supabase")
        if any(fullname == name or fullname.startswith(name + ".") for name in forbidden):
            raise AssertionError("Standalone Monitoring imported shared runtime: " + fullname)
sys.meta_path.insert(0, RejectSharedImports())
import dotenv
# Guard this fresh interpreter's import-time configuration too.
dotenv.dotenv_values = lambda *args, **kwargs: {}
from app.agents.monitoring.api import app
assert app.title == "Monitoring & Reporting Agent"
print("isolated import passed")
'''
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=Path(__file__).resolve().parents[4],
                            capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated import passed"


@pytest.mark.parametrize("source", ["planning", "risk"])
def test_historical_live_observations_rejected_before_clients(auth, schedule, source):
    calls = []

    def forbidden_client(**options):
        calls.append(options)
        pytest.fail("Historical live source must be rejected before opening clients")

    settings = MonitoringAPISettings(
        planning_base_url="https://planning.example.test" if source == "planning" else None,
        risk_base_url="https://risk.example.test" if source == "risk" else None,
    )
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    changes: dict[str, object] = {"as_of": yesterday}
    if source == "risk":
        changes.update(planning_message=message(schedule), received_on=yesterday, include_risks=True)
    with TestClient(create_app(settings, authenticator=auth, client_factory=forbidden_client)) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(**changes))
        assert response.status_code == 422
        assert calls == []
        assert_redacted(response)


@pytest.mark.parametrize("field", ["actual_start", "updated_on", "actual_end"])
def test_actual_task_future_dates_rejected_before_sources(harness, observations, field):
    client, planning, risk, repository, factories = harness
    task = observations["tasks"][0]
    task[field] = (TODAY + timedelta(days=1)).isoformat()
    if field == "actual_end":
        task.update(status="COMPLETED", progress_percentage=100)
    response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
        actual_progress=observations, observed_on=TODAY.isoformat(), include_risks=True, save_report=True,
    ))
    assert response.status_code == 422
    assert factories == planning.calls == risk.calls == repository.calls == []


def test_risks_are_included_by_default_and_missing_source_is_503(auth, schedule):
    calls = []

    def forbidden_plan(client):
        calls.append(client)
        pytest.fail("Missing included Risk source must fail before Planning")

    with TestClient(create_app(MonitoringAPISettings(), authenticator=auth,
                              planning_provider_factory=forbidden_plan)) as client:
        body = request_body()
        del body["include_risks"]
        response = client.post(ROOT + "/reports", headers=HEADERS, json=body)
        assert response.status_code == 503
        assert "Risk" in response.json()["detail"]
        assert calls == []


@pytest.mark.parametrize("operation", ["save", "get", "list", "export"])
def test_storage_timeout_is_504_without_report_response(harness, operation):
    client, _, _, repository, _ = harness
    repository.error = httpx.ReadTimeout("unsafe storage timeout " + DB_KEY)
    if operation == "save":
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(save_report=True))
    else:
        path = ROOT + "/projects/P001/reports"
        if operation != "list":
            path += "/report-1"
        if operation == "export":
            path += "/coordinator-message"
        response = client.get(path, headers=HEADERS)
    assert response.status_code == 504
    assert_redacted(response)
    assert repository.reports == {}


def test_wrapped_supabase_timeout_is_detected_without_echo(auth):
    def handler(request):
        raise httpx.ReadTimeout("sensitive storage timeout " + DB_KEY, request=request)

    factory, clients = mock_clients(handler)
    settings = MonitoringAPISettings(storage=SupabaseMonitoringSettings(
        url="https://storage.example.test", secret_key=SecretStr(DB_KEY),
    ))
    with TestClient(create_app(settings, authenticator=auth, client_factory=factory)) as client:
        response = client.get(ROOT + "/projects/P001/reports", headers=HEADERS)
        assert response.status_code == 504
        assert_redacted(response)
    assert clients[0].is_closed


@pytest.mark.parametrize("failure,status", [("timeout", 504), ("mismatch", 502)])
def test_real_risk_failure_is_not_success(auth, schedule, failure, status):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "planning.example.test":
            return httpx.Response(200, json=schedule)
        if failure == "timeout":
            raise httpx.ReadTimeout("sensitive Risk timeout " + KEY, request=request)
        return httpx.Response(200, json={"project_id": "P002", "risks": [], "summary": "",
                                       "evidence_used": [], "knowledge_sources": []})

    factory, clients = mock_clients(handler)
    settings = MonitoringAPISettings(planning_base_url="https://planning.example.test",
                                     risk_base_url="https://risk.example.test")
    with TestClient(create_app(settings, authenticator=auth, client_factory=factory)) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(include_risks=True))
        assert response.status_code == status
        assert "report_id" not in response.json()
        assert_redacted(response)
    assert len(requests) == 2
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("policy", ["fail", "report_unknown"])
@pytest.mark.parametrize("failure", ["project", "schema", "risk_field", "duplicates", "json", "non_object", "task"])
def test_real_risk_integrity_failures_are_redacted_and_never_saved(auth, schedule, risk_response, policy, failure):
    requests = []
    repository = MemoryRepository()
    if failure == "project":
        risk_response["project_id"] = "P002"
    elif failure == "schema":
        del risk_response["summary"]
    elif failure == "risk_field":
        risk_response["risks"][0]["confidence"] = 2
    elif failure == "duplicates":
        risk_response["risks"].append(deepcopy(risk_response["risks"][0]))
    elif failure == "task":
        risk_response["risks"][0]["task_id"] = "UNKNOWN"

    def handler(request):
        requests.append(request)
        if request.url.host == "planning.example.test":
            return httpx.Response(200, json=schedule)
        if failure == "json":
            return httpx.Response(200, text="Invalid JSON " + KEY)
        return httpx.Response(200, json=[] if failure == "non_object" else risk_response)

    factory, clients = mock_clients(handler)
    settings = MonitoringAPISettings(planning_base_url="https://planning.example.test",
                                     risk_base_url="https://risk.example.test", risk_failure_policy=policy)
    with TestClient(create_app(settings, authenticator=auth, repository=repository, client_factory=factory)) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS,
                               json=request_body(include_risks=True, save_report=True))
        assert response.status_code == 502
        assert "report_id" not in response.json()
        assert_redacted(response)
    assert repository.calls == [] and repository.reports == {}
    assert len(requests) == 2
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("policy", ["fail", "report_unknown"])
@pytest.mark.parametrize("failure,status", [
    ("connection", 502), ("timeout", 504), (408, 502), (429, 502), (500, 502), (503, 502),
    (302, 502), (400, 502), (401, 502), (403, 502), (404, 502), (422, 502),
])
def test_real_risk_degrades_only_availability_failures(auth, schedule, policy, failure, status):
    repository = MemoryRepository()
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "planning.example.test":
            return httpx.Response(200, json=schedule)
        if failure == "connection":
            raise httpx.ConnectError("Synthetic failure " + KEY, request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("Synthetic timeout " + KEY, request=request)
        return httpx.Response(failure, text="Synthetic error " + KEY,
                              headers={"Location": "https://untrusted.example.test/" + KEY})

    factory, clients = mock_clients(handler)
    settings = MonitoringAPISettings(planning_base_url="https://planning.example.test",
                                     risk_base_url="https://risk.example.test", risk_failure_policy=policy)
    degraded = policy == "report_unknown" and failure in ("connection", "timeout", 408, 429, 500, 503)
    with TestClient(create_app(settings, authenticator=auth, repository=repository, client_factory=factory)) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS,
                               json=request_body(include_risks=True, save_report=True))
        assert response.status_code == (200 if degraded else status)
        assert_redacted(response)
        if degraded:
            report = response.json()
            assert report["coverage"]["risk_status"] == "unavailable"
            assert report["risks"] == [] and report["status"] == "AT_RISK"
            assert any(warning["code"] == "RISK_UNAVAILABLE" for warning in report["warnings"])
            assert repository.calls == [("save", "P001", PRINCIPAL.subject)]
            assert len(repository.reports) == 1
        else:
            assert "report_id" not in response.json()
            assert repository.calls == [] and repository.reports == {}
    assert len(requests) == 2
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("message_input", [False, True])
def test_real_risk_context_isolates_source_credentials_and_retains_evidence(auth, schedule, observations,
                                                                          risk_response, message_input):
    marker = "synthetic-sensitive-source"
    schedule["metadata"] = {
        "accessToken": marker,
        "records": json.dumps([{"clientSecret": marker, "document_id": "review-1"}]),
        "note": "Vendor review is pending",
    }
    observations["metadata"] = {"headers": {"Authorization": marker, "Accept": "application/json"}}
    envelope = message(schedule)
    envelope["transport_metadata"] = json.dumps({"APIKey": marker, "correlation_id": "review-correlation"})
    originals = deepcopy((schedule, observations, envelope))
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=schedule if request.url.host == "planning.example.test" else risk_response)

    factory, clients = mock_clients(handler)
    settings = MonitoringAPISettings(planning_base_url="https://planning.example.test",
                                     risk_base_url="https://risk.example.test")
    body = request_body(actual_progress=observations, observed_on=TODAY.isoformat(), include_risks=True)
    if message_input:
        body.update(planning_message=envelope, received_on=TODAY.isoformat())
    with TestClient(create_app(settings, authenticator=auth, client_factory=factory)) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS, json=body)
        assert response.status_code == 200
        assert_redacted(response, marker)
        report = response.json()
        assert report["planning_context"]["metadata"]["note"] == "Vendor review is pending"
        assert json.loads(report["planning_context"]["metadata"]["records"]) == [{"document_id": "review-1"}]
        expected_actual = deepcopy(observations)
        expected_actual["metadata"] = {"headers": {"Accept": "application/json"}}
        assert report["actual_context"]["source_payload"] == expected_actual
        if message_input:
            source = report["planning_context"]["message_source"]["message"]
            assert json.loads(source["transport_metadata"]) == {"correlation_id": "review-correlation"}
    assert (schedule, observations, envelope) == originals
    information = json.loads(requests[-1].content)["project_information"]
    assert marker not in information
    context = json.loads(information)
    metadata = context["planned_baseline"]["context"]["metadata"]
    assert metadata["note"] == "Vendor review is pending"
    assert json.loads(metadata["records"]) == [{"document_id": "review-1"}]
    assert context["observed_actual_measurements"]["context"]["source_payload"]["metadata"] == {
        "headers": {"Accept": "application/json"},
    }
    if message_input:
        source = context["planned_baseline"]["context"]["message_source"]["message"]
        assert json.loads(source["transport_metadata"]) == {"correlation_id": "review-correlation"}
    assert len(requests) == (1 if message_input else 2)
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("policy", ["ignore", "zero", "degrade"])
def test_unknown_risk_policy_is_rejected_safely(policy):
    with pytest.raises(MonitoringAPIConfigurationError) as captured:
        MonitoringAPISettings(risk_failure_policy=policy)
    assert "must be fail or report_unknown" in str(captured.value)


@pytest.mark.parametrize("limit", [1, 20, 100])
def test_valid_list_limits_pass_to_authorized_repository(harness, limit):
    client, _, _, repository, _ = harness
    response = client.get(ROOT + "/projects/P001/reports?limit=" + str(limit), headers=HEADERS)
    assert response.status_code == 200 and response.json() == []
    assert repository.calls == [("list", "P001", PRINCIPAL.subject, limit)]


def test_default_list_limit_is_twenty(harness):
    client, _, _, repository, _ = harness
    assert client.get(ROOT + "/projects/P001/reports", headers=HEADERS).json() == []
    assert repository.calls == [("list", "P001", PRINCIPAL.subject, 20)]


@pytest.mark.parametrize("source", ["planning", "storage"])
def test_upstream_http_exceptions_cannot_bypass_redaction(harness, source):
    client, planning, _, repository, _ = harness
    failure = HTTPException(400, {"token": TOKEN, "server_key": DB_KEY}, headers={"X-Secret": KEY})
    if source == "planning":
        planning.error = failure
        response = client.get(ROOT + "/projects/P001/baseline", headers=HEADERS)
    else:
        repository.error = failure
        response = client.get(ROOT + "/projects/P001/reports", headers=HEADERS)
    assert response.status_code == 502
    assert "x-secret" not in response.headers
    assert_redacted(response)


@pytest.fixture
def shared_settings(monkeypatch, schedule):
    # Only the integration plumbing is stubbed here; guarded account tests exercise
    # real signed JWTs, DB refresh, role changes, and ownership races.
    actor = SimpleNamespace(role="user", user_id="11111111-1111-4111-8111-111111111111",
                            subject="offline-user", owner_filter="11111111-1111-4111-8111-111111111111")

    def require_actor(*, authorization, x_api_key):
        assert x_api_key is None
        if authorization != HEADERS["Authorization"]:
            raise HTTPException(401, "Invalid authentication token.", headers={"WWW-Authenticate": "Bearer"})
        return actor

    class Schedule:
        project_id = "P001"

        def model_dump(self, **options):
            return deepcopy(schedule)

    class Repository:
        def get(self, project_id, *, owner_user_id):
            assert owner_user_id == actor.user_id
            return Schedule() if project_id == "P001" else None

        def exists(self, project_id, *, owner_user_id):
            return self.get(project_id, owner_user_id=owner_user_id) is not None

        def get_all(self, *, owner_user_id):
            assert owner_user_id == actor.user_id
            return [Schedule()]

    for name, attributes in {
        "app.core.project_access": {"require_project_actor": require_actor},
        "app.repository.planning_repository": {"PlanningRepository": Repository},
    }.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    return MonitoringAPISettings(auth_identities=SecretStr(identity_json()), planning_api_key=SecretStr(KEY))


@pytest.fixture
def shared_clients(monkeypatch):
    """Keep real in-process transports, but intercept every potential network call."""
    constructor = httpx.AsyncClient
    clients, options_seen, network_requests, rows = [], [], [], []

    def network(request):
        network_requests.append(request)
        assert request.url.host == "storage.example.test"
        assert request.url.path == "/rest/v1/monitoring_reports"
        assert request.headers["apikey"] == DB_KEY
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        if request.method == "POST":
            rows.append(json.loads(request.content))
            return httpx.Response(201)
        assert request.method == "GET"
        return httpx.Response(200, json=rows)

    def factory(**options):
        options_seen.append(dict(options))
        if "transport" not in options:
            options["transport"] = httpx.MockTransport(network)
        client = constructor(**options)
        clients.append(client)
        return client

    monkeypatch.setattr("app.agents.monitoring.api.httpx.AsyncClient", factory)
    return clients, options_seen, network_requests, rows


@pytest.fixture
def shared_upstreams(schedule, risk_response, shared_clients):
    service, calls = FastAPI(), []

    @service.get("/api/planning/schedule/{project_id}")
    async def planning(project_id: str, request: Request):
        calls.append((request, None))
        assert project_id == "P001"
        return schedule

    @service.post("/api/risk/analyse")
    async def risk(request: Request):
        payload = await request.json()
        calls.append((request, payload))
        return risk_response

    return service, calls


@pytest.mark.parametrize("configured", [True, False])
def test_shared_health_registers_sources_without_bypassing_auth(shared_upstreams, shared_settings, shared_clients,
                                                               configured):
    service, calls = shared_upstreams
    settings = shared_settings if configured else MonitoringAPISettings()
    service.include_router(create_shared_router(service, settings))
    with TestClient(service) as client:
        response = client.get(ROOT + "/health")
        assert response.status_code == 200
        assert response.json() == {
            "agent": "Monitoring & Reporting Agent",
            "status": "ready",
            "capabilities": {"auth_configured": True, "planning_configured": True,
                             "risk_configured": True, "storage_configured": False,
                             "observations_storage_configured": False, "llm_configured": False},
            "live_sources_verified": False,
        }
        assert_redacted(response)
        session = client.get(ROOT + "/session", headers=HEADERS)
        assert session.status_code == 200
        assert session.json()["project_ids"] == ["P001"]
        assert session.json()["subject"] == "11111111-1111-4111-8111-111111111111"
    assert calls == shared_clients[0] == shared_clients[2] == []
    assert settings.planning_base_url is settings.risk_base_url is None


def test_shared_default_loads_env_settings_without_changing_environment(monkeypatch, shared_upstreams,
                                                                     shared_settings, shared_clients):
    service, calls = shared_upstreams
    loaded = []

    def load(cls):
        loaded.append(cls)
        return replace(shared_settings, planning_base_url="https://unused-planning.example.test",
                       risk_base_url="https://unused-risk.example.test")

    monkeypatch.setattr(MonitoringAPISettings, "from_env", classmethod(load))
    service.include_router(create_shared_router(service))
    with TestClient(service) as client:
        assert client.get(ROOT + "/health").json()["status"] == "ready"
        assert client.get(ROOT + "/projects/P001/baseline", headers=HEADERS).status_code == 200
    assert loaded == [MonitoringAPISettings]
    assert calls == shared_clients[0] == shared_clients[2] == []


@pytest.mark.parametrize("operation,headers,identity,status", [
    ("baseline", {}, {}, 401),
    ("generate", {"Authorization": "Bearer " + "z" * 40}, {}, 401),
    ("baseline", {"X-API-Key": KEY}, {}, 401),
    ("generate", {"Authorization": "Bearer " + "z" * 40, "X-API-Key": KEY}, None, 401),
])
def test_shared_auth_and_scopes_precede_internal_routes(shared_upstreams, shared_settings, shared_clients,
                                                       operation, headers, identity, status):
    service, calls = shared_upstreams
    settings = replace(shared_settings, auth_identities=(
        SecretStr(identity_json(**identity)) if identity is not None else None))
    service.include_router(create_shared_router(service, settings))
    with TestClient(service) as client:
        if operation == "baseline":
            response = client.get(ROOT + "/projects/P001/baseline", headers=headers)
        else:
            response = client.post(ROOT + "/reports", headers=headers, json=request_body(include_risks=True))
        assert response.status_code == status
        assert_redacted(response)
        if status == 401:
            assert response.headers["www-authenticate"] == "Bearer"
    assert calls == shared_clients[0] == shared_clients[2] == []


def test_shared_real_baseline_report_and_risk_context(shared_upstreams, shared_settings, shared_clients,
                                                    schedule, observations, risk_response):
    service, calls = shared_upstreams
    service.include_router(create_shared_router(service, shared_settings, planning_api_key=KEY))
    with TestClient(service) as client:
        baseline = client.get(ROOT + "/projects/P001/baseline", headers=HEADERS)
        assert baseline.status_code == 200
        assert baseline.json() == adapt_planning_schedule(schedule, TODAY).model_dump(mode="json")
        assert baseline.json()["tasks"][0]["expected_progress"] is None
        for _ in range(2):
            body = request_body(actual_progress=observations, observed_on=TODAY.isoformat())
            del body["include_risks"]
            response = client.post(ROOT + "/reports", headers=HEADERS, json=body)
            assert response.status_code == 200
            report = response.json()
            assert report["metrics"]["actual_progress"] == 40
            assert report["planning_context"] == schedule
            assert report["risks"][0]["risk_id"] == "R001"
            assert report["risk_summary"] == risk_response["summary"]
            assert report["risk_evidence_used"] == risk_response["evidence_used"]
            assert report["coverage"]["risk_status"] == "available"
            assert report["coverage"]["actual_status"] == "complete"
            assert_redacted(response)
    assert [request.url.path for request, _ in calls] == ["/api/risk/analyse"] * 2
    for request, payload in calls:
        assert "authorization" not in request.headers and "apikey" not in request.headers
        if payload is None:
            assert request.method == "GET" and request.headers["x-api-key"] == KEY
        else:
            assert request.method == "POST" and "x-api-key" not in request.headers
            context = json.loads(payload["project_information"])
            assert payload["project_id"] == context["project_id"] == "P001"
            assert payload["source_agent"] == "Monitoring Agent" and payload["top_k"] == 5
            assert context["planned_baseline"]["tasks"][0]["task_id"] == "T001"
            assert context["observed_actual_measurements"]["tasks"][0]["progress"] == 40
            assert context["source_dates"]["planning_updated_on"] is None
            assert context["source_dates"]["planning_observed_on"] == TODAY.isoformat()
            assert TOKEN not in json.dumps(payload) and KEY not in json.dumps(payload)
    clients, options, network_requests, rows = shared_clients
    assert len(clients) == 2 and len({id(client) for client in clients}) == 2
    assert all(client.is_closed for client in clients)
    assert all(isinstance(option["transport"], httpx.ASGITransport) for option in options)
    assert all(option["follow_redirects"] is False and option["trust_env"] is False for option in options)
    assert network_requests == rows == []


@pytest.mark.parametrize("key", ["shared-explicit-key", SecretStr("shared-explicit-key"), None])
def test_shared_planning_key_override_and_fallback(shared_upstreams, shared_settings, key):
    service, calls = shared_upstreams
    service.include_router(create_shared_router(service, shared_settings, planning_api_key=key))
    with TestClient(service) as client:
        assert client.get(ROOT + "/projects/P001/baseline", headers=HEADERS).status_code == 200
    assert calls == []  # Planning reads the owner-filtered repository, not service-key HTTP.
    assert shared_settings.planning_api_key.get_secret_value() == KEY


@pytest.mark.parametrize("source", ["risk"])
@pytest.mark.parametrize("failure,status", [
    ("redirect", 502), ("http-error", 502), ("unhandled", 502), ("bad-json", 502),
    ("wrong-project", 502), ("timeout", 504),
])
def test_shared_provider_errors_are_redacted_without_redirects(shared_settings, shared_clients, schedule,
                                                              risk_response, source, failure, status):
    service, calls, redirected = FastAPI(), [], []

    async def upstream(request: Request):
        calls.append(request.url.path)
        is_planning = request.method == "GET"
        data = schedule if is_planning else risk_response
        if is_planning != (source == "planning"):
            return data
        if failure == "redirect":
            return Response(status_code=302, headers={"Location": "/redirect-target"})
        if failure == "http-error":
            raise HTTPException(500, {"key": KEY, "token": TOKEN}, headers={"X-Secret": DB_KEY})
        if failure == "unhandled":
            raise RuntimeError("sensitive " + KEY)
        if failure == "bad-json":
            return Response(content="invalid " + KEY, media_type="application/json")
        if failure == "wrong-project":
            return {**data, "project_id": "P002"}
        await asyncio.sleep(0.1)
        return data

    @service.api_route("/redirect-target", methods=["GET", "POST"])
    async def redirect_target():
        redirected.append(True)
        return {}

    service.add_api_route("/api/planning/schedule/{project_id}", upstream, methods=["GET"])
    service.add_api_route("/api/risk/analyse", upstream, methods=["POST"])
    settings = replace(shared_settings, provider_timeout_seconds=0.02 if failure == "timeout" else 30)
    service.include_router(create_shared_router(service, settings))
    with TestClient(service) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(include_risks=True))
        assert response.status_code == status
        assert_redacted(response)
        assert "x-secret" not in response.headers and "location" not in response.headers
    assert calls == ["/api/risk/analyse"]
    assert redirected == shared_clients[2] == []
    assert all(client.is_closed for client in shared_clients[0])


@pytest.mark.parametrize("source", ["planning", "risk"])
def test_shared_historical_as_of_rejected_before_clients(shared_upstreams, shared_settings, shared_clients,
                                                       schedule, source):
    service, calls = shared_upstreams
    service.include_router(create_shared_router(service, shared_settings))
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    changes: dict[str, object] = {"as_of": yesterday}
    if source == "risk":
        changes.update(planning_message=message(schedule), received_on=yesterday, include_risks=True)
    with TestClient(service) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(**changes))
        assert response.status_code == 422
        assert response.json()["detail"] == "Current live observations cannot be used for a historical as_of"
        assert_redacted(response)
    assert calls == shared_clients[0] == shared_clients[2] == []


def test_shared_storage_uses_separate_network_client_only_on_explicit_save(shared_upstreams, shared_settings,
                                                                        shared_clients):
    service, calls = shared_upstreams
    internal_storage_calls = []

    @service.api_route("/rest/v1/{path:path}", methods=["GET", "POST"])
    async def internal_storage(path: str):
        internal_storage_calls.append(path)
        raise AssertionError("Storage must never be routed through the shared application")

    storage = SupabaseMonitoringSettings(url="https://storage.example.test", secret_key=SecretStr(DB_KEY))
    service.include_router(create_shared_router(service, replace(shared_settings, storage=storage)))
    clients, options, network_requests, rows = shared_clients
    with TestClient(service) as client:
        unsaved = client.post(ROOT + "/reports", headers=HEADERS, json=request_body())
        assert unsaved.status_code == 200
        assert unsaved.json()["metrics"]["actual_progress"] is None
        assert rows == network_requests == clients == []
        saved = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(save_report=True))
        assert saved.status_code == 200
        report = saved.json()
        assert rows[0]["report"] == report
        assert client.get(ROOT + "/projects/P001/reports", headers=HEADERS).json() == [report]
        path = ROOT + "/projects/P001/reports/" + report["report_id"]
        assert client.get(path, headers=HEADERS).json() == report
        assert client.get(path + "/coordinator-message", headers=HEADERS).json()["report"] == report
    assert calls == internal_storage_calls == []
    assert [request.method for request in network_requests] == ["POST", "GET", "GET", "GET"]
    assert len(clients) == 4 and all(client.is_closed for client in clients)
    storage_options = [option for option in options if "base_url" not in option]
    assert len(storage_options) == 4 and all("transport" not in option for option in storage_options)
    assert TOKEN not in json.dumps(rows) and KEY not in json.dumps(rows) and DB_KEY not in json.dumps(rows)


def test_shared_save_without_storage_fails_before_internal_routes(shared_upstreams, shared_settings, shared_clients):
    service, calls = shared_upstreams
    service.include_router(create_shared_router(service, shared_settings))
    with TestClient(service) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(save_report=True))
        assert response.status_code == 503
        assert_redacted(response)
    assert calls == shared_clients[0] == shared_clients[2] == []


@pytest.mark.asyncio
async def test_actual_main_bootstrap_mounts_all_agents_offline(monkeypatch, shared_settings, schedule):
    planning_router = APIRouter(prefix="/api/planning")
    risk_router = APIRouter(prefix="/api/risk")
    shared_key = "offline-bootstrap-shared-planning-key"
    planning_headers, env_loads = [], []

    @planning_router.get("/schedule/{project_id}")
    async def planning(project_id: str, request: Request):
        assert project_id == "P001"
        planning_headers.append(request.headers)
        return schedule

    @risk_router.get("/health")
    async def risk_health():
        return {"status": "offline"}

    def forbid_startup(*args, **kwargs):
        pytest.fail("Bootstrap regression must not run startup validation or open a browser")

    def no_env_settings(cls):
        env_loads.append(cls)
        return shared_settings

    bootstrap_settings = SimpleNamespace(
        cors_origins=("http://localhost:5173",), planning_api_key=shared_key)
    auth_router = APIRouter(prefix="/api/auth")
    auth_router.add_api_route("/login", lambda: {}, methods=["POST"])
    coordinator_router = APIRouter(prefix="/api/coordinator")
    coordinator_router.add_api_route("/interpret", lambda: {}, methods=["POST"])
    lightweight_modules = {
        "app.api.auth": {"router": auth_router},
        "app.api.accounts": {"router": APIRouter(prefix="/api/accounts")},
        "app.api.planning": {"router": planning_router},
        "app.api.risks": {"router": risk_router},
        "app.agents.coordinator.router": {"router": coordinator_router},
        "app.core.config": {
            "get_settings": lambda: bootstrap_settings,
            "settings": bootstrap_settings,
            "validate_configuration": forbid_startup,
        },
    }
    for name, attributes in lightweight_modules.items():
        lightweight = ModuleType(name)
        lightweight.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, lightweight)
    monkeypatch.setattr(MonitoringAPISettings, "from_env", classmethod(no_env_settings))
    monkeypatch.setattr("webbrowser.open", forbid_startup)
    monkeypatch.setattr("logging.basicConfig", lambda **kwargs: None)

    main_path = Path(__file__).resolve().parents[3] / "main.py"
    temporary_name = "app._monitoring_bootstrap_regression"
    spec = importlib.util.spec_from_file_location(temporary_name, main_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, temporary_name, module)
    spec.loader.exec_module(module)
    service = module.app
    paths = service.openapi()["paths"]
    assert "/api/auth/login" in paths
    for prefix in ("/api/planning/", "/api/risk/", "/api/coordinator/", ROOT + "/"):
        assert any(path.startswith(prefix) for path in paths)
    assert env_loads == [MonitoringAPISettings]

    # ASGITransport deliberately does not run the production app's lifespan.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service, raise_app_exceptions=False),
                                 base_url="http://testserver") as client:
        health = await client.get(ROOT + "/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ready"
        assert health.json()["capabilities"] == {
            "auth_configured": True, "planning_configured": True,
            "risk_configured": True, "storage_configured": False,
            "observations_storage_configured": False, "llm_configured": False,
        }
        assert health.json()["live_sources_verified"] is False
        assert planning_headers == []
        assert_redacted(health, shared_key)
        assert (await client.get("/api/risk/health")).json() == {"status": "offline"}
        baseline = await client.get(ROOT + "/projects/P001/baseline", headers=HEADERS)
        assert baseline.status_code == 200
        assert baseline.json() == adapt_planning_schedule(schedule, TODAY).model_dump(mode="json")
        assert_redacted(baseline, shared_key)
    assert planning_headers == []  # Shared Monitoring bypasses the service-only Planning route.
    assert shared_settings.planning_api_key.get_secret_value() == KEY


OBSERVATIONS = ROOT + "/projects/P001/observations"


def observation_body(observations, **changes):
    return {"request_id": "observation-request", "source": "site-inspection",
            "observed_on": TODAY.isoformat(), "actual_progress": deepcopy(observations), **changes}


class MemoryObservationRepository:
    """Test double only; production never falls back to memory."""

    def __init__(self):
        self.receipts, self.calls = {}, []
        self.error = None

    async def save(self, receipt, principal):
        self.calls.append(("save", receipt.project_id, principal.subject))
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        self.receipts[receipt.observation_id] = receipt.model_copy(deep=True)

    async def get(self, project_id, observation_id, principal):
        self.calls.append(("get", project_id, observation_id, principal.subject))
        if self.error is not None:
            raise self.error
        # Deliberately don't filter the test double: the API must also bind IDs.
        return self.receipts.get(observation_id)

    async def list(self, project_id, principal, limit=20):
        self.calls.append(("list", project_id, principal.subject, limit))
        if self.error is not None:
            raise self.error
        return [r for r in self.receipts.values() if r.project_id == project_id][:limit]


@pytest.fixture
def observation_harness(auth, schedule):
    planning, risk = OfflinePlanning(schedule), OfflineRisk()
    reports, receipts, factories = MemoryRepository(), MemoryObservationRepository(), []

    def plan_factory(client):
        factories.append("planning")
        return planning

    def risk_factory(client):
        factories.append("risk")
        return risk

    service = create_app(
        MonitoringAPISettings(), authenticator=auth, planning_provider_factory=plan_factory,
        risk_provider_factory=risk_factory, repository=reports, observation_repository=receipts,
    )
    with TestClient(service) as client:
        yield client, planning, risk, reports, receipts, factories


def test_intake_validation_only_is_canonical_not_durable(observation_harness, observations):
    client, planning, risk, reports, receipts, factories = observation_harness
    observations["tasks"][0]["status"] = "in progress"
    response = client.post(OBSERVATIONS, headers=HEADERS, json=observation_body(observations))
    assert response.status_code == 200
    receipt = response.json()
    assert set(receipt) == {"observation_id", "request_id", "project_id", "source", "observed_on",
                            "accepted_at", "submitted_by", "actual_progress", "persisted", "limitations"}
    assert receipt["persisted"] is False and receipt["submitted_by"] == PRINCIPAL.subject
    assert receipt["source"] == "site-inspection"
    assert receipt["accepted_at"].endswith("Z")
    assert receipt["actual_progress"]["tasks"][0]["status"] == "IN_PROGRESS"
    assert "updated_on" not in receipt["actual_progress"]
    assert "updated_on" not in receipt["actual_progress"]["tasks"][0]
    assert any("caller-claimed" in item for item in receipt["limitations"])
    assert any("Validation-only" in item for item in receipt["limitations"])
    assert factories == ["planning"] and planning.calls == ["P001"]
    assert risk.calls == reports.calls == receipts.calls == []
    assert client.get(OBSERVATIONS + "/" + receipt["observation_id"], headers=HEADERS).status_code == 404
    assert client.get(OBSERVATIONS, headers=HEADERS).json() == []
    assert_redacted(response)


def test_intake_explicit_save_preserves_genuine_dates_and_awaits_storage(observation_harness, observations):
    client, _, risk, reports, receipts, _ = observation_harness
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    observations["updated_on"] = yesterday
    observations["tasks"][0]["updated_on"] = yesterday
    response = client.post(OBSERVATIONS, headers=HEADERS, json=observation_body(observations, save_observation=True))
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["persisted"] is True
    assert receipt["actual_progress"] == observations
    assert receipt["observation_id"] in receipts.receipts
    assert receipts.calls == [("save", "P001", PRINCIPAL.subject)]
    assert risk.calls == reports.calls == []
    assert client.get(OBSERVATIONS + "/" + receipt["observation_id"], headers=HEADERS).json() == receipt
    assert client.get(OBSERVATIONS, headers=HEADERS).json() == [receipt]
    assert receipts.calls[-1] == ("list", "P001", PRINCIPAL.subject, 20)
    assert client.get(ROOT + "/projects/P002/observations", headers=HEADERS).json() == []
    # Even an injected repository returning a different project's receipt fails closed.
    assert client.get(ROOT + "/projects/P002/observations/" + receipt["observation_id"],
                      headers=HEADERS).status_code == 502


@pytest.mark.parametrize("permissions,projects,operation", [
    (["monitoring:read"], ["P001"], "intake"),
    (["monitoring:generate"], ["P002"], "intake"),
    (["monitoring:generate"], ["P001"], "get"),
    (["monitoring:generate"], ["P001"], "list"),
    (["monitoring:read"], ["P002"], "get"),
    (["monitoring:read"], ["P002"], "list"),
    (["monitoring:generate"], ["P001"], "report"),
    (["monitoring:read"], ["P001"], "report"),
    (["monitoring:read", "monitoring:generate"], ["P002"], "report"),
])
def test_observation_scopes_precede_all_factories_and_storage(monkeypatch, schedule, observations,
                                                              permissions, projects, operation):
    principal = MonitoringPrincipal(subject="limited-user", project_ids=frozenset(projects),
                                    permissions=frozenset(permissions))
    receipts, reports = MemoryObservationRepository(), MemoryRepository()

    def forbidden(*args, **kwargs):
        pytest.fail("Unauthorized request reached a local/upstream/client factory")

    monkeypatch.setattr("app.agents.monitoring.api.ActualProgressProvider", forbidden)
    monkeypatch.setattr("app.agents.monitoring.api.PlanningMessageProvider", forbidden)
    service = create_app(
        MonitoringAPISettings(), authenticator=MonitoringTokenAuthenticator({TOKEN: principal}),
        planning_provider_factory=forbidden, risk_provider_factory=forbidden, client_factory=forbidden,
        observation_repository=receipts, repository=reports,
    )
    with TestClient(service) as client:
        if operation == "intake":
            response = client.post(OBSERVATIONS, headers=HEADERS, json=observation_body(
                observations, save_observation=True, planning_message=message(schedule), received_on=TODAY.isoformat()))
        elif operation == "report":
            response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
                observation_id="observation-1", planning_message=message(schedule), received_on=TODAY.isoformat()))
        else:
            response = client.get(OBSERVATIONS + ("/observation-1" if operation == "get" else ""), headers=HEADERS)
        assert response.status_code == 403
        assert receipts.calls == reports.calls == []
        assert_redacted(response)


@pytest.mark.parametrize("configured,status", [(False, 503), (True, 401)])
@pytest.mark.parametrize("operation", ["intake", "get", "list"])
def test_observation_routes_require_verified_auth(auth, observations, configured, status, operation):
    with TestClient(create_app(MonitoringAPISettings(), authenticator=auth if configured else None)) as client:
        response = (client.post(OBSERVATIONS, json=observation_body(observations)) if operation == "intake" else
                    client.get(OBSERVATIONS + ("/missing" if operation == "get" else "")))
        assert response.status_code == status
        assert_redacted(response)


@pytest.mark.parametrize("mutation", [
    "extra", "payload_extra", "task_extra", "project", "duplicate", "status", "progress_bool", "progress_string",
    "not_started", "completed_without_end", "incomplete_100", "future_observation", "future_update",
    "future_start", "future_end", "project_update_older", "empty_tasks", "too_many_tasks", "save_string",
    "source_url", "source_whitespace", "numeric_date", "timestamp_date", "task_id", "received_without_message",
])
def test_invalid_intake_is_rejected_before_sources(observation_harness, observations, mutation):
    client, planning, risk, reports, receipts, factories = observation_harness
    body = observation_body(observations, save_observation=True)
    actual, task = body["actual_progress"], body["actual_progress"]["tasks"][0]
    if mutation == "extra":
        body["submitted_by"] = "attacker"
    elif mutation == "payload_extra":
        actual["authorization"] = TOKEN
    elif mutation == "task_extra":
        task["api_key"] = KEY
    elif mutation == "project":
        actual["project_id"] = "P002"
    elif mutation == "duplicate":
        actual["tasks"].append(deepcopy(task))
    elif mutation == "status":
        task["status"] = "ESTIMATED"
    elif mutation.startswith("progress_"):
        task["progress_percentage"] = True if mutation == "progress_bool" else "40"
    elif mutation == "not_started":
        task["status"] = "NOT_STARTED"
    elif mutation == "completed_without_end":
        task.update(status="COMPLETED", progress_percentage=100)
    elif mutation == "incomplete_100":
        task["progress_percentage"] = 100
    elif mutation == "future_observation":
        body["observed_on"] = (TODAY + timedelta(days=1)).isoformat()
    elif mutation.startswith("future_"):
        field = {"future_update": "updated_on", "future_start": "actual_start", "future_end": "actual_end"}[mutation]
        task[field] = (TODAY + timedelta(days=1)).isoformat()
        if field == "actual_end":
            task.update(status="COMPLETED", progress_percentage=100)
    elif mutation == "project_update_older":
        actual["updated_on"] = (TODAY - timedelta(days=1)).isoformat()
    elif mutation == "empty_tasks":
        actual["tasks"] = []
    elif mutation == "too_many_tasks":
        actual["tasks"] = [deepcopy(task) for _ in range(1001)]
    elif mutation == "save_string":
        body["save_observation"] = "true"
    elif mutation.startswith("source_"):
        body["source"] = "https://untrusted.test" if mutation == "source_url" else " site-inspection "
    elif mutation == "numeric_date":
        body["observed_on"] = 0
    elif mutation == "timestamp_date":
        body["observed_on"] += "T00:00:00Z"
    elif mutation == "task_id":
        task["task_id"] = " T001 "
    else:
        body["received_on"] = TODAY.isoformat()
    response = client.post(OBSERVATIONS, headers=HEADERS, json=body)
    assert response.status_code == 422
    assert factories == planning.calls == risk.calls == reports.calls == receipts.calls == []
    assert_redacted(response)


def test_intake_unknown_tasks_rejected_before_any_storage(observation_harness, observations):
    client, planning, risk, reports, receipts, _ = observation_harness
    observations["tasks"][0]["task_id"] = "T999"
    response = client.post(OBSERVATIONS, headers=HEADERS, json=observation_body(observations, save_observation=True))
    assert response.status_code == 422 and planning.calls == ["P001"]
    assert risk.calls == reports.calls == receipts.calls == []


@pytest.mark.parametrize("mutation", ["sender", "receiver", "action", "status", "project", "date"])
def test_intake_message_validation_precedes_sources(observation_harness, schedule, observations, mutation):
    client, planning, risk, reports, receipts, factories = observation_harness
    envelope = message(schedule)
    received = TODAY.isoformat()
    if mutation == "project":
        envelope["data"]["project_id"] = "P002"
    elif mutation == "date":
        received = (TODAY + timedelta(days=1)).isoformat()
    else:
        envelope[mutation] = "invalid"
    response = client.post(OBSERVATIONS, headers=HEADERS, json=observation_body(
        observations, planning_message=envelope, received_on=received, save_observation=True))
    assert response.status_code == 422
    assert factories == planning.calls == risk.calls == reports.calls == receipts.calls == []


def test_intake_real_message_works_without_live_or_storage(auth, schedule, observations):
    with TestClient(create_app(MonitoringAPISettings(), authenticator=auth)) as client:
        response = client.post(OBSERVATIONS, headers=HEADERS, json=observation_body(
            observations, planning_message=message(schedule), received_on=TODAY.isoformat()))
        assert response.status_code == 200 and response.json()["persisted"] is False
        saved = client.post(OBSERVATIONS, headers=HEADERS, json=observation_body(
            observations, planning_message=message(schedule), received_on=TODAY.isoformat(), save_observation=True))
        assert saved.status_code == 503
        assert client.get(OBSERVATIONS, headers=HEADERS).status_code == 503
        assert client.get(OBSERVATIONS + "/missing", headers=HEADERS).status_code == 503


@pytest.mark.parametrize("error,status", [(RuntimeError(DB_KEY), 502), (TimeoutError(DB_KEY), 504)])
def test_observation_save_failure_never_returns_receipt(observation_harness, observations, error, status):
    client, _, risk, reports, receipts, _ = observation_harness
    receipts.error = error
    response = client.post(OBSERVATIONS, headers=HEADERS, json=observation_body(observations, save_observation=True))
    assert response.status_code == status
    assert "observation_id" not in response.json() and receipts.receipts == {}
    assert risk.calls == reports.calls == []
    assert_redacted(response)


@pytest.mark.parametrize("limit", [0, 101, -1, "true", "1.2"])
def test_observation_list_limits_precede_storage(observation_harness, limit):
    client, _, _, _, receipts, factories = observation_harness
    assert client.get(OBSERVATIONS + "?limit=" + str(limit), headers=HEADERS).status_code == 422
    assert receipts.calls == factories == []


@pytest.mark.parametrize("field", ["actual_progress", "observed_on"])
def test_stored_id_is_mutually_exclusive_with_inline_fields(observation_harness, observations, field):
    client, _, _, reports, receipts, factories = observation_harness
    changes = {field: observations if field == "actual_progress" else TODAY.isoformat()}
    response = client.post(ROOT + "/reports", headers=HEADERS,
                           json=request_body(observation_id="observation-1", **changes))
    assert response.status_code == 422
    assert reports.calls == receipts.calls == factories == []


def test_stored_observation_report_provenance_saved_and_exported(observation_harness, observations):
    client, planning, risk, reports, receipts, _ = observation_harness
    saved = client.post(OBSERVATIONS, headers=HEADERS,
                        json=observation_body(observations, save_observation=True)).json()
    response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
        observation_id=saved["observation_id"], include_risks=True, save_report=True))
    assert response.status_code == 200
    report = response.json()
    assert report["metrics"]["actual_progress"] == 40
    assert report["coverage"]["actual_updated_on"] is None
    assert report["coverage"]["actual_observed_on"] == saved["observed_on"]
    provenance = report["actual_context"]["observation_receipt"]
    for name in ("observation_id", "request_id", "project_id", "source", "observed_on", "accepted_at", "submitted_by"):
        assert provenance[name] == saved[name]
    assert provenance["source_is_caller_claimed"] is True
    assert provenance["submitted_by_is_server_verified"] is True
    assert provenance["accepted_at_basis"] == "server_acceptance_not_observation_or_source_update"
    assert report["actual_context"]["source_payload"] == saved["actual_progress"]
    assert PRINCIPAL.subject not in json.dumps(risk.calls[0][1])
    assert "observation_receipt" not in risk.calls[0][1]["observed_actual_measurements"]["context"]
    assert planning.calls == ["P001", "P001"]
    assert len(receipts.receipts) == 1 and len(reports.reports) == 1
    path = ROOT + "/projects/P001/reports/" + report["report_id"]
    assert client.get(path, headers=HEADERS).json() == report
    assert client.get(path + "/coordinator-message", headers=HEADERS).json()["report"] == report
    assert_redacted(response)


def test_stored_receipt_acceptance_is_not_a_historical_source_date(observation_harness, schedule, observations):
    client, _, risk, _, receipts, _ = observation_harness
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    observations["updated_on"] = observations["tasks"][0]["updated_on"] = yesterday
    saved_response = client.post(OBSERVATIONS, headers=HEADERS, json=observation_body(
        observations, observed_on=yesterday, planning_message=message(schedule), received_on=yesterday,
        save_observation=True))
    assert saved_response.status_code == 200
    saved = saved_response.json()
    assert saved["accepted_at"][:10] == TODAY.isoformat()
    response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
        observation_id=saved["observation_id"], as_of=yesterday,
        planning_message=message(schedule), received_on=yesterday))
    assert response.status_code == 200
    assert response.json()["coverage"]["actual_updated_on"] == yesterday
    assert response.json()["coverage"]["actual_observed_on"] == yesterday
    assert response.json()["actual_context"]["observation_receipt"]["accepted_at"] == saved["accepted_at"]
    assert risk.calls == [] and len(receipts.receipts) == 1


def test_stored_observation_future_relative_to_report_is_422(observation_harness, schedule, observations):
    client, planning, risk, reports, receipts, factories = observation_harness
    saved = client.post(OBSERVATIONS, headers=HEADERS,
                        json=observation_body(observations, save_observation=True)).json()
    factories.clear()
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
        observation_id=saved["observation_id"], as_of=yesterday,
        planning_message=message(schedule), received_on=yesterday))
    assert response.status_code == 422 and receipts.calls[-1][0] == "get"
    assert factories == risk.calls == reports.calls == [] and planning.calls == ["P001"]


def test_stored_actuals_revalidated_against_chosen_baseline_before_risk_and_save(observation_harness,
                                                                            schedule, observations):
    client, planning, risk, reports, receipts, _ = observation_harness
    saved = client.post(OBSERVATIONS, headers=HEADERS,
                        json=observation_body(observations, save_observation=True)).json()
    schedule["schedule"][0]["task_id"] = "T002"
    schedule["analysis"]["critical_path"] = ["T002"]
    planning.plan = adapt_planning_schedule(schedule, TODAY)
    response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
        observation_id=saved["observation_id"], include_risks=True, save_report=True))
    assert response.status_code == 422
    assert risk.calls == reports.calls == [] and len(receipts.receipts) == 1


@pytest.mark.parametrize("include_risks", [False, True])
def test_stored_id_does_not_bypass_live_historical_guard(auth, schedule, include_risks):
    receipts = MemoryObservationRepository()
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    changes = {"planning_message": message(schedule), "received_on": yesterday} if include_risks else {}
    settings = MonitoringAPISettings(planning_base_url="https://planning.example.test",
                                     risk_base_url="https://risk.example.test")
    with TestClient(create_app(settings, authenticator=auth, observation_repository=receipts,
                              client_factory=lambda **kwargs: pytest.fail("Historical guard opened a client"))) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
            observation_id="missing", as_of=yesterday, include_risks=include_risks, **changes))
        assert response.status_code == 422 and receipts.calls == []


def test_stored_lookup_missing_wrong_id_corrupt_and_report_save_failure(observation_harness, observations):
    client, _, risk, reports, receipts, _ = observation_harness
    assert client.post(ROOT + "/reports", headers=HEADERS,
                       json=request_body(observation_id="missing")).status_code == 404
    saved = client.post(OBSERVATIONS, headers=HEADERS,
                        json=observation_body(observations, save_observation=True)).json()
    receipts.receipts["wrong-id"] = receipts.receipts[saved["observation_id"]]
    assert client.get(OBSERVATIONS + "/wrong-id", headers=HEADERS).status_code == 502
    assert client.post(ROOT + "/reports", headers=HEADERS,
                       json=request_body(observation_id="wrong-id")).status_code == 502
    corrupt = deepcopy(saved)
    corrupt["actual_progress"]["project_id"] = "P002"
    receipts.receipts["corrupt"] = corrupt
    assert client.post(ROOT + "/reports", headers=HEADERS,
                       json=request_body(observation_id="corrupt")).status_code == 502
    assert risk.calls == reports.calls == []
    reports.error = RuntimeError(DB_KEY)
    response = client.post(ROOT + "/reports", headers=HEADERS,
                           json=request_body(observation_id=saved["observation_id"], save_report=True))
    assert response.status_code == 502 and reports.reports == {}
    assert len(receipts.receipts) == 3
    assert_redacted(response)


def test_monitoring_post_body_is_bounded_before_factories(observation_harness):
    client, _, _, reports, receipts, factories = observation_harness
    for path in (OBSERVATIONS, ROOT + "/reports"):
        response = client.post(path, headers={**HEADERS, "Content-Type": "application/json"},
                               content=json.dumps({"oversized": "x" * 1_048_576}))
        assert response.status_code == 413
        assert_redacted(response)
    assert factories == reports.calls == receipts.calls == []


def test_real_observation_storage_and_report_clients_are_credential_isolated(auth, schedule, observations,
                                                                          risk_response):
    sent, observation_rows, report_rows = [], [], []

    def handler(request):
        sent.append(request)
        assert TOKEN not in request.content.decode()
        if request.url.host == "planning.example.test":
            assert request.headers["x-api-key"] == KEY
            assert "apikey" not in request.headers and "authorization" not in request.headers
            return httpx.Response(200, json=schedule)
        if request.url.host == "risk.example.test":
            assert all(name not in request.headers for name in ("x-api-key", "apikey", "authorization"))
            assert KEY not in request.content.decode() and DB_KEY not in request.content.decode()
            assert PRINCIPAL.subject not in request.content.decode()
            return httpx.Response(200, json=risk_response)
        assert request.url.host == "storage.example.test"
        assert request.headers["apikey"] == DB_KEY
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        if request.url.path == "/rest/v1/monitoring_observations":
            rows = observation_rows
        else:
            assert request.url.path == "/rest/v1/monitoring_reports"
            rows = report_rows
        if request.method == "POST":
            rows.append(json.loads(request.content))
            return httpx.Response(201)
        return httpx.Response(200, json=rows)

    factory, clients = mock_clients(handler)
    settings = MonitoringAPISettings(
        planning_base_url="https://planning.example.test", risk_base_url="https://risk.example.test",
        planning_api_key=SecretStr(KEY),
        storage=SupabaseMonitoringSettings(url="https://storage.example.test", secret_key=SecretStr(DB_KEY)),
    )
    with TestClient(create_app(settings, authenticator=auth, client_factory=factory)) as client:
        health = client.get(ROOT + "/health").json()
        assert health["capabilities"]["observations_storage_configured"] is True
        assert health["live_sources_verified"] is False and sent == []
        assert client.post(OBSERVATIONS, headers=HEADERS, json=observation_body(observations)).status_code == 200
        assert len(sent) == 1 and observation_rows == report_rows == []
        response = client.post(OBSERVATIONS, headers=HEADERS,
                               json=observation_body(observations, save_observation=True))
        assert response.status_code == 200
        saved = response.json()
        assert observation_rows[0]["receipt"] == saved
        generated = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
            observation_id=saved["observation_id"], include_risks=True, save_report=True))
        assert generated.status_code == 200
        assert report_rows[0]["report"] == generated.json()
        assert generated.json()["actual_context"]["observation_receipt"]["source"] == saved["source"]
        assert client.get(OBSERVATIONS + "/" + saved["observation_id"], headers=HEADERS).json() == saved
    assert all(client.is_closed for client in clients)
    assert len(clients) == len({id(client) for client in clients}) == 8
    assert len([request for request in sent if request.url.host == "risk.example.test"]) == 1
    assert sum(request.method == "POST" and request.url.host == "storage.example.test" for request in sent) == 2
    assert TOKEN not in json.dumps(observation_rows + report_rows)
    assert DB_KEY not in json.dumps(observation_rows + report_rows)


def test_shared_observation_database_client_never_uses_asgi_routes(monkeypatch, shared_upstreams,
                                                                 shared_settings, observations):
    service, calls = shared_upstreams
    constructor = httpx.AsyncClient
    clients, options_seen, rows, network_sent = [], [], [], []

    @service.post("/rest/v1/monitoring_observations")
    async def internal_storage():
        pytest.fail("Observation persistence used internal ASGI instead of its database client")

    def network(request):
        network_sent.append(request)
        assert request.url.host == "storage.example.test"
        assert request.url.path == "/rest/v1/monitoring_observations"
        assert request.headers["apikey"] == DB_KEY
        assert "x-api-key" not in request.headers and "authorization" not in request.headers
        if request.method == "POST":
            rows.append(json.loads(request.content))
            return httpx.Response(201)
        return httpx.Response(200, json=rows)

    def factory(**options):
        options_seen.append(dict(options))
        if "transport" not in options:
            options["transport"] = httpx.MockTransport(network)
        client = constructor(**options)
        clients.append(client)
        return client

    monkeypatch.setattr("app.agents.monitoring.api.httpx.AsyncClient", factory)
    settings = replace(shared_settings, storage=SupabaseMonitoringSettings(
        url="https://storage.example.test", secret_key=SecretStr(DB_KEY)))
    service.include_router(create_shared_router(service, settings))
    with TestClient(service) as client:
        response = client.post(OBSERVATIONS, headers=HEADERS,
                               json=observation_body(observations, save_observation=True))
        assert response.status_code == 200
        assert calls == []
        assert len(network_sent) == 1
        assert client.get(OBSERVATIONS, headers=HEADERS).json() == [response.json()]
    assert len(clients) == 2 and all(client.is_closed for client in clients)
    assert all("base_url" not in option and "transport" not in option for option in options_seen)
    assert all(options["trust_env"] is False for options in options_seen)


def test_observation_repository_configuration_is_independent_of_reports(auth):
    receipts = MemoryObservationRepository()
    service = FastAPI()
    service.include_router(create_router(MonitoringAPISettings(), authenticator=auth,
                                        observation_repository=receipts))
    with TestClient(service) as client:
        capabilities = client.get(ROOT + "/health").json()["capabilities"]
        assert capabilities["observations_storage_configured"] is True
        assert capabilities["storage_configured"] is False
        assert receipts.calls == []
        assert client.get(OBSERVATIONS + "?limit=100", headers=HEADERS).json() == []
        assert receipts.calls[-1][-1] == 100


def test_stored_receipt_submitter_is_not_the_generating_report_author(auth, schedule, observations):
    receipts, reports = MemoryObservationRepository(), MemoryRepository()
    with TestClient(create_app(MonitoringAPISettings(), authenticator=auth,
                              planning_provider_factory=lambda client: OfflinePlanning(schedule),
                              observation_repository=receipts)) as client:
        saved_response = client.post(OBSERVATIONS, headers=HEADERS,
                                    json=observation_body(observations, save_observation=True))
        assert saved_response.status_code == 200
        saved = saved_response.json()
    viewer = MonitoringPrincipal(subject="another-manager", project_ids=frozenset({"P001"}),
                                 permissions=frozenset({"monitoring:read", "monitoring:generate"}))
    with TestClient(create_app(MonitoringAPISettings(), authenticator=MonitoringTokenAuthenticator({TOKEN: viewer}),
                              planning_provider_factory=lambda client: OfflinePlanning(schedule),
                              observation_repository=receipts, repository=reports)) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
            observation_id=saved["observation_id"], request_id="new-report-request", save_report=True))
        assert response.status_code == 200
        report = response.json()
        assert report["requested_by"] == viewer.subject and report["request_id"] == "new-report-request"
        assert report["actual_context"]["observation_receipt"]["submitted_by"] == PRINCIPAL.subject
        assert report["actual_context"]["observation_receipt"]["request_id"] == "observation-request"
        assert reports.reports[report["report_id"]].requested_by == viewer.subject


@pytest.mark.parametrize("actual_source", ["stored", "inline", "absent"])
def test_future_as_of_rejected_before_all_factories_and_storage(monkeypatch, auth, schedule,
                                                               observations, actual_source):
    receipts, reports = MemoryObservationRepository(), MemoryRepository()

    def forbidden(*args, **kwargs):
        pytest.fail("Future report reached a local/provider/client factory")

    monkeypatch.setattr("app.agents.monitoring.api.PlanningMessageProvider", forbidden)
    monkeypatch.setattr("app.agents.monitoring.api.ActualProgressProvider", forbidden)
    changes = {}
    if actual_source != "absent":
        changes.update(planning_message=message(schedule), received_on=TODAY.isoformat())
    if actual_source == "stored":
        changes["observation_id"] = "must-not-be-looked-up"
    elif actual_source == "inline":
        changes.update(actual_progress=observations, observed_on=TODAY.isoformat())
    service = create_app(
        MonitoringAPISettings(), authenticator=auth, planning_provider_factory=forbidden,
        risk_provider_factory=forbidden, client_factory=forbidden,
        observation_repository=receipts, repository=reports,
    )
    with TestClient(service) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
            as_of=(TODAY + timedelta(days=1)).isoformat(), include_risks=True, save_report=True, **changes))
    assert response.status_code == 422
    assert response.json() == {"detail": "Monitoring report as_of cannot be in the future"}
    assert receipts.calls == reports.calls == []
    assert_redacted(response)


@pytest.mark.parametrize("permissions", [["monitoring:read"], ["monitoring:generate"]])
def test_future_stored_report_still_authorizes_first(permissions):
    principal = MonitoringPrincipal(subject="limited-user", project_ids=frozenset({"P001"}),
                                    permissions=frozenset(permissions))
    receipts, reports = MemoryObservationRepository(), MemoryRepository()
    service = create_app(
        MonitoringAPISettings(), authenticator=MonitoringTokenAuthenticator({TOKEN: principal}),
        observation_repository=receipts, repository=reports,
    )
    with TestClient(service) as client:
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(
            observation_id="must-not-be-looked-up", as_of=(TODAY + timedelta(days=1)).isoformat()))
    assert response.status_code == 403
    assert receipts.calls == reports.calls == []
    assert_redacted(response)


@pytest.mark.parametrize("actual_source", ["stored", "inline", "absent"])
def test_report_generation_persistence_and_reader_exports_redact_credentials(observation_harness,
                                                                           observations, actual_source):
    client, planning, risk, reports, _, _ = observation_harness
    marker = "synthetic-report-credential"
    planning.plan.context["metadata"] = {
        "accessToken": marker, "note": "Vendor review is pending",
        "records": json.dumps([{"clientSecret": marker, "document_id": "review-1"}]),
    }
    risk.snapshot.evidence_used = [{"metadata": {"Authorization": marker, "page": 3}}]
    changes = {}
    if actual_source == "stored":
        saved_response = client.post(OBSERVATIONS, headers=HEADERS,
                                    json=observation_body(observations, save_observation=True))
        assert saved_response.status_code == 200
        changes["observation_id"] = saved_response.json()["observation_id"]
    elif actual_source == "inline":
        observations["metadata"] = {"api_key": marker, "description": "Recorded execution"}
        changes.update(actual_progress=observations, observed_on=TODAY.isoformat())
    originals = deepcopy((planning.plan.context, observations, risk.snapshot.evidence_used))
    response = client.post(ROOT + "/reports", headers=HEADERS,
                           json=request_body(include_risks=True, save_report=True, **changes))
    assert response.status_code == 200
    assert_redacted(response, marker)
    report = response.json()
    assert report["planning_context"]["metadata"]["note"] == "Vendor review is pending"
    assert json.loads(report["planning_context"]["metadata"]["records"]) == [{"document_id": "review-1"}]
    assert report["risk_evidence_used"] == [{"metadata": {"page": 3}}]
    if actual_source == "stored":
        assert report["actual_context"]["observation_receipt"]["observation_id"] == changes["observation_id"]
    elif actual_source == "inline":
        assert report["actual_context"]["source_payload"]["metadata"] == {"description": "Recorded execution"}
    assert reports.reports[report["report_id"]].model_dump(mode="json") == report
    assert (planning.plan.context, observations, risk.snapshot.evidence_used) == originals

    reader = MonitoringPrincipal(subject="report-reader", project_ids=frozenset({"P001"}),
                                 permissions=frozenset({"monitoring:read"}))
    service = create_app(MonitoringAPISettings(),
                         authenticator=MonitoringTokenAuthenticator({TOKEN: reader}), repository=reports)
    path = ROOT + "/projects/P001/reports"
    with TestClient(service) as reader_client:
        for suffix in ("", "/" + report["report_id"], "/" + report["report_id"] + "/coordinator-message"):
            fetched = reader_client.get(path + suffix, headers=HEADERS)
            assert fetched.status_code == 200
            assert_redacted(fetched, marker)
            data = fetched.json()
            returned = data[0] if suffix == "" else data["report"] if suffix.endswith("coordinator-message") else data
            assert returned == report


@pytest.mark.parametrize("as_dict", [False, True])
def test_legacy_report_reader_paths_redact_credentials_without_rewriting_storage(observation_harness, as_dict):
    client, _, _, reports, _, _ = observation_harness
    response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body(save_report=True))
    assert response.status_code == 200
    payload = response.json()
    marker = "synthetic-legacy-credential"
    payload["planning_context"]["metadata"] = {"APIKey": marker, "page": 3}
    payload["actual_context"] = {"source_payload": {
        "accessToken": marker, "project_id": "P001", "description": "Historical evidence",
    }}
    payload["risk_evidence_used"] = [{"clientSecret": marker, "document_id": "review-1"}]
    payload["risk_knowledge_sources"] = [json.dumps(json.dumps({"Cookie": marker, "source": "review.pdf"}))]
    original = deepcopy(payload)
    report_id = payload["report_id"]
    legacy = reports.reports[report_id]
    if as_dict:
        reports.reports[report_id] = payload
    else:
        # Simulate an already instantiated legacy model, bypassing current validation.
        for field in ("planning_context", "actual_context", "risk_evidence_used", "risk_knowledge_sources"):
            setattr(legacy, field, payload[field])
    reader = MonitoringPrincipal(subject="report-reader", project_ids=frozenset({"P001"}),
                                 permissions=frozenset({"monitoring:read"}))
    service = create_app(MonitoringAPISettings(),
                         authenticator=MonitoringTokenAuthenticator({TOKEN: reader}), repository=reports)
    path = ROOT + "/projects/P001/reports"
    with TestClient(service) as reader_client:
        for suffix in ("", "/" + report_id, "/" + report_id + "/coordinator-message"):
            fetched = reader_client.get(path + suffix, headers=HEADERS)
            assert fetched.status_code == 200
            assert_redacted(fetched, marker)
            data = fetched.json()
            returned = data[0] if suffix == "" else data["report"] if suffix.endswith("coordinator-message") else data
            assert returned["planning_context"]["metadata"] == {"page": 3}
            assert returned["actual_context"]["source_payload"] == {
                "project_id": "P001", "description": "Historical evidence",
            }
            assert returned["risk_evidence_used"] == [{"document_id": "review-1"}]
            assert json.loads(json.loads(returned["risk_knowledge_sources"][0])) == {"source": "review.pdf"}
    assert payload == original
    if not as_dict:
        assert legacy.planning_context == original["planning_context"]
        assert legacy.actual_context == original["actual_context"]
