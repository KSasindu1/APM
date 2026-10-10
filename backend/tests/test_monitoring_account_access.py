"""Guarded shared Monitoring contracts using real JWT verification and synthetic stores."""
from __future__ import annotations

import builtins
import importlib.abc
import io
import json
import os
import socket
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_planning_ownership_api import (
    ADMIN, BACKEND, KEY, SECRET_ERROR, USER_A, USER_B, AccountStoreUnavailable,
    FakeAccount, FakeAccountStore, FakeRepository, OfflineImports, deny_external,
    load_source, protected_open, stub_module,
)


class MonitoringOfflineImports(OfflineImports):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app.agents" or fullname.startswith("app.agents.monitoring"):
            return None
        return super().find_spec(fullname, path, target)


@contextmanager
def guards():
    with ExitStack() as stack:
        stack.enter_context(patch.object(builtins, "open", protected_open(builtins.open)))
        stack.enter_context(patch.object(io, "open", protected_open(io.open)))
        stack.enter_context(patch.object(sys, "meta_path", [MonitoringOfflineImports(), *sys.meta_path]))
        stack.enter_context(patch.object(socket, "create_connection", deny_external))
        stack.enter_context(patch.object(socket, "getaddrinfo", deny_external))
        yield


with guards():
    import dotenv
    import dotenv.main
    import httpx
    import jwt
    import pytest
    import pydantic_settings.sources.providers.dotenv as settings_dotenv
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from pydantic_settings.sources.providers.env import EnvSettingsSource


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    with guards():
        for module in (dotenv, dotenv.main):
            for name in ("load_dotenv", "dotenv_values", "find_dotenv"):
                monkeypatch.setattr(module, name, deny_external)
        monkeypatch.setattr(dotenv.main.DotEnv, "_get_stream", deny_external)
        monkeypatch.setattr(settings_dotenv, "read_env_file", deny_external)
        for name in ("_read_env_files", "_read_env_file", "_static_read_env_file"):
            monkeypatch.setattr(settings_dotenv.DotEnvSettingsSource, name, deny_external)
        monkeypatch.setattr(EnvSettingsSource, "_load_env_vars", deny_external)
        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_external)
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_external)
        yield


class Store:
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.error = None
        self.after = None

    async def run(self, name, project_id, principal, identifier=None, value=None, limit=20):
        # Exercise the genuine server-side scope, not a lookalike namespace.
        assert type(principal).__name__ == "MonitoringPrincipal"
        assert principal.project_ids == frozenset({project_id})
        assert principal.permissions == frozenset({"monitoring:read", "monitoring:generate"})
        self.calls.append((name, project_id, principal.subject))
        if self.error:
            raise self.error
        if name == "save":
            self.rows[identifier] = value.model_copy(deep=True)
            result = None
        elif name == "get":
            result = self.rows.get(identifier)
        else:
            result = [row for row in self.rows.values() if row.project_id == project_id][:limit]
        if self.after:
            self.after()
        return result

    async def save(self, value, principal):
        identifier = getattr(value, "report_id", None) or value.observation_id
        return await self.run("save", value.project_id, principal, identifier, value)

    async def get(self, project_id, identifier, principal):
        return await self.run("get", project_id, principal, identifier)

    async def list(self, project_id, principal, limit=20):
        return await self.run("list", project_id, principal, limit=limit)


