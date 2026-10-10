"""Guarded offline planning ownership contracts; no real settings, agents, or storage."""
from __future__ import annotations

import builtins
import importlib.abc
import importlib.util
import io
import os
import socket
import sys
from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast
from unittest.mock import patch


BACKEND = Path(__file__).resolve().parents[1]
BASE = "/api/planning"
USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
ADMIN = "33333333-3333-4333-8333-333333333333"
KEY = "synthetic-planning-service-key"
SECRET_ERROR = "synthetic-private-database-error"


def deny_external(*args, **kwargs):
    raise AssertionError("Offline planning tests prohibit settings readers and external IO")


def protected_open(reader):
    def guarded(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            parts = os.fsdecode(file).replace("\\", "/").lower().split("/")
            if any(part == ".env" or part.startswith(".env.") for part in parts):
                raise AssertionError("Protected environment file")
        return reader(file, *args, **kwargs)
    return guarded


class OfflineImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (
            fullname == "app.core.config"
            or fullname.startswith(("app.services.", "app.repository.", "app.agents."))
            or fullname.split(".")[0] in {
                "supabase", "postgrest", "groq", "chromadb", "torch", "transformers",
                "sentence_transformers",
            }
        ):
            deny_external()


@contextmanager
def offline_io():
    # Install these guards even before this test module's third-party imports.
    with ExitStack() as stack:
        stack.enter_context(patch.object(builtins, "open", protected_open(builtins.open)))
        stack.enter_context(patch.object(io, "open", protected_open(io.open)))
        stack.enter_context(patch.object(sys, "meta_path", [OfflineImports(), *sys.meta_path]))
        # Windows asyncio needs its internal socketpair; deny external entry points.
        stack.enter_context(patch.object(socket, "create_connection", deny_external))
        stack.enter_context(patch.object(socket, "getaddrinfo", deny_external))
        yield


with offline_io():
    import dotenv
    import dotenv.main
    import httpx
    import jwt
    import pydantic_settings.sources.providers.dotenv as settings_dotenv
    import pytest
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from pydantic_settings.sources.providers.env import EnvSettingsSource


@contextmanager
def offline_guards_context():
    with offline_io(), ExitStack() as stack:
        for module in (dotenv, dotenv.main):
            for name in ("load_dotenv", "dotenv_values", "find_dotenv"):
                stack.enter_context(patch.object(module, name, deny_external))
        stack.enter_context(patch.object(dotenv.main.DotEnv, "_get_stream", deny_external))
        stack.enter_context(patch.object(settings_dotenv, "read_env_file", deny_external))
        for name in ("_read_env_files", "_read_env_file", "_static_read_env_file"):
            stack.enter_context(patch.object(settings_dotenv.DotEnvSettingsSource, name, deny_external))
        stack.enter_context(patch.object(EnvSettingsSource, "_load_env_vars", deny_external))
        stack.enter_context(patch.object(httpx.HTTPTransport, "handle_request", deny_external))
        stack.enter_context(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", deny_external))
        yield


@pytest.fixture(autouse=True)
def offline_guards():
    with offline_guards_context():
        yield


def stub_module(monkeypatch, name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def load_source(monkeypatch, name, relative_path):
    spec = importlib.util.spec_from_file_location(name, BACKEND / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class FakeAccount:
    user_id: str
    username: str
    role: str
    active: bool = True
    password_hash: str = "unused-synthetic-password-hash"


class AccountStoreUnavailable(Exception):
    pass


class ProjectScheduleExists(Exception):
    pass


class FakeAccountStore:
    def __init__(self):
        self.rows = {
            "user_a": FakeAccount(USER_A, "user_a", "user"),
            "user_b": FakeAccount(USER_B, "user_b", "user"),
            "managed_admin": FakeAccount(ADMIN, "managed_admin", "admin"),
            "inactive_user": FakeAccount(
                "44444444-4444-4444-8444-444444444444", "inactive_user", "user", False,
            ),
        }
        self.calls = []
        self.fail = False

    def find(self, username):
        self.calls.append(username)
        if self.fail:
            raise AccountStoreUnavailable(SECRET_ERROR)
        return self.rows.get(username)


def make_schedule(models, project_id, project_name="Synthetic project"):
    return models.ScheduleResponse.model_validate({
        "project_id": project_id,
        "project_name": project_name,
        "schedule": [{
            "task_id": "T001", "name": "Synthetic task", "duration_days": 2,
            "priority": "HIGH", "planned_start": "2026-10-01", "planned_end": "2026-10-02",
            "is_critical": True, "risk_level": "MEDIUM", "risk_buffer_days": 1,
            "rationale": "Synthetic deterministic result with risk adjustments.",
        }],
        "analysis": {
            "total_duration_days": 2, "available_duration_days": 30, "deadline_met": True,
            "delay_days": 0, "critical_path": ["T001"], "critical_path_duration_days": 2,
            "utilization_percentage": 6.67, "warnings": ["Synthetic risk analysis."],
        },
        "responsible_ai": {
            "ai_generated": False, "human_review_required": True, "confidence": 0.95,
            "explanation": "Synthetic scheduling response.", "limitations": ["Offline fake."],
        },
        "warnings": ["Synthetic risk analysis."],
    })


class FakeRepository:
    """Exact ownership repository contract, including conditional writes and insert races."""
    def __init__(self, models):
        self.rows = {
            "project-a": (USER_A, make_schedule(models, "project-a")),
            "project-b": (USER_B, make_schedule(models, "project-b")),
            "unassigned": (None, make_schedule(models, "unassigned")),
        }
        self.calls = []
        self.failures = {}
        self.before_delete = None

    def check(self, name, *args):
        self.calls.append((name, *args))
        if name in self.failures:
            raise self.failures[name]

    def matches(self, project_id, owner_user_id):
        return project_id in self.rows and (
            owner_user_id is None or self.rows[project_id][0] == owner_user_id
        )

    def create(self, schedule, *, owner_user_id):
        self.check("create", schedule.project_id, owner_user_id)
        if schedule.project_id in self.rows:
            raise ProjectScheduleExists(SECRET_ERROR)
        self.rows[schedule.project_id] = (owner_user_id, schedule)
        return schedule

    def save(self, schedule):
        self.check("save", schedule.project_id)
        owner = self.rows[schedule.project_id][0] if schedule.project_id in self.rows else None
        self.rows[schedule.project_id] = (owner, schedule)
        return schedule

    def get(self, project_id, *, owner_user_id=None):
        self.check("get", project_id, owner_user_id)
        return self.rows[project_id][1] if self.matches(project_id, owner_user_id) else None

    def get_all(self, *, owner_user_id=None):
        self.check("get_all", owner_user_id)
        return [schedule for owner, schedule in self.rows.values()
                if owner_user_id is None or owner == owner_user_id]

    def update(self, project_id, schedule, *, owner_user_id=None):
        self.check("update", project_id, owner_user_id)
        if not self.matches(project_id, owner_user_id):
            return None
        self.rows[project_id] = (self.rows[project_id][0], schedule)
        return schedule

    def delete(self, project_id, *, owner_user_id=None):
        self.check("delete", project_id, owner_user_id)
        if self.before_delete is not None:
            self.before_delete()
        if not self.matches(project_id, owner_user_id):
            return False
        del self.rows[project_id]
        return True

    def assign_owner(self, project_id, owner_user_id):
        self.check("assign_owner", project_id, owner_user_id)
        if project_id not in self.rows:
            return False
        self.rows[project_id] = (owner_user_id, self.rows[project_id][1])
        return True


class FakePlanningService:
    def __init__(self, models):
        self.models = models
        self.requests = []
        self.callbacks = []
        self.after_generation = None
        self.error = None

    async def create_schedule_with_risk(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        result = make_schedule(self.models, request.project_id, request.project_name)
        self.callbacks.append(result.model_dump(mode="json"))
        if self.after_generation is not None:
            self.after_generation()
        return result


@pytest.fixture
def api(monkeypatch):
    # Synthetic packages prevent even package initializers from loading application state.
    for name in ("app", "app.core", "app.api", "app.model", "app.services", "app.repository"):
        stub_module(monkeypatch, name, __path__=[])
    settings = SimpleNamespace(
        admin_username="Bootstrap", admin_password="synthetic-bootstrap-password",
        admin_password_hash=None, jwt_secret_key="synthetic-planning-signing-material-" + "x" * 64,
        jwt_algorithm="HS256", jwt_issuer="offline-planning", jwt_audience="offline-admin",
        account_jwt_audience="offline-accounts", access_token_expire_minutes=10,
        planning_api_key=KEY,
    )
    stub_module(
        monkeypatch, "app.core.config", settings=settings, get_settings=lambda: settings,
        Settings=deny_external,
    )
    store = FakeAccountStore()
    stub_module(
        monkeypatch, "app.services.account_store", Account=FakeAccount,
        AccountStoreUnavailable=AccountStoreUnavailable, get_account_store=lambda: store,
    )
    security = load_source(monkeypatch, "offline_planning_security", "app/core/security.py")
    monkeypatch.setitem(sys.modules, "app.core.security", security)
    accounts = load_source(monkeypatch, "offline_planning_accounts", "app/core/account_security.py")
    monkeypatch.setitem(sys.modules, "app.core.account_security", accounts)
    access = load_source(monkeypatch, "offline_planning_access", "app/core/project_access.py")
    monkeypatch.setitem(sys.modules, "app.core.project_access", access)
    models = load_source(monkeypatch, "offline_planning_models", "app/model/planning.py")
    monkeypatch.setitem(sys.modules, "app.model.planning", models)
    repository = FakeRepository(models)
    service = FakePlanningService(models)
    stub_module(
        monkeypatch, "app.repository.planning_repository", PlanningRepository=lambda: repository,
        ProjectScheduleExists=ProjectScheduleExists,
    )
    stub_module(monkeypatch, "app.services.planning_service", PlanningService=lambda: service)
    planning = load_source(monkeypatch, "offline_planning_api", "app/api/planning.py")
    application = FastAPI()
    application.include_router(planning.router)
    with TestClient(application) as client:
        yield SimpleNamespace(
            client=client, module=planning, access=access, repository=repository, service=service,
            security=security, accounts=accounts, store=store, settings=settings, models=models,
        )


def bearer(token):
    return {"Authorization": "Bearer " + token}


def account_headers(api, username="user_a"):
    token, _ = api.accounts.create_account_token(api.store.rows[username])
    return bearer(token)


def admin_headers(api, kind="managed"):
    if kind == "managed":
        return account_headers(api, "managed_admin")
    return bearer(api.security.create_access_token(api.settings.admin_username, "admin"))


def payload(project_id="new-project", **extra):
    return {
        "project_id": project_id, "project_name": "Synthetic project",
        "objective": "Create an offline synthetic schedule.", "start_date": "2026-10-01",
        "deadline": "2026-10-30", "use_llm": False,
        "tasks": [{"task_id": "T001", "name": "Synthetic task", "duration_days": 2}],
        "resources": [], "risks": [], **extra,
    }


def message(project_id="service-project", action="CREATE_SCHEDULE"):
    return {
        "sender": "coordinator_agent", "receiver": "planning_agent", "action": action,
        "request_id": "offline-request", "data": payload(project_id),
    }


def safe(response, status_code):
    assert response.status_code == status_code, response.text
    assert SECRET_ERROR not in response.text
    return response.json()


def direct_request(api, method, project_id, headers):
    kwargs = {"headers": headers}
    if method == "PUT":
        kwargs["json"] = payload(project_id)
    return api.client.request(method, BASE + "/schedule/" + project_id, **kwargs)


PROTECTED_REQUESTS = [
    ("GET", "/schedules", None),
    ("GET", "/schedule/project-a", None),
    ("POST", "/schedule", payload()),
    ("PUT", "/schedule/project-a", payload("project-a")),
    ("DELETE", "/schedule/project-a", None),
]


def test_health_remains_public_even_without_service_configuration(api):
    api.settings.planning_api_key = None
    assert safe(api.client.get(BASE + "/health"), 200) == {
        "agent": "Planning & Scheduling Agent", "status": "healthy",
    }
    assert not api.repository.calls and not api.service.requests


@pytest.mark.parametrize("method,path,body", PROTECTED_REQUESTS)
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "incorrect"}])
def test_unauthenticated_requests_fail_before_repository_or_generation(api, method, path, body, headers):
    safe(api.client.request(method, BASE + path, json=body, headers=headers), 401)
    assert not api.repository.calls and not api.service.requests


@pytest.mark.parametrize("authorization", [
    "", "Basic synthetic", "Bearer", "Bearer ", "Bearer  token", "Bearer token extra",
    "Bearer invalid-jwt", "Unknown invalid-jwt",
])
@pytest.mark.parametrize("method,path,body", PROTECTED_REQUESTS)
def test_authorization_is_authoritative_and_never_falls_back_to_valid_key(
    api, authorization, method, path, body,
):
    safe(api.client.request(method, BASE + path, json=body, headers={
        "Authorization": authorization, "X-API-Key": KEY,
    }), 401)
    assert not api.repository.calls and not api.service.requests


@pytest.mark.parametrize("missing_key", [None, "", "   "])
@pytest.mark.parametrize("method,path,body", PROTECTED_REQUESTS)
def test_absent_key_configuration_has_no_development_bypass(api, missing_key, method, path, body):
    api.settings.planning_api_key = missing_key
    safe(api.client.request(method, BASE + path, json=body, headers={"X-API-Key": KEY}), 503)
    assert not api.repository.calls and not api.service.requests


def test_valid_user_bearer_plus_service_key_and_injected_filters_stays_user_scoped(api):
    headers = {**account_headers(api), "X-API-Key": KEY}
    rows = safe(api.client.get(BASE + "/schedules", headers=headers,
                               params={"owner_user_id": USER_B, "role": "admin"}), 200)
    assert {row["project_id"] for row in rows} == {"project-a"}
    assert api.repository.calls == [("get_all", USER_A)]
    safe(direct_request(api, "GET", "project-b", headers), 404)
    safe(direct_request(api, "PUT", "project-b", headers), 404)
    safe(direct_request(api, "DELETE", "project-b", headers), 404)
    assert not api.service.requests


def test_account_and_admin_access_do_not_require_service_key_configuration(api):
    api.settings.planning_api_key = None
    safe(api.client.get(BASE + "/schedules", headers=account_headers(api)), 200)
    safe(api.client.get(BASE + "/schedules", headers=admin_headers(api)), 200)
    safe(api.client.put(BASE + "/schedule/unassigned/owner", headers=admin_headers(api),
                        json={"username": "user_a"}), 200)


@pytest.mark.parametrize("username,owner,projects", [
    ("user_a", USER_A, {"project-a"}), ("user_b", USER_B, {"project-b"}),
    ("managed_admin", None, {"project-a", "project-b", "unassigned"}),
])
def test_listings_use_verified_uuid_and_hide_unassigned_projects(api, username, owner, projects):
    rows = safe(api.client.get(BASE + "/schedules", headers=account_headers(api, username)), 200)
    assert {row["project_id"] for row in rows} == projects
    assert api.repository.calls == [("get_all", owner)]


@pytest.mark.parametrize("kind", ["managed", "bootstrap"])
def test_administrators_and_services_can_list_every_project(api, kind):
    for headers in (admin_headers(api, kind), {"X-API-Key": KEY}):
        rows = safe(api.client.get(BASE + "/schedules", headers=headers), 200)
        assert {row["project_id"] for row in rows} == {"project-a", "project-b", "unassigned"}
        assert api.repository.calls[-1] == ("get_all", None)


@pytest.mark.parametrize("username,owner,foreign", [
    ("user_a", USER_A, "project-b"), ("user_b", USER_B, "project-a"),
])
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_foreign_unassigned_and_missing_direct_access_is_the_same_404(
    api, username, owner, foreign, method,
):
    original = dict(api.repository.rows)
    for project_id in (foreign, "unassigned", "missing"):
        response = direct_request(api, method, project_id, account_headers(api, username))
        assert safe(response, 404) == {"detail": "Schedule not found."}
        assert api.repository.calls[-1] == (
            "delete" if method == "DELETE" else "get", project_id, owner,
        )
    assert api.repository.rows == original
    assert not api.service.requests


@pytest.mark.parametrize("username,owner,project_id", [
    ("user_a", USER_A, "project-a"), ("user_b", USER_B, "project-b"),
])
def test_users_can_get_update_and_delete_only_their_project(api, username, owner, project_id):
    headers = account_headers(api, username)
    expected = api.repository.rows[project_id][1].model_dump(mode="json")
    assert safe(direct_request(api, "GET", project_id, headers), 200) == expected
    assert safe(direct_request(api, "PUT", project_id, headers), 200) == expected
    assert api.repository.calls[-2:] == [("get", project_id, owner), ("update", project_id, owner)]
    assert api.repository.rows[project_id][0] == owner
    assert api.service.callbacks == [expected]
    assert safe(direct_request(api, "DELETE", project_id, headers), 200) == {
        "project_id": project_id, "deleted": True,
    }
    assert api.repository.calls[-1] == ("delete", project_id, owner)
    assert project_id not in api.repository.rows


@pytest.mark.parametrize("kind", ["managed", "bootstrap"])
@pytest.mark.parametrize("project_id", ["project-a", "project-b", "unassigned"])
def test_admin_global_direct_crud_preserves_existing_owner_and_schedule_shape(api, kind, project_id):
    headers = admin_headers(api, kind)
    owner, expected = api.repository.rows[project_id]
    assert safe(direct_request(api, "GET", project_id, headers), 200) == expected.model_dump(mode="json")
    safe(direct_request(api, "PUT", project_id, headers), 200)
    assert api.repository.rows[project_id][0] == owner
    assert api.repository.calls[-1] == ("update", project_id, None)
    safe(direct_request(api, "DELETE", project_id, headers), 200)
    assert api.repository.calls[-1] == ("delete", project_id, None)


@pytest.mark.parametrize("username,owner", [
    ("user_a", USER_A), ("user_b", USER_B), ("managed_admin", ADMIN),
])
def test_browser_creator_owns_new_project_and_all_client_ownership_fields_are_ignored(api, username, owner):
    body = payload(owner_user_id=USER_B, owner_username="user_b", user_id=USER_B,
                   owner={"user_id": USER_B}, role="admin", subject="Bootstrap")
    result = safe(api.client.post(BASE + "/schedule", headers=account_headers(api, username), json=body), 200)
    assert api.repository.rows["new-project"][0] == owner
    assert api.repository.calls == [("get", "new-project", None), ("create", "new-project", owner)]
    expected_request = api.models.ProjectPlanningRequest.model_validate(payload()).model_dump(mode="json")
    assert api.service.requests[0].model_dump(mode="json") == expected_request
    assert result == api.service.callbacks[0]
    assert set(result) == {"project_id", "project_name", "schedule", "analysis", "responsible_ai", "warnings"}


def test_bootstrap_browser_create_is_insert_only_and_unassigned(api):
    safe(api.client.post(BASE + "/schedule", headers=admin_headers(api, "bootstrap"),
                         json=payload(owner_user_id=USER_A)), 200)
    assert api.repository.rows["new-project"][0] is None
    assert api.repository.calls[-1] == ("create", "new-project", None)
    rows = safe(api.client.get(BASE + "/schedules", headers=account_headers(api)), 200)
    assert {row["project_id"] for row in rows} == {"project-a"}


@pytest.mark.parametrize("project_id", ["project-a", "project-b", "unassigned"])
def test_service_schedule_upsert_keeps_the_original_owner(api, project_id):
    owner = api.repository.rows[project_id][0]
    safe(api.client.post(BASE + "/schedule", headers={"X-API-Key": KEY},
                         json=payload(project_id, owner_user_id=ADMIN)), 200)
    assert api.repository.rows[project_id][0] == owner
    assert api.repository.calls == [("save", project_id)]


def test_service_create_without_an_account_uuid_is_unassigned(api):
    safe(api.client.post(BASE + "/schedule", headers={"X-API-Key": KEY},
                         json=payload(owner_user_id=USER_A)), 200)
    assert api.repository.rows["new-project"][0] is None


@pytest.mark.parametrize("username", ["user_a", "user_b", "managed_admin"])
@pytest.mark.parametrize("project_id", ["project-a", "project-b", "unassigned"])
def test_duplicate_browser_create_cannot_hijack_or_generate_over_existing_project(api, username, project_id):
    original = dict(api.repository.rows)
    assert safe(api.client.post(BASE + "/schedule", headers=account_headers(api, username),
                                json=payload(project_id)), 409) == {"detail": "Schedule already exists."}
    assert api.repository.rows == original
    assert api.repository.calls == [("get", project_id, None)]
    assert not api.service.requests


def test_atomic_insert_duplicate_after_generation_does_not_replace_winner(api):
    winner = make_schedule(api.models, "new-project", "Other creator wins")
    api.service.after_generation = lambda: api.repository.rows.update({"new-project": (USER_B, winner)})
    safe(api.client.post(BASE + "/schedule", headers=account_headers(api), json=payload()), 409)
    assert api.repository.rows["new-project"] == (USER_B, winner)
    assert api.repository.calls[-1] == ("create", "new-project", USER_A)
    assert not any(call[0] == "save" for call in api.repository.calls)


@pytest.mark.parametrize("race", ["reassign", "delete"])
def test_conditional_update_blocks_ownership_change_or_deletion_during_generation(api, race):
    original = api.repository.rows["project-a"][1]
    def change_project():
        if race == "reassign":
            api.repository.rows["project-a"] = (USER_B, original)
        else:
            del api.repository.rows["project-a"]
    api.service.after_generation = change_project
    assert safe(direct_request(api, "PUT", "project-a", account_headers(api)), 404) == {
        "detail": "Schedule not found.",
    }
    assert api.repository.calls == [("get", "project-a", USER_A), ("update", "project-a", USER_A)]
    if race == "reassign":
        assert api.repository.rows["project-a"] == (USER_B, original)
    else:
        assert "project-a" not in api.repository.rows


def test_delete_is_conditional_when_ownership_changes(api):
    original = api.repository.rows["project-a"][1]
    api.repository.before_delete = lambda: api.repository.rows.update({"project-a": (USER_B, original)})
    safe(direct_request(api, "DELETE", "project-a", account_headers(api)), 404)
    assert api.repository.rows["project-a"] == (USER_B, original)
    assert not api.service.requests


def test_update_checks_access_before_body_id_mismatch_or_generation(api):
    headers = account_headers(api)
    safe(api.client.put(BASE + "/schedule/project-a", headers=headers, json=payload("different")), 400)
    assert safe(api.client.put(BASE + "/schedule/project-b", headers=headers,
                               json=payload("different")), 404) == {"detail": "Schedule not found."}
    assert not api.service.requests


def test_update_ownership_injection_never_reassigns_project(api):
    safe(api.client.put(BASE + "/schedule/project-a", headers=account_headers(api),
                        json=payload("project-a", owner_user_id=USER_B)), 200)
    assert api.repository.rows["project-a"][0] == USER_A
    assert "owner_user_id" not in api.service.requests[0].model_dump()


@pytest.mark.parametrize("kind", ["managed", "bootstrap"])
@pytest.mark.parametrize("project_id", ["unassigned", "project-a"])
def test_admin_assignment_canonicalizes_username_and_changes_visibility(api, kind, project_id):
    result = safe(api.client.put(BASE + "/schedule/" + project_id + "/owner",
                                 headers=admin_headers(api, kind), json={"username": "UsEr_B"}), 200)
    assert result == {"project_id": project_id, "owner_user_id": USER_B, "owner_username": "user_b"}
    assert api.repository.calls[-1] == ("assign_owner", project_id, USER_B)
    assert api.store.calls[-1] == "user_b"
    safe(direct_request(api, "GET", project_id, account_headers(api, "user_b")), 200)
    safe(direct_request(api, "GET", project_id, account_headers(api, "user_a")), 404)
    assert not api.service.requests


@pytest.mark.parametrize("auth_kind,status_code", [
    ("user", 403), ("service", 401), ("none", 401), ("invalid_and_key", 401),
])
def test_owner_assignment_is_bearer_admin_only_not_public_or_service_admin(api, auth_kind, status_code):
    headers = {
        "user": account_headers(api), "service": {"X-API-Key": KEY}, "none": {},
        "invalid_and_key": {"Authorization": "Bearer invalid", "X-API-Key": KEY},
    }[auth_kind]
    safe(api.client.put(BASE + "/schedule/unassigned/owner", headers=headers,
                        json={"username": "user_a"}), status_code)
    assert not api.repository.calls
    assert api.repository.rows["unassigned"][0] is None


@pytest.mark.parametrize("body,status_code", [
    ({}, 422), ({"username": 123}, 422), ({"username": "user_a", "owner_user_id": USER_B}, 422),
    ({"username": "user_a", "role": "admin"}, 422), ({"username": "  "}, 422),
    ({"username": "bad name"}, 400), ({"username": "\u00e9user"}, 400),
])
def test_assignment_body_is_strict_and_does_not_accept_owner_ids_or_extra_fields(api, body, status_code):
    safe(api.client.put(BASE + "/schedule/unassigned/owner", headers=admin_headers(api, "bootstrap"),
                        json=body), status_code)
    assert api.repository.rows["unassigned"][0] is None
    assert not api.store.calls


@pytest.mark.parametrize("username", ["missing_user", "inactive_user", "managed_admin"])
def test_assignment_target_must_be_an_existing_active_regular_user(api, username):
    assert safe(api.client.put(BASE + "/schedule/unassigned/owner", headers=admin_headers(api),
                               json={"username": username}), 400) == {
        "detail": "An active regular user is required.",
    }
    assert api.repository.rows["unassigned"][0] is None


def test_assignment_to_missing_project_is_404(api):
    safe(api.client.put(BASE + "/schedule/missing/owner", headers=admin_headers(api),
                        json={"username": "user_a"}), 404)
    assert not any(call[0] == "assign_owner" for call in api.repository.calls)


@pytest.mark.parametrize("change", ["inactive", "role", "uuid", "username", "missing"])
@pytest.mark.parametrize("username", ["user_a", "managed_admin"])
def test_fresh_database_account_identity_and_role_are_required_even_with_valid_service_key(api, change, username):
    headers = {**account_headers(api, username), "X-API-Key": KEY}
    account = api.store.rows[username]
    if change == "missing":
        del api.store.rows[username]
    else:
        fields = {
            "inactive": {"active": False}, "role": {"role": "user" if account.role == "admin" else "admin"},
            "uuid": {"user_id": USER_B}, "username": {"username": "someone_else"},
        }[change]
        api.store.rows[username] = replace(account, **fields)
    safe(api.client.get(BASE + "/schedules", headers=headers), 401)
    safe(api.client.put(BASE + "/schedule/unassigned/owner", headers=headers,
                        json={"username": "user_b"}), 401)
    assert not api.repository.calls and not api.service.requests


def test_legacy_jwt_requires_actual_configured_admin_subject_and_role(api):
    for subject, role in (("other-admin", "admin"), (api.settings.admin_username, "user")):
        headers = bearer(api.security.create_access_token(subject, role))
        safe(api.client.get(BASE + "/schedules", headers={**headers, "X-API-Key": KEY}), 403)
    assert not api.repository.calls


@pytest.mark.parametrize("mutation", ["expired", "future", "wrong_signature", "wrong_audience", "bad_uuid"])
def test_invalid_managed_jwt_is_not_a_service_key_fallback(api, mutation):
    headers = account_headers(api)
    claims = jwt.decode(headers["Authorization"].split(" ", 1)[1], options={"verify_signature": False})
    key = api.settings.jwt_secret_key
    if mutation == "expired":
        claims.update(iat=claims["iat"] - 1200, exp=claims["iat"] - 600)
    elif mutation == "future":
        claims.update(iat=claims["iat"] + 1000, exp=claims["exp"] + 1000)
    elif mutation == "wrong_signature":
        key = "other-synthetic-signing-material-" + "y" * 64
    elif mutation == "wrong_audience":
        claims["aud"] = api.settings.jwt_audience
    else:
        claims["account_id"] = "not-a-uuid"
    token = jwt.encode(claims, key, algorithm="HS256")
    safe(api.client.get(BASE + "/schedules", headers={**bearer(token), "X-API-Key": KEY}), 401)
    assert not api.repository.calls


@pytest.mark.parametrize("kind", ["user", "managed_admin", "bootstrap", "malformed"])
def test_message_rejects_browser_tokens_even_with_a_valid_service_key(api, kind):
    if kind == "bootstrap":
        headers = admin_headers(api, "bootstrap")
    elif kind == "malformed":
        headers = {"Authorization": "Bearer"}
    else:
        headers = account_headers(api, "user_a" if kind == "user" else "managed_admin")
    safe(api.client.post(BASE + "/message", headers={**headers, "X-API-Key": KEY}, json=message()), 403)
    assert not api.service.requests and not api.repository.calls


@pytest.mark.parametrize("key,status_code", [(None, 401), ("wrong", 401), (KEY, 503)])
def test_message_requires_a_valid_configured_service_key(api, key, status_code):
    if status_code == 503:
        api.settings.planning_api_key = None
    headers = {"X-API-Key": key} if key else {}
    safe(api.client.post(BASE + "/message", headers=headers, json=message()), status_code)
    assert not api.service.requests and not api.repository.calls


@pytest.mark.parametrize("project_id", ["service-project", "project-a"])
def test_service_message_preserves_envelope_risk_callback_and_owner_on_upsert(api, project_id):
    expected_owner = api.repository.rows[project_id][0] if project_id in api.repository.rows else None
    result = safe(api.client.post(BASE + "/message", headers={"X-API-Key": KEY},
                                  json=message(project_id)), 200)
    assert result == {
        "sender": "planning_agent", "receiver": "coordinator_agent", "action": "SCHEDULE_CREATED",
        "request_id": "offline-request", "status": "success", "data": api.service.callbacks[0], "error": None,
    }
    assert api.repository.rows[project_id][0] == expected_owner
    assert api.repository.calls == [("save", project_id)]
    expected_request = api.models.ProjectPlanningRequest.model_validate(payload(project_id)).model_dump(mode="json")
    assert api.service.requests[0].model_dump(mode="json") == expected_request


def test_service_message_unsupported_action_preserves_failure_envelope_without_generation(api):
    result = safe(api.client.post(BASE + "/message", headers={"X-API-Key": KEY},
                                  json=message(action="UNSUPPORTED")), 200)
    assert result["status"] == "failed" and result["action"] == "UNSUPPORTED"
    assert result["error"]["code"] == "UNSUPPORTED_ACTION"
    assert not api.service.requests and not api.repository.calls


@pytest.mark.parametrize("error,code", [(ValueError(SECRET_ERROR), "PLANNING_ERROR"),
                                        (RuntimeError(SECRET_ERROR), "INTERNAL_ERROR")])
def test_service_message_generation_errors_are_safe_and_keep_envelope(api, error, code):
    api.service.error = error
    result = safe(api.client.post(BASE + "/message", headers={"X-API-Key": KEY}, json=message()), 200)
    assert result["status"] == "failed" and result["action"] == "SCHEDULE_CREATED"
    assert result["error"]["code"] == code
    assert not api.repository.calls


def test_service_message_storage_failure_returns_503_with_safe_agent_envelope(api):
    api.repository.failures["save"] = RuntimeError(SECRET_ERROR)
    result = safe(api.client.post(BASE + "/message", headers={"X-API-Key": KEY}, json=message()), 503)
    assert result["status"] == "failed" and result["error"] == {
        "code": "INTERNAL_ERROR", "message": "Planning agent failed.",
    }
    assert result["request_id"] == "offline-request"


@pytest.mark.parametrize("operation,method,path,body", [
    ("get", "GET", "/schedule/project-a", None),
    ("get_all", "GET", "/schedules", None),
    ("get", "POST", "/schedule", payload()),
    ("create", "POST", "/schedule", payload()),
    ("get", "PUT", "/schedule/project-a", payload("project-a")),
    ("update", "PUT", "/schedule/project-a", payload("project-a")),
    ("delete", "DELETE", "/schedule/project-a", None),
    ("assign_owner", "PUT", "/schedule/unassigned/owner", {"username": "user_b"}),
])
@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_ownership_storage_failures_are_fixed_503_not_database_exception_details(
    api, operation, method, path, body, error_type,
):
    api.repository.failures[operation] = error_type(SECRET_ERROR)
    headers = admin_headers(api) if operation == "assign_owner" else account_headers(api)
    assert safe(api.client.request(method, BASE + path, headers=headers, json=body), 503) == {
        "detail": "Project storage is unavailable.",
    }
    if operation in ("get", "get_all", "delete", "assign_owner"):
        assert not api.service.requests


def test_fresh_authentication_store_unavailability_fails_closed(api):
    headers = {**account_headers(api), "X-API-Key": KEY}
    api.store.fail = True
    assert safe(api.client.get(BASE + "/schedules", headers=headers), 503) == {
        "detail": "Authentication is unavailable.",
    }
    assert not api.repository.calls


def test_assignment_target_store_unavailability_is_safe_503(api):
    headers = admin_headers(api, "bootstrap")
    api.store.fail = True
    assert safe(api.client.put(BASE + "/schedule/unassigned/owner", headers=headers,
                               json={"username": "user_a"}), 503) == {
        "detail": "Project storage is unavailable.",
    }
    assert not any(call[0] == "assign_owner" for call in api.repository.calls)


@pytest.mark.parametrize("error,status_code", [(ValueError(SECRET_ERROR), 400),
                                               (RuntimeError(SECRET_ERROR), 500)])
def test_browser_generation_errors_are_safe_and_do_not_write(api, error, status_code):
    api.service.error = error
    safe(api.client.post(BASE + "/schedule", headers=account_headers(api), json=payload()), status_code)
    assert api.repository.calls == [("get", "new-project", None)]


def test_incomplete_user_actor_cannot_become_an_unscoped_repository_filter(api):
    with pytest.raises(HTTPException) as exc:
        _ = api.access.ProjectActor("user", None, "user_a").owner_filter
    assert exc.value.status_code == 401


def test_guards_block_environment_readers_live_imports_and_external_http(api):
    for reader in (builtins.open, io.open):
        for name in (".env", ".env.example"):
            with pytest.raises(AssertionError, match="Protected environment file"):
                reader(name)
    for reader in (dotenv.load_dotenv, dotenv.dotenv_values, dotenv.find_dotenv,
                   settings_dotenv.read_env_file, EnvSettingsSource._load_env_vars):
        with pytest.raises(AssertionError):
            cast(Callable[..., object], reader)()
    with pytest.raises(AssertionError):
        sys.modules["app.core.config"].Settings()
    with pytest.raises(AssertionError):
        OfflineImports().find_spec("app.agents.planning.agent")
    with pytest.raises(AssertionError):
        socket.create_connection(("offline.invalid", 443))
    with pytest.raises(AssertionError):
        httpx.HTTPTransport().handle_request(httpx.Request("GET", "https://offline.invalid"))
