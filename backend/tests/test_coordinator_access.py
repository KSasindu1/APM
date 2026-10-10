"""Offline Coordinator access contracts with real JWT verification and no live IO."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

# This helper guards its third-party imports before loading them. Application
# configuration, dotenv readers, live agents, storage and network stay forbidden.
from test_planning_ownership_api import (
    KEY, SECRET_ERROR, AccountStoreUnavailable, FakeAccountStore, load_source,
    offline_guards_context, stub_module,
)

with offline_guards_context():
    import jwt
    import pytest
    from fastapi import FastAPI
    from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def offline():
    with offline_guards_context():
        yield


@pytest.fixture
def api(monkeypatch):
    for name in ("app", "app.core", "app.services", "app.model", "app.api", "app.agents", "app.agents.coordinator"):
        stub_module(monkeypatch, name, __path__=[])
    settings = SimpleNamespace(
        admin_username="bootstrap_admin", admin_password="synthetic-bootstrap-password", admin_password_hash=None,
        jwt_secret_key="synthetic-coordinator-signing-material-" + "x" * 64, jwt_algorithm="HS256",
        jwt_issuer="offline-coordinator", jwt_audience="offline-admin", account_jwt_audience="offline-accounts",
        access_token_expire_minutes=10, planning_api_key=KEY,
    )
    stub_module(monkeypatch, "app.core.config", settings=settings, get_settings=lambda: settings)
    store = FakeAccountStore()
    stub_module(monkeypatch, "app.services.account_store", Account=type(store.rows["user_a"]),
                AccountStoreUnavailable=AccountStoreUnavailable, get_account_store=lambda: store)
    security = load_source(monkeypatch, "app.core.security", "app/core/security.py")
    accounts = load_source(monkeypatch, "app.core.account_security", "app/core/account_security.py")
    access = load_source(monkeypatch, "app.core.project_access", "app/core/project_access.py")
    load_source(monkeypatch, "app.model.planning", "app/model/planning.py")
    load_source(monkeypatch, "app.agents.coordinator.schemas", "app/agents/coordinator/schemas.py")
    nlp = load_source(monkeypatch, "app.agents.coordinator.nlp", "app/agents/coordinator/nlp.py")
    coordinator = SimpleNamespace(handle=AsyncMock(return_value={
        "request_id": "synthetic-request", "project_id": "synthetic-project", "status": "completed",
    }))
    gateway = object()
    stub_module(monkeypatch, "app.agents.coordinator.agent", CoordinatorAgent=lambda value: coordinator)
    stub_module(monkeypatch, "app.agents.coordinator.planning_gateway", PlanningGateway=lambda: gateway)
    # /request retains its existing lazy planning verifier without importing live planning.
    verifier = Mock(wraps=access.verify_api_key)
    stub_module(monkeypatch, "app.api.planning", verify_api_key=verifier)
    module = load_source(monkeypatch, "app.agents.coordinator.router", "app/agents/coordinator/router.py")
    extract = Mock(wraps=nlp.extract_project)
    monkeypatch.setattr(module, "extract_project", extract)
    application = FastAPI()
    application.include_router(module.router)
    with TestClient(application) as client:
        yield SimpleNamespace(client=client, module=module, settings=settings, store=store,
                              security=security, accounts=accounts, access=access, extract=extract,
                              coordinator=coordinator, verifier=verifier, nlp=nlp)


TEXT = "Project: Inventory. Build stock tracking. Start date: 2026-10-12. Deadline: 2026-12-12. We have 3 developers."
INTERPRET = "/api/coordinator/interpret"
REQUEST = "/api/coordinator/request"
BODY = {"project_name": "Inventory", "objective": "Build a stock tracking application.",
        "start_date": "2026-10-12", "deadline": "2026-12-12"}


def bearer(api, username="user_a"):
    token, _ = api.accounts.create_account_token(api.store.rows[username])
    return {"Authorization": "Bearer " + token}


def denied(api, headers, status=401):
    response = api.client.post(INTERPRET, headers=headers, json={"text": TEXT})
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    assert KEY not in response.text and TEXT not in response.text and SECRET_ERROR not in response.text
    api.extract.assert_not_called()
    api.coordinator.handle.assert_not_awaited()
    api.verifier.assert_not_called()
    return response


@pytest.mark.parametrize("authorization", [None, "", "Basic synthetic", "Bearer", "Bearer ",
                                           "Bearer invalid", "Bearer bad token", "Bearer " + "x" * 4097])
@pytest.mark.parametrize("service_key", [False, True])
def test_interpret_requires_explicit_valid_bearer_even_with_service_key(api, authorization, service_key):
    headers = {"X-API-Key": KEY} if service_key else {}
    if authorization is not None:
        headers["Authorization"] = authorization
    denied(api, headers)
    assert api.store.calls == []


@pytest.mark.parametrize("identity", ["user_a", "managed_admin", "bootstrap_admin"])
@pytest.mark.parametrize("service_key", [False, True])
def test_verified_users_and_admins_keep_exact_extraction_shape_without_planning(api, identity, service_key):
    headers = ({"Authorization": "Bearer " + api.security.create_access_token(identity, "admin")}
               if identity == "bootstrap_admin" else bearer(api, identity))
    if service_key:
        headers["X-API-Key"] = "invalid-service-key-is-irrelevant"
    response = api.client.post(INTERPRET, headers=headers, json={"text": TEXT})
    assert response.status_code == 200
    assert response.json() == api.nlp.extract_project(TEXT).model_dump()
    assert response.json()["draft"]["resources"][0]["capacity"] == 3
    api.extract.assert_called_once_with(TEXT)
    api.coordinator.handle.assert_not_awaited()
    api.verifier.assert_not_called()
    assert api.store.calls == ([] if identity == "bootstrap_admin" else [identity])


@pytest.mark.parametrize("mutation", ["expired", "future", "audience", "issuer", "identity", "role", "purpose", "signature"])
def test_signed_claims_are_verified_before_interpretation(api, mutation):
    original = bearer(api)["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(original, options={"verify_signature": False})
    changes = {
        "expired": {"iat": 1, "exp": 2}, "future": {"iat": claims["exp"] + 1, "exp": claims["exp"] + 100},
        "audience": {"aud": "wrong-audience"}, "issuer": {"iss": "wrong-issuer"},
        "identity": {"account_id": api.store.rows["user_b"].user_id}, "role": {"role": "admin"},
        "purpose": {"token_use": "service_access"}, "signature": {},
    }
    claims.update(changes[mutation])
    key = "wrong-synthetic-signature-" + "z" * 64 if mutation == "signature" else api.settings.jwt_secret_key
    token = jwt.encode(claims, key, algorithm=api.settings.jwt_algorithm)
    denied(api, {"Authorization": "Bearer " + token, "X-API-Key": KEY})


@pytest.mark.parametrize("identity", ["user_a", "managed_admin"])
@pytest.mark.parametrize("change", ["inactive", "role", "removed", "identity"])
def test_account_identity_and_role_are_refreshed_each_request(api, identity, change):
    headers = bearer(api, identity)
    current = api.store.rows[identity]
    if change == "removed":
        del api.store.rows[identity]
    else:
        api.store.rows[identity] = replace(current, **{
            "inactive": {"active": False}, "role": {"role": "user" if current.role == "admin" else "admin"},
            "identity": {"user_id": api.store.rows["user_b"].user_id},
        }[change])
    denied(api, headers)
    assert api.store.calls == [identity]


def test_store_outage_fails_safely_before_extraction(api):
    headers = bearer(api)
    api.store.fail = True
    denied(api, headers, 503)


def test_nonadmin_bootstrap_token_cannot_interpret(api):
    denied(api, {"Authorization": "Bearer " + api.security.create_access_token("bootstrap_admin", "user")}, 403)


def test_service_actor_is_rejected_defensively_without_passing_a_key(api, monkeypatch):
    resolver = Mock(return_value=api.access.ProjectActor("service", None, "synthetic-service"))
    monkeypatch.setattr(api.module, "require_project_actor", resolver)
    denied(api, {"Authorization": "Bearer synthetic", "X-API-Key": KEY}, 403)
    resolver.assert_called_once_with(authorization="Bearer synthetic", x_api_key=None)


def test_authorization_precedes_invalid_input_and_valid_users_keep_validation(api):
    assert api.client.post(INTERPRET, json={"text": "short"}).status_code == 401
    response = api.client.post(INTERPRET, headers=bearer(api), json={"text": "short"})
    assert response.status_code == 422
    api.extract.assert_not_called()


@pytest.mark.parametrize("identity", ["anonymous", "user", "admin", "bad_service"])
def test_request_remains_service_key_protected_and_never_runs_for_browser_bearers(api, identity):
    headers = {"anonymous": {}, "user": bearer(api), "admin": bearer(api, "managed_admin"),
               "bad_service": {"X-API-Key": "invalid-service-key"}}[identity]
    response = api.client.post(REQUEST, headers=headers, json=BODY)
    assert response.status_code == 401
    api.coordinator.handle.assert_not_awaited()
    api.extract.assert_not_called()
    api.verifier.assert_called_once_with(headers.get("X-API-Key"))


def test_request_retains_success_for_trusted_service_key(api):
    response = api.client.post(REQUEST, headers={"X-API-Key": KEY}, json=BODY)
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    api.verifier.assert_called_once_with(KEY)
    api.coordinator.handle.assert_awaited_once()
    api.extract.assert_not_called()
    assert api.store.calls == []