@pytest.fixture
def api(monkeypatch):
    for name in ("app", "app.core", "app.services", "app.repository", "app.model"):
        stub_module(monkeypatch, name, __path__=[])
    stub_module(monkeypatch, "app.agents", __path__=[str(BACKEND / "app/agents")])
    stub_module(monkeypatch, "app.agents.monitoring", __path__=[str(BACKEND / "app/agents/monitoring")])
    settings = SimpleNamespace(
        admin_username="管理者", admin_password="synthetic-bootstrap-password", admin_password_hash=None,
        jwt_secret_key="synthetic-monitoring-signing-material-" + "x" * 64, jwt_algorithm="HS256",
        jwt_issuer="offline-monitoring", jwt_audience="offline-admin", account_jwt_audience="offline-accounts",
        access_token_expire_minutes=10, planning_api_key=KEY,
    )
    stub_module(monkeypatch, "app.core.config", settings=settings, get_settings=lambda: settings)
    store = FakeAccountStore()
    stub_module(monkeypatch, "app.services.account_store", Account=FakeAccount,
                AccountStoreUnavailable=AccountStoreUnavailable, get_account_store=lambda: store)
    security = load_source(monkeypatch, "app.core.security", "app/core/security.py")
    accounts = load_source(monkeypatch, "app.core.account_security", "app/core/account_security.py")
    access = load_source(monkeypatch, "app.core.project_access", "app/core/project_access.py")
    models = load_source(monkeypatch, "app.model.planning", "app/model/planning.py")
    repository = FakeRepository(models)
    repository.exists = lambda project_id, **scope: repository.get(project_id, **scope) is not None
    stub_module(monkeypatch, "app.repository.planning_repository", PlanningRepository=lambda: repository)
    # Load the real module without import-time app configuration or ambient credentials.
    with patch.object(dotenv, "dotenv_values", lambda *args, **kwargs: {}), patch.dict(os.environ, {}, clear=True):
        module = load_source(monkeypatch, "app.agents.monitoring.api", "app/agents/monitoring/api.py")
    # Ensure a previous test run's adapter cannot retain another api module's exception class.
    load_source(monkeypatch, "app.agents.monitoring.account_access", "app/agents/monitoring/account_access.py")
    reports, observations = Store(), Store()
    application = FastAPI()
    risk_calls = []
    after_risk = []

    @application.post("/api/risk/analyse")
    async def risk(payload: dict, request: Request):
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers and "apikey" not in request.headers
        risk_calls.append(payload)
        if after_risk:
            after_risk[0]()
        return {"project_id": payload["project_id"], "risks": [], "summary": "Synthetic risk result",
                "evidence_used": [], "knowledge_sources": []}

    # Inject synthetic persistence while exercising create_shared_router's wiring.
    real_create = module.create_router

    def create(*args, **kwargs):
        return real_create(*args, repository=reports, observation_repository=observations, **kwargs)

    monkeypatch.setattr(module, "create_router", create)
    application.include_router(module.create_shared_router(application, module.MonitoringAPISettings()))
    with TestClient(application) as client:
        yield SimpleNamespace(client=client, module=module, security=security, accounts=accounts,
                              access=access, store=store, settings=settings, repository=repository,
                              reports=reports, observations=observations, risk_calls=risk_calls,
                              after_risk=after_risk)


ROOT = "/api/monitoring"
TODAY = date.today().isoformat()


def headers(api, username="user_a"):
    return {"Authorization": "Bearer " + api.accounts.create_account_token(api.store.rows[username])[0]}


def admin_headers(api, kind):
    if kind == "managed":
        return headers(api, "managed_admin")
    return {"Authorization": "Bearer " + api.security.create_access_token(api.settings.admin_username, "admin")}


def body(project_id="project-a", **changes):
    return {"project_id": project_id, "request_id": "synthetic-request", "as_of": TODAY,
            "include_risks": False, **changes}


def envelope(api, project_id="project-a"):
    return {"sender": "planning_agent", "receiver": "project_monitor_report_agent", "action": "SCHEDULE_CREATED",
            "request_id": "synthetic-planning", "status": "success",
            "data": api.repository.rows[project_id][1].model_dump(mode="json")}


def intake(api, project_id="project-a", **changes):
    return {"request_id": "synthetic-intake", "source": "manual", "observed_on": TODAY,
            "actual_progress": {"project_id": project_id, "tasks": [{"task_id": "T001", "status": "IN_PROGRESS",
                                                                       "progress_percentage": 40}]}, **changes}


def safe(response, status):
    assert response.status_code == status, response.text
    assert SECRET_ERROR not in response.text
    assert response.headers["cache-control"] == "no-store"
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    return response.json()


OPERATIONS = ["baseline", "reports", "report", "coordinator", "observations", "observation",
              "generate", "imported-generate", "intake", "imported-intake", "stored-generate"]


def request(api, operation, project_id, auth):
    path = ROOT + "/projects/" + project_id
    if operation in ("generate", "imported-generate", "stored-generate"):
        changes = {}
        if operation == "imported-generate":
            changes = {"planning_message": envelope(api), "received_on": TODAY}
        if operation == "stored-generate":
            changes = {"observation_id": "missing-observation"}
        return api.client.post(ROOT + "/reports", headers=auth, json=body(project_id, **changes))
    if operation in ("intake", "imported-intake"):
        changes = {"planning_message": envelope(api), "received_on": TODAY} if operation == "imported-intake" else {}
        return api.client.post(path + "/observations", headers=auth, json=intake(api, project_id, **changes))
    suffix = {"baseline": "/baseline", "reports": "/reports", "report": "/reports/missing-report",
              "coordinator": "/reports/missing-report/coordinator-message", "observations": "/observations",
              "observation": "/observations/missing-observation"}[operation]
    return api.client.get(path + suffix, headers=auth)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("project_id", ["project-b", "unassigned", "missing"])
def test_foreign_unassigned_missing_are_same_404_before_sources_and_storage(api, monkeypatch, operation, project_id):
    monkeypatch.setattr(api.module.PlanningMessageProvider, "get_plan", deny_external)
    monkeypatch.setattr(api.module.ActualProgressProvider, "get_actual", deny_external)
    safe(request(api, operation, project_id, headers(api)), 404)
    assert api.risk_calls == api.reports.calls == api.observations.calls == []
    assert all(call[0] != "get_all" for call in api.repository.calls)
    assert all(call[-1] == USER_A for call in api.repository.calls)


def test_health_and_session_use_account_scope_not_monitoring_configuration(api):
    health = safe(api.client.get(ROOT + "/health"), 200)
    assert health["status"] == "ready" and health["capabilities"]["auth_configured"] is True
    assert api.repository.calls == []
    session = safe(api.client.get(ROOT + "/session", headers=headers(api)), 200)
    assert session == {"subject": USER_A, "project_ids": ["project-a"],
                       "permissions": ["monitoring:generate", "monitoring:read"]}
    assert api.repository.calls == [("get_all", USER_A), ("get", "project-a", USER_A)]


@pytest.mark.parametrize("kind", ["user", "managed", "bootstrap"])
def test_incompatible_planning_ids_do_not_block_other_accessible_projects(api, kind):
    for project_id, owner in [("legacy project", USER_A), ("別の案件", USER_B)]:
        source = api.repository.rows["project-a"][1]
        api.repository.rows[project_id] = (owner, source.model_copy(update={"project_id": project_id}))
    auth = headers(api) if kind == "user" else admin_headers(api, kind)
    session = safe(api.client.get(ROOT + "/session", headers=auth), 200)
    assert session["project_ids"] == (["project-a"] if kind == "user" else ["project-a", "project-b", "unassigned"])
    assert session["unsupported_project_count"] == (1 if kind == "user" else 2)
    safe(request(api, "baseline", "project-a", auth), 200)


@pytest.mark.parametrize("kind,subject", [("managed", ADMIN), ("bootstrap", "bootstrap-admin")])
def test_admins_access_all_existing_including_unassigned(api, kind, subject):
    auth = admin_headers(api, kind)
    assert safe(api.client.get(ROOT + "/session", headers=auth), 200) == {
        "subject": subject, "project_ids": ["project-a", "project-b", "unassigned"],
        "permissions": ["monitoring:generate", "monitoring:read"]}
    for project_id in ("project-a", "project-b", "unassigned"):
        safe(request(api, "baseline", project_id, auth), 200)
        report = safe(api.client.post(ROOT + "/reports", headers=auth, json=body(project_id, save_report=True)), 200)
        assert report["requested_by"] == subject
    safe(request(api, "baseline", "missing", auth), 404)
    assert all(call[-1] is None for call in api.repository.calls)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("auth", [{}, {"X-API-Key": KEY}, {"Authorization": "Bearer invalid", "X-API-Key": KEY}])
def test_authentication_is_required_before_project_and_all_other_io(api, operation, auth):
    safe(request(api, operation, "project-a", auth), 401)
    assert api.repository.calls == api.reports.calls == api.observations.calls == api.risk_calls == []


@pytest.mark.parametrize("failure", ["expired", "signature", "purpose", "inactive", "role", "deleted", "id"])
def test_real_signed_account_tokens_require_fresh_claims_and_database_identity(api, failure):
    auth = headers(api)
    token = auth["Authorization"].split()[1]
    if failure in ("expired", "signature", "purpose"):
        payload = jwt.decode(token, options={"verify_signature": False})
        if failure == "expired":
            payload["iat"] -= 1000
            payload["exp"] -= 1000
        if failure == "purpose":
            payload["token_use"] = "admin_access"
        token = jwt.encode(payload, "wrong-synthetic-signing-material-" + "y" * 64 if failure == "signature"
                           else api.settings.jwt_secret_key, algorithm="HS256")
        auth = {"Authorization": "Bearer " + token, "X-API-Key": KEY}
    elif failure == "deleted":
        del api.store.rows["user_a"]
    else:
        changes = {"inactive": {"active": False}, "role": {"role": "admin"}, "id": {"user_id": USER_B}}[failure]
        api.store.rows["user_a"] = replace(api.store.rows["user_a"], **changes)
    safe(api.client.get(ROOT + "/session", headers=auth), 401)
    assert api.repository.calls == []


def test_legacy_monitoring_mapping_is_not_a_shared_fallback_and_service_actor_is_rejected(api, monkeypatch):
    token = "synthetic-legacy-monitoring-token-" + "x" * 40
    application = FastAPI()
    settings = api.module.MonitoringAPISettings(auth_identities=json.dumps({token: {
        "subject": "legacy", "project_ids": ["project-a"],
        "permissions": ["monitoring:read", "monitoring:generate"],
    }}))
    application.include_router(api.module.create_shared_router(application, settings))
    with TestClient(application) as client:
        safe(client.get(ROOT + "/session", headers={"Authorization": "Bearer " + token}), 401)
    monkeypatch.setattr(api.access, "require_project_actor", lambda **kwargs: api.access.ProjectActor("service", None, "service"))
    safe(api.client.get(ROOT + "/session", headers=headers(api)), 401)
    assert api.repository.calls == []


def test_owner_can_save_read_and_export_reports_and_observations(api):
    auth = headers(api)
    receipt = safe(api.client.post(ROOT + "/projects/project-a/observations", headers=auth,
                                   json=intake(api, save_observation=True)), 200)
    assert receipt["submitted_by"] == USER_A and receipt["persisted"]
    report = safe(api.client.post(ROOT + "/reports", headers=auth,
                                  json=body(observation_id=receipt["observation_id"], save_report=True)), 200)
    assert report["requested_by"] == USER_A
    assert report["actual_context"]["observation_receipt"]["submitted_by"] == USER_A
    for name, value, identifier in (("reports", report, report["report_id"]),
                                     ("observations", receipt, receipt["observation_id"])):
        path = ROOT + "/projects/project-a/" + name
        assert safe(api.client.get(path, headers=auth), 200) == [value]
        assert safe(api.client.get(path + "/" + identifier, headers=auth), 200) == value
        safe(api.client.get(path + "/" + identifier, headers=headers(api, "user_b")), 404)
    path = ROOT + "/projects/project-a/reports/" + report["report_id"] + "/coordinator-message"
    assert safe(api.client.get(path, headers=auth), 200)["report"] == report
    assert all(call[0] != "get_all" for call in api.repository.calls)


@pytest.mark.parametrize("operation", ["report", "coordinator", "observation", "stored-generate"])
def test_missing_history_is_404_for_owned_project(api, operation):
    safe(request(api, operation, "project-a", headers(api)), 404)


@pytest.mark.parametrize("operation", ["reports", "report", "coordinator", "observations", "observation", "stored-generate"])
@pytest.mark.parametrize("error", [RuntimeError(SECRET_ERROR), TimeoutError(SECRET_ERROR)])
def test_shared_storage_outages_are_safe_503(api, operation, error):
    api.reports.error = api.observations.error = error
    safe(request(api, operation, "project-a", headers(api)), 503)


@pytest.mark.parametrize("operation", ["baseline", "generate", "imported-generate", "intake", "reports", "observations"])
def test_project_store_unavailable_is_503_before_sources(api, operation):
    api.repository.failures["get"] = RuntimeError(SECRET_ERROR)
    safe(request(api, operation, "project-a", headers(api)), 503)
    assert api.risk_calls == api.reports.calls == api.observations.calls == []


def test_account_store_unavailable_is_safe_503(api):
    auth = headers(api)
    api.store.fail = True
    safe(api.client.get(ROOT + "/session", headers=auth), 503)
    assert api.repository.calls == []


@pytest.mark.parametrize("save", [False, True])
@pytest.mark.parametrize("change,status", [("owner", 404), ("deleted", 404), ("inactive", 401), ("role", 401), ("expired", 401)])
def test_generation_rechecks_before_save_and_release_after_slow_work(api, monkeypatch, save, change, status):
    original = api.module.MonitoringAgent.generate

    async def generate(agent, request, principal):
        assert agent.repository is None
        report = await original(agent, request, principal)
        if change == "owner":
            api.repository.assign_owner("project-a", USER_B)
        elif change == "deleted":
            del api.repository.rows["project-a"]
        elif change == "expired":
            monkeypatch.setattr(api.accounts.time, "time", lambda: 9_000_000_000)
        else:
            api.store.rows["user_a"] = replace(api.store.rows["user_a"], **({"active": False} if change == "inactive" else {"role": "admin"}))
        return report

    monkeypatch.setattr(api.module.MonitoringAgent, "generate", generate)
    safe(api.client.post(ROOT + "/reports", headers=headers(api), json=body(save_report=save)), status)
    assert api.reports.rows == {} and api.reports.calls == []


@pytest.mark.parametrize("save", [False, True])
def test_intake_rechecks_after_imported_planning_before_save_or_release(api, monkeypatch, save):
    original = api.module.PlanningMessageProvider.get_plan

    async def get_plan(provider, project_id):
        plan = await original(provider, project_id)
        api.repository.assign_owner(project_id, USER_B)
        return plan

    monkeypatch.setattr(api.module.PlanningMessageProvider, "get_plan", get_plan)
    safe(api.client.post(ROOT + "/projects/project-a/observations", headers=headers(api),
                         json=intake(api, planning_message=envelope(api), received_on=TODAY, save_observation=save)), 404)
    assert api.observations.rows == {} and api.observations.calls == []


@pytest.mark.parametrize("operation", ["reports", "report", "coordinator", "observations", "observation", "stored-generate"])
def test_history_rechecks_ownership_after_storage_before_release_or_use(api, operation):
    auth = headers(api)
    receipt = safe(api.client.post(ROOT + "/projects/project-a/observations", headers=auth,
                                   json=intake(api, save_observation=True)), 200)
    report = safe(api.client.post(ROOT + "/reports", headers=auth, json=body(save_report=True)), 200)
    api.reports.rows = {"missing-report": api.module.MonitoringReport.model_validate(report).model_copy(
        update={"report_id": "missing-report"})}
    api.observations.rows = {"missing-observation": api.module.ObservationReceipt.model_validate(receipt).model_copy(
        update={"observation_id": "missing-observation"})}
    api.reports.after = api.observations.after = lambda: api.repository.assign_owner("project-a", USER_B)
    safe(request(api, operation, "project-a", auth), 404)
    assert api.risk_calls == []


def test_baseline_rechecks_after_slow_source_before_release(api, monkeypatch):
    from app.agents.monitoring.account_access import MonitoringAccountAccess
    original = MonitoringAccountAccess.get_plan

    async def get_plan(access, project_id):
        result = await original(access, project_id)
        api.repository.assign_owner(project_id, USER_B)
        return result

    monkeypatch.setattr(MonitoringAccountAccess, "get_plan", get_plan)
    safe(request(api, "baseline", "project-a", headers(api)), 404)


@pytest.mark.parametrize("save", [False, True])
def test_reassignment_during_risk_blocks_report_persistence_and_release(api, save):
    api.after_risk.append(lambda: api.repository.assign_owner("project-a", USER_B))
    safe(api.client.post(ROOT + "/reports", headers=headers(api),
                         json=body(include_risks=True, save_report=save)), 404)
    assert len(api.risk_calls) == 1
    assert api.reports.rows == {} and api.reports.calls == []


def test_risk_receives_project_context_without_browser_or_service_credentials(api):
    auth = headers(api)
    safe(api.client.post(ROOT + "/reports", headers=auth, json=body(include_risks=True)), 200)
    assert len(api.risk_calls) == 1
    context = json.dumps(api.risk_calls[0])
    assert auth["Authorization"].split()[1] not in context and KEY not in context
    assert USER_A not in context


@pytest.mark.parametrize("save", [False, True])
def test_reassignment_during_imported_planning_never_reaches_risk(api, monkeypatch, save):
    original = api.module.PlanningMessageProvider.get_plan

    async def get_plan(provider, project_id):
        plan = await original(provider, project_id)
        api.repository.assign_owner(project_id, USER_B)
        return plan

    monkeypatch.setattr(api.module.PlanningMessageProvider, "get_plan", get_plan)
    safe(api.client.post(ROOT + "/reports", headers=headers(api), json=body(
        planning_message=envelope(api), received_on=TODAY, include_risks=True, save_report=save)), 404)
    assert api.risk_calls == api.reports.calls == []


@pytest.mark.parametrize("kind", ["report", "observation"])
def test_explicit_save_outage_is_503_without_success_receipt_or_report(api, kind):
    api.reports.error = api.observations.error = RuntimeError(SECRET_ERROR)
    auth = headers(api)
    if kind == "report":
        response = api.client.post(ROOT + "/reports", headers=auth, json=body(save_report=True))
    else:
        response = api.client.post(ROOT + "/projects/project-a/observations", headers=auth,
                                   json=intake(api, save_observation=True))
    safe(response, 503)
    assert api.reports.rows == api.observations.rows == {}


@pytest.mark.parametrize("kind", ["report", "observation"])
def test_reassignment_during_save_blocks_result_release_even_if_write_completed(api, kind):
    api.reports.after = api.observations.after = lambda: api.repository.assign_owner("project-a", USER_B)
    if kind == "report":
        response = api.client.post(ROOT + "/reports", headers=headers(api), json=body(save_report=True))
    else:
        response = api.client.post(ROOT + "/projects/project-a/observations", headers=headers(api),
                                   json=intake(api, save_observation=True))
    safe(response, 404)
    # This is a fresh pre/post-save gate, not a transaction across databases.
    assert len(api.reports.rows) + len(api.observations.rows) == 1


@pytest.mark.parametrize("kind", ["report", "observation"])
def test_corrupt_history_cannot_escape_project_or_identifier_binding(api, kind):
    auth = headers(api)
    if kind == "report":
        value = safe(api.client.post(ROOT + "/reports", headers=auth, json=body(save_report=True)), 200)
        model, store, collection, identifier = api.module.MonitoringReport, api.reports, "reports", "report_id"
    else:
        value = safe(api.client.post(ROOT + "/projects/project-a/observations", headers=auth,
                                     json=intake(api, save_observation=True)), 200)
        model, store, collection, identifier = api.module.ObservationReceipt, api.observations, "observations", "observation_id"
    for changes in ({"project_id": "project-b"}, {identifier: "wrong-id"}):
        store.rows[value[identifier]] = model.model_validate(value).model_copy(update=changes)
        safe(api.client.get(ROOT + "/projects/project-a/" + collection + "/" + value[identifier], headers=auth), 502)


def test_valid_user_bearer_with_service_key_stays_owner_scoped(api):
    auth = {**headers(api), "X-API-Key": KEY}
    safe(request(api, "baseline", "project-a", auth), 200)
    safe(request(api, "baseline", "project-b", auth), 404)
    assert all(call[-1] == USER_A for call in api.repository.calls)


@pytest.mark.parametrize("operation", ["baseline", "generate", "session"])
def test_planning_repository_is_lazy_and_reused_only_within_one_request(api, monkeypatch, operation):
    instances = []

    class RequestRepository:
        def __init__(self):
            self.queries = []
            instances.append(self)

        def __getattr__(self, method):
            def query(*args, **kwargs):
                self.queries.append((method, args, kwargs))
                return getattr(api.repository, method)(*args, **kwargs)
            return query

    monkeypatch.setattr(sys.modules["app.repository.planning_repository"], "PlanningRepository", RequestRepository)
    safe(api.client.get(ROOT + "/health"), 200)
    safe(api.client.get(ROOT + "/session"), 401)
    assert instances == []

    auth = admin_headers(api, "managed") if operation == "session" else headers(api)
    for index in range(2):
        account_queries_before = len(api.store.calls)
        if operation == "session":
            response = api.client.get(ROOT + "/session", headers=auth)
        else:
            response = request(api, operation, "project-a", auth)
        safe(response, 200)
        assert len(instances) == index + 1
        assert len(instances[index].queries) >= 2
        assert len(api.store.calls) - account_queries_before >= len(instances[index].queries)
        if operation == "session":
            assert [method for method, _, _ in instances[index].queries] == ["get_all", "exists", "exists", "exists"]
    assert instances[0] is not instances[1]


def test_session_rechecks_projects_reassigned_during_listing(api, monkeypatch):
    original = api.repository.get_all

    def get_all(**scope):
        result = original(**scope)
        api.repository.assign_owner("project-a", USER_B)
        return result

    monkeypatch.setattr(api.repository, "get_all", get_all)
    session = safe(api.client.get(ROOT + "/session", headers=headers(api)), 200)
    assert session["project_ids"] == [] and session["subject"] == USER_A


@pytest.mark.parametrize("changes", [{"active": False}, {"role": "user"}])
def test_managed_admin_token_is_rejected_after_database_role_or_activity_changes(api, changes):
    auth = admin_headers(api, "managed")
    api.store.rows["managed_admin"] = replace(api.store.rows["managed_admin"], **changes)
    safe(api.client.get(ROOT + "/session", headers=auth), 401)
    assert api.repository.calls == []


@pytest.mark.parametrize("kind", ["reports", "observations"])
def test_history_refreshes_account_before_releasing_results(api, kind):
    store = api.reports if kind == "reports" else api.observations
    store.after = lambda: api.store.rows.update(user_a=replace(api.store.rows["user_a"], active=False))
    safe(api.client.get(ROOT + "/projects/project-a/" + kind, headers=headers(api)), 401)


def test_incomplete_user_actor_cannot_become_unscoped(api, monkeypatch):
    monkeypatch.setattr(api.access, "require_project_actor", lambda **kwargs: api.access.ProjectActor("user", None, "user_a"))
    safe(request(api, "baseline", "project-a", headers(api)), 401)
    assert api.repository.calls == []


def test_guards_block_operator_files_storage_and_live_imports():
    with pytest.raises(AssertionError):
        Path(BACKEND / ".env").read_text()
    with pytest.raises(AssertionError):
        dotenv.dotenv_values()
    with pytest.raises(AssertionError):
        MonitoringOfflineImports().find_spec("app.repository.planning_repository")
