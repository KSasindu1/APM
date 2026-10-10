"""Offline auth regressions: never import main/agents or load repository secrets."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib
import json
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dotenv
import dotenv.main
import jwt
import pydantic_settings.sources.providers.dotenv as settings_dotenv
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic_settings.sources.providers.env import EnvSettingsSource


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _deny_env_read(*args, **kwargs):
    raise AssertionError("Auth tests must not read dotenv files.")


@contextmanager
def _offline_settings():
    # Sources are constructed before settings_customise_sources; disable loading
    # there too. Guard both libraries' readers in case any code bypasses the source.
    with ExitStack() as stack:
        for target in (
            "dotenv.load_dotenv", "dotenv.dotenv_values", "dotenv.find_dotenv",
            "dotenv.main.load_dotenv", "dotenv.main.dotenv_values", "dotenv.main.find_dotenv",
            "dotenv.main.DotEnv._get_stream",
            "pydantic_settings.sources.providers.dotenv.read_env_file",
            "pydantic_settings.sources.providers.dotenv.DotEnvSettingsSource._read_env_file",
            "pydantic_settings.sources.providers.dotenv.DotEnvSettingsSource._static_read_env_file",
        ):
            stack.enter_context(patch(target, side_effect=_deny_env_read))
        stack.enter_context(patch.object(
            settings_dotenv.DotEnvSettingsSource, "_read_env_files", return_value={}
        ))
        stack.enter_context(patch.object(EnvSettingsSource, "_load_env_vars", return_value={}))
        yield


# config constructs its compatibility settings at import time: guard BEFORE import.
with _offline_settings():
    config = importlib.import_module("app.core.config")
    security = importlib.import_module("app.core.security")
    auth = importlib.import_module("app.api.auth")


@pytest.fixture(autouse=True)
def guarded_settings():
    with _offline_settings():
        yield


@pytest.fixture
def configured(monkeypatch):
    current = config.Settings(
        _env_file=None,
        admin_username="管理员",
        admin_password="  päss🔒\t ",
        jwt_secret_key="synthetic-signing-material-" + "x" * 64,
        jwt_issuer="test-apm",
        jwt_audience="test-admin",
        access_token_expire_minutes=10,
    )
    for module in (config, security, auth):
        monkeypatch.setattr(module, "settings", current)
    monkeypatch.setattr(auth, "login_limiter", auth.LoginAttemptLimiter())
    return current


@pytest.fixture
def app(configured):
    application = FastAPI()
    application.include_router(auth.router)
    return application


@pytest.fixture
def client(app):
    with TestClient(app, client=("198.51.100.10", 12345)) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def password_hash():
    return security.hash_admin_password("  päss🔒\t ")


def login_body(current):
    return {"username": current.admin_username, "password": current.admin_password}


def assert_safe(response, code, *sensitive_values):
    assert response.status_code == code
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    for value in sensitive_values:
        assert value not in response.text
    if code == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    if code >= 400:
        assert set(response.json()) == {"detail"}
        assert isinstance(response.json()["detail"], str)


def claims(current, **updates):
    now = int(time.time())
    payload = {
        "sub": current.admin_username, "role": "admin", "iat": now,
        "exp": now + 600, "iss": current.jwt_issuer, "aud": current.jwt_audience,
        "token_use": "admin_access",
    }
    payload.update(updates)
    return payload


def signed(current, payload=None, *, key=None, algorithm=None):
    # Build raw test JWTs so encoder-side validation cannot hide decoder bugs.
    algorithm = algorithm or current.jwt_algorithm
    key = current.jwt_secret_key if key is None else key

    def segment(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).rstrip(b"=")

    signing_input = b".".join((
        segment({"alg": algorithm, "typ": "JWT"}),
        segment(payload if payload is not None else claims(current)),
    ))
    digest = hmac.new(key.encode("utf-8"), signing_input, getattr(hashlib, "sha" + algorithm[2:])).digest()
    return (signing_input + b"." + base64.urlsafe_b64encode(digest).rstrip(b"=")).decode("ascii")


def session(client, token):
    return client.get("/api/auth/session", headers={"Authorization": "Bearer " + token})


def encoded_hash(rounds="600000", salt=b"s" * 32, digest=b"d" * 32):
    return "$".join((
        "pbkdf2_sha256", rounds,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    ))


def test_dotenv_guards_and_synthetic_settings(monkeypatch, tmp_path):
    # Even inherited environment values are not part of the test configuration.
    monkeypatch.setenv("ADMIN_USERNAME", "environment-identity-must-not-load")
    monkeypatch.setenv("ADMIN_PASSWORD", "environment-password-must-not-load")
    current = config.Settings(_env_file=tmp_path / "synthetic.env")
    assert current.admin_username == config.Settings.model_fields["admin_username"].default
    assert current.admin_password == config.Settings.model_fields["admin_password"].default
    assert current.admin_password_hash is None
    assert current.jwt_secret_key == ""
    readers = (
        dotenv.load_dotenv, dotenv.dotenv_values, dotenv.main.load_dotenv,
        dotenv.main.dotenv_values, settings_dotenv.read_env_file,
        settings_dotenv.DotEnvSettingsSource._static_read_env_file,
    )
    for reader in readers:
        with pytest.raises(AssertionError, match="must not read dotenv"):
            reader(BACKEND / ".env")


def test_password_whitespace_and_secret_repr():
    secrets = {
        "admin_password": "  exact-password\t ",
        "admin_password_hash": "  hidden-hash  ",
        "jwt_secret_key": "  hidden-signing-key  ",
        "groq_api_key": "hidden-groq", "planning_api_key": "hidden-planning",
        "supabase_secret_key": "hidden-supabase",
        "postgres_database_url": "hidden-database-credentials",
    }
    current = config.Settings(_env_file=None, **secrets)
    for name in ("admin_password", "admin_password_hash", "jwt_secret_key"):
        assert getattr(current, name) == secrets[name]
    for value in secrets.values():
        assert value.strip() not in repr(current)
        assert value.strip() not in str(current)


@pytest.mark.parametrize(("field", "value"), [
    ("admin_username", ""), ("admin_username", " \t "),
    ("admin_username", "\ud800"), ("admin_username", "u" * 101),
    ("admin_username", "🔒" * 101),
    ("admin_username", " admin"), ("admin_username", "admin "),
    ("admin_username", "\ufeffadmin"), ("admin_username", "admin\ufeff"),
    ("admin_username", "\ufeffadmin\ufeff"),
    *[("admin_username", "user" + chr(codepoint) + "name") for codepoint in (*range(32), 127)],
    ("admin_password", ""), ("admin_password", " \t "),
    ("admin_password", "\ud800"), ("admin_password", "p" * 257),
    ("jwt_secret_key", ""), ("jwt_secret_key", "s" * 31),
    ("jwt_secret_key", " " * 64), ("jwt_secret_key", "\ud800" * 64),
    ("jwt_algorithm", "none"), ("jwt_algorithm", "RS256"),
    ("jwt_algorithm", "hs256"), ("jwt_algorithm", "HS999"),
    ("access_token_expire_minutes", 0), ("access_token_expire_minutes", -1),
    ("access_token_expire_minutes", 1441), ("access_token_expire_minutes", True),
    ("access_token_expire_minutes", "60"), ("access_token_expire_minutes", 1.5),
    ("jwt_issuer", ""), ("jwt_audience", " \t "),
])
def test_bad_configuration_is_503_on_every_auth_path(client, configured, field, value):
    setattr(configured, field, value)
    assert_safe(client.post("/api/auth/login", json={"username": "x", "password": "x"}), 503)
    assert_safe(client.get("/api/auth/session"), 503)
    for action in (
        lambda: security.create_access_token("x", "admin"),
        lambda: security.decode_access_token("invalid"),
    ):
        with pytest.raises(HTTPException) as exc:
            action()
        assert exc.value.status_code == 503
        assert exc.value.detail == "Authentication is unavailable."
        assert exc.value.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("username", [
    "管理员", "a b", "a\u0080b", "a\ufeffb", "e\u0301", "\U00010000", "\U0010ffff",
    "🔒" * 51, "🔒" * 100, "u" * 100,
])
def test_supported_username_codepoints_login_and_session(client, configured, username):
    configured.admin_username = username
    security.validate_auth_configuration()
    response = client.post("/api/auth/login", json=login_body(configured))
    assert_safe(response, 200)
    assert response.json()["username"] == username
    session_response = session(client, response.json()["access_token"])
    assert_safe(session_response, 200)
    assert session_response.json()["username"] == username


def test_configured_username_is_trimmed_without_stripping_password(client, configured):
    synthetic = config.Settings(
        _env_file=None, admin_username=" \t管理员🔒\t ", admin_password=" \tpassword🔒\t ",
    )
    assert synthetic.admin_username == "管理员🔒"
    assert synthetic.admin_password == " \tpassword🔒\t "
    configured.admin_username = synthetic.admin_username
    configured.admin_password = synthetic.admin_password
    response = client.post("/api/auth/login", json=login_body(configured))
    assert_safe(response, 200)
    assert response.json()["username"] == synthetic.admin_username
    assert_safe(client.post("/api/auth/login", json={
        "username": synthetic.admin_username, "password": synthetic.admin_password.strip(),
    }), 401)


@pytest.mark.parametrize("password", ["🔒" * 129, "🔒" * 256, " \t" + "🔒" * 252 + "\t "])
def test_supplementary_password_codepoints_and_exact_whitespace(client, configured, password):
    configured.admin_username = "🔒" * 51
    configured.admin_password = config.Settings(_env_file=None, admin_password=password).admin_password
    assert configured.admin_password == password
    assert len(configured.admin_username) == 51
    assert 129 <= len(password) <= 256
    assert len(configured.admin_username.encode("utf-16-le")) // 2 > 100
    assert len(password.encode("utf-16-le")) // 2 > 256
    response = client.post("/api/auth/login", json=login_body(configured))
    assert_safe(response, 200)
    assert_safe(session(client, response.json()["access_token"]), 200)
    assert security.verify_admin_password(password, security.hash_admin_password(password))
    if password != password.strip():
        assert_safe(client.post("/api/auth/login", json={
            "username": configured.admin_username, "password": password.strip(),
        }), 401)


@pytest.mark.parametrize(("field", "overflow"), [
    ("username", "u" * 101), ("username", "🔒" * 101),
    ("password", "p" * 257), ("password", "🔒" * 257),
])
def test_login_request_codepoint_limits_are_strict(client, configured, field, overflow):
    configured.admin_username = "🔒" * 100
    configured.admin_password = "🔒" * 256
    assert_safe(client.post("/api/auth/login", json=login_body(configured)), 200)
    body = login_body(configured)
    body[field] = overflow
    response = client.post("/api/auth/login", json=body)
    assert_safe(response, 422, "🔒")
    assert response.json() == {"detail": "Invalid authentication request."}


def test_entirely_missing_configuration(client, configured):
    configured.admin_username = ""
    configured.admin_password = ""
    configured.jwt_secret_key = ""
    assert_safe(client.post("/api/auth/login", json={}), 503)
    assert_safe(client.get("/api/auth/session"), 503)


@pytest.mark.parametrize(("algorithm", "byte_length"), [("HS256", 32), ("HS384", 48), ("HS512", 64)])
def test_hmac_minimum_is_algorithm_bits_and_utf8_bytes(configured, algorithm, byte_length):
    configured.jwt_algorithm = algorithm
    configured.jwt_secret_key = "é" * (byte_length // 2)
    security.validate_auth_configuration()
    token = security.create_access_token(configured.admin_username, "admin")
    assert security.decode_access_token(token)["sub"] == configured.admin_username
    configured.jwt_secret_key = "a" * (byte_length - 1)
    with pytest.raises(HTTPException) as exc:
        security.validate_auth_configuration()
    assert exc.value.status_code == 503


@pytest.mark.parametrize("ttl", [1, 1440])
def test_ttl_bounds_minted_as_integer_seconds(configured, monkeypatch, ttl):
    configured.access_token_expire_minutes = ttl
    monkeypatch.setattr(security, "time", SimpleNamespace(time=lambda: 2_000_000_000))
    payload = security.decode_access_token(security.create_access_token(configured.admin_username, "admin"))
    assert type(payload["iat"]) is int
    assert type(payload["exp"]) is int
    assert payload["exp"] - payload["iat"] == ttl * 60


def test_salted_hash_generation_verification_and_no_output(password_hash, capsys):
    another_hash = security.hash_admin_password("  päss🔒\t ")
    assert another_hash != password_hash
    for encoded in (password_hash, another_hash):
        assert encoded.startswith("pbkdf2_sha256$600000$")
        assert security.verify_admin_password("  päss🔒\t ", encoded)
        assert not security.verify_admin_password("päss🔒", encoded)
        assert not security.verify_admin_password("wrong", encoded)
        assert not security.verify_admin_password("\ud800", encoded)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("rounds", [0, 599999, 2000001, True, "600000", 600000.0])
def test_hash_generation_iteration_bounds(rounds):
    with pytest.raises(ValueError, match="iteration count"):
        security.hash_admin_password("password", iterations=rounds)


@pytest.mark.parametrize("password", ["", " \t ", "\ud800", "x" * 257])
def test_hash_generation_rejects_invalid_passwords(password):
    with pytest.raises(ValueError, match="Invalid administrator password"):
        security.hash_admin_password(password)


@pytest.mark.parametrize("malformed", [
    "", " ", "plaintext", "bcrypt$600000$salt$digest", "pbkdf2_sha256$600000$!$!",
    encoded_hash("599999"), encoded_hash("2000001"), encoded_hash("0600000"),
    encoded_hash("+600000"), encoded_hash("600000.0"), encoded_hash("６０００００"),
    encoded_hash(salt=b"short"), encoded_hash(salt=b"s" * 65),
    encoded_hash(digest=b"short"), encoded_hash() + "$extra", "h" * 257,
])
def test_malformed_hash_is_503_without_plaintext_downgrade(client, configured, malformed):
    configured.admin_password_hash = malformed
    response = client.post("/api/auth/login", json=login_body(configured))
    assert_safe(response, 503, configured.admin_password, configured.jwt_secret_key)
    assert_safe(client.get("/api/auth/session"), 503)
    with pytest.raises(ValueError, match="Invalid administrator password hash"):
        security.verify_admin_password(configured.admin_password, malformed)


def test_hash_precedence_and_no_plaintext_requirement(client, configured, password_hash):
    configured.admin_password_hash = password_hash
    configured.admin_password = "legacy-password-must-not-work"
    response = client.post("/api/auth/login", json=login_body(configured))
    assert_safe(response, 401, configured.admin_password, password_hash)
    configured.admin_password = ""
    response = client.post("/api/auth/login", json={
        "username": configured.admin_username, "password": "  päss🔒\t ",
    })
    assert_safe(response, 200, password_hash)
    assert_safe(session(client, response.json()["access_token"]), 200)


@pytest.mark.parametrize(("username", "password"), [
    ("wrong", "  päss🔒\t "), ("管理员", "wrong"),
    ("管理员", "päss🔒"), ("管理员 ", "  päss🔒\t "),
])
def test_wrong_unicode_or_whitespace_credentials_are_generic_401(client, username, password):
    response = client.post("/api/auth/login", content=json.dumps({
        "username": username, "password": password,
    }), headers={"Content-Type": "application/json"})
    assert_safe(response, 401)
    assert response.json() == {"detail": "Invalid username or password."}


@pytest.mark.parametrize("field", ["username", "password"])
def test_invalid_unicode_is_sanitized_validation_error(client, configured, field):
    body = login_body(configured)
    body[field] = "\ud800"
    response = client.post("/api/auth/login", content=json.dumps(body),
                           headers={"Content-Type": "application/json"})
    assert_safe(response, 422)
    assert response.json() == {"detail": "Invalid authentication request."}


def test_utf8_constant_time_comparison(configured):
    assert security.constant_time_equal("管理员🔒", "管理员🔒")
    assert not security.constant_time_equal("管理员🔒", "administrator")
    assert not security.constant_time_equal("\ud800", "\ud800")
    assert security.authenticate_admin(configured.admin_username, configured.admin_password)


def test_password_is_checked_even_for_wrong_username(configured, password_hash, monkeypatch):
    configured.admin_password_hash = password_hash
    with patch.object(security, "verify_admin_password", wraps=security.verify_admin_password) as verify:
        assert not security.authenticate_admin("wrong", "wrong-password")
    verify.assert_called_once_with("wrong-password", password_hash)


@pytest.mark.parametrize("missing", ["sub", "role", "iat", "exp", "iss", "aud", "token_use"])
def test_missing_claims_are_401(client, configured, missing):
    payload = claims(configured)
    del payload[missing]
    assert_safe(session(client, signed(configured, payload)), 401)


@pytest.mark.parametrize(("claim", "value"), [
    ("iat", True), ("iat", False), ("iat", "1"), ("iat", "not-a-number"),
    ("iat", 1.5), ("iat", None), ("iat", []),
    ("exp", True), ("exp", False), ("exp", "9999999999"),
    ("exp", 9999999999.0), ("exp", None), ("exp", {}),
    ("sub", None), ("sub", 12), ("sub", ""), ("sub", " \t "),
    ("role", None), ("role", 12), ("role", ""), ("role", []),
    ("iss", "foreign-issuer"), ("iss", None),
    ("aud", "foreign-audience"), ("aud", ["test-admin"]), ("aud", None),
    ("token_use", "refresh"), ("token_use", None), ("token_use", True),
])
def test_invalid_claims_are_sanitized_401(client, configured, claim, value):
    response = session(client, signed(configured, claims(configured, **{claim: value})))
    assert_safe(response, 401, configured.jwt_secret_key)
    assert response.json() == {"detail": "Invalid authentication token."}


@pytest.mark.parametrize("case", ["expired", "future_iat", "equal_dates", "reversed_dates", "expired_now"])
def test_expiry_future_iat_and_ordering(client, configured, case):
    now = int(time.time())
    updates = {
        "expired": {"iat": now - 20, "exp": now - 1},
        "future_iat": {"iat": now + 60, "exp": now + 600},
        "equal_dates": {"iat": now, "exp": now},
        "reversed_dates": {"iat": now, "exp": now - 1},
        "expired_now": {"iat": now - 60, "exp": now},
    }[case]
    response = session(client, signed(configured, claims(configured, **updates)))
    assert_safe(response, 401)
    assert response.json()["detail"] == "Invalid authentication token."


def test_deterministic_expiry_boundary(configured, monkeypatch):
    monkeypatch.setattr(security, "time", SimpleNamespace(time=lambda: 2_000_000_000))
    token = security.create_access_token(configured.admin_username, "admin")
    monkeypatch.setattr(security, "time", SimpleNamespace(time=lambda: 2_000_000_600))
    with pytest.raises(HTTPException) as exc:
        security.decode_access_token(token)
    assert exc.value.status_code == 401


@pytest.mark.parametrize("algorithm", ["HS384", "HS512", "none"])
def test_wrong_algorithm_is_401(client, configured, algorithm):
    key = "" if algorithm == "none" else configured.jwt_secret_key
    token = jwt.encode(claims(configured), key, algorithm=algorithm)
    assert_safe(session(client, token), 401)


def test_wrong_signature_and_malformed_tokens(client, configured):
    assert_safe(session(client, signed(configured, key="different-test-key-" + "z" * 64)), 401)
    for token in ("not-a-jwt", "a.b.c", "", "eyJhbGciOiJIUzI1NiJ9.e30.bad"):
        assert_safe(session(client, token), 401)


@pytest.mark.parametrize(("subject", "role"), [
    ("管理员", "member"), ("foreign-user", "admin"),
    ("foreign-user", "member"), ("管理员 ", "admin"), ("\ud800", "admin"),
])
def test_valid_foreign_subject_or_nonadmin_role_is_403(client, configured, subject, role):
    response = session(client, signed(configured, claims(configured, sub=subject, role=role)))
    assert_safe(response, 403, configured.jwt_secret_key)
    assert response.json() == {"detail": "Administrator access is required."}


@pytest.mark.parametrize("authorization", [None, "Basic credential", "Bearer", "Unknown token"])
def test_session_requires_bearer(client, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    assert_safe(client.get("/api/auth/session", headers=headers), 401, "credential")


@pytest.mark.parametrize("body", [
    {}, {"username": "u"}, {"password": "sensitive-validation-password"},
    {"username": "u", "password": "sensitive-validation-password", "role": "admin"},
    {"username": "u", "password": "sensitive-validation-password", "user_id": "extra-id"},
    {"username": 123, "password": "sensitive-validation-password"},
    {"username": "u", "password": ["sensitive-validation-password"]},
    {"username": "u", "password": ""}, {"username": "", "password": "p"},
    {"username": "u" * 101, "password": "sensitive-validation-password"},
    {"username": "u", "password": "sensitive-validation-password" * 20},
    [], None,
])
def test_strict_validation_never_echoes_credentials(client, body):
    response = client.post("/api/auth/login", json=body)
    assert_safe(response, 422, "sensitive-validation-password", "extra-id")
    assert response.json() == {"detail": "Invalid authentication request."}


def test_malformed_json_is_sanitized(client):
    response = client.post("/api/auth/login", content='{"password":"sensitive-json-password",',
                           headers={"Content-Type": "application/json"})
    assert_safe(response, 422, "sensitive-json-password")


def test_non_json_validation_is_sanitized(client):
    response = client.post("/api/auth/login", content="password=sensitive-form-password",
                           headers={"Content-Type": "text/plain"})
    assert_safe(response, 422, "sensitive-form-password")


@pytest.mark.parametrize("with_length", [True, False])
def test_oversized_body_is_413_without_echo(client, with_length):
    content = b"sensitive-oversized-password" + b"x" * auth.MAX_LOGIN_BODY_BYTES
    body = content if with_length else iter((content[:100], content[100:]))
    response = client.post("/api/auth/login", content=body,
                           headers={"Content-Type": "application/json"})
    assert_safe(response, 413, "sensitive-oversized-password")


def test_stream_body_cap_stops_before_reading_more(app):
    chunks = iter((b"x" * 3000, b"x" * 3000, b"must-not-be-read"))
    consumed = []
    messages = []

    async def receive():
        chunk = next(chunks)
        consumed.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/api/auth/login",
        "raw_path": b"/api/auth/login", "query_string": b"", "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("198.51.100.20", 12345), "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    assert len(consumed) == 2
    assert messages[0]["status"] == 413
    assert dict(messages[0]["headers"])[b"cache-control"] == b"no-store"
    assert b"must-not-be-read" not in messages[1]["body"]


@pytest.mark.parametrize("length", ["invalid", "-1", "9999999999"])
def test_invalid_or_large_content_length_is_safe(client, length):
    response = client.post("/api/auth/login", content="sensitive-content-length",
                           headers={"Content-Type": "application/json", "Content-Length": length})
    assert_safe(response, 413 if length.isdecimal() else 400, "sensitive-content-length")


def test_throttle_counts_attempts_and_ignores_forwarded_headers(client, configured):
    body = {"username": configured.admin_username, "password": "wrong-password"}
    for index in range(5):
        response = client.post("/api/auth/login", json=body, headers={
            "X-Forwarded-For": f"203.0.113.{index}", "Forwarded": f"for=203.0.113.{index}",
        })
        assert_safe(response, 401)
    response = client.post("/api/auth/login", json=login_body(configured))
    assert_safe(response, 429, configured.admin_username, configured.admin_password)
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert set(auth.login_limiter._windows) == {"198.51.100.10"}


def test_validation_and_success_also_consume_attempts(client, configured, monkeypatch):
    monkeypatch.setattr(auth, "login_limiter", auth.LoginAttemptLimiter(attempts=2))
    assert_safe(client.post("/api/auth/login", json={}), 422)
    assert_safe(client.post("/api/auth/login", json=login_body(configured)), 200)
    assert_safe(client.post("/api/auth/login", json=login_body(configured)), 429)


def test_actual_peer_ips_have_separate_budgets(app, configured, monkeypatch):
    monkeypatch.setattr(auth, "login_limiter", auth.LoginAttemptLimiter(attempts=1))
    with TestClient(app, client=("198.51.100.10", 1111)) as first:
        assert_safe(first.post("/api/auth/login", json=login_body(configured)), 200)
        assert_safe(first.post("/api/auth/login", json=login_body(configured)), 429)
    with TestClient(app, client=("198.51.100.11", 2222)) as second:
        assert_safe(second.post("/api/auth/login", json=login_body(configured)), 200)


def test_limiter_bounded_memory_no_eviction_or_permanent_lockout(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    limiter = auth.LoginAttemptLimiter(attempts=2, window_seconds=10, capacity=2)
    limiter.check("ip-one")
    limiter.check("ip-two")
    for index in range(20):
        with pytest.raises(HTTPException) as exc:
            limiter.check(f"overflow-{index}")
        assert exc.value.status_code == 429
        assert exc.value.headers["Retry-After"] == "10"
        assert exc.value.headers["Cache-Control"] == "no-store"
        assert len(limiter._windows) == 2
    limiter.check("ip-one")
    clock[0] = 109.5
    with pytest.raises(HTTPException) as exc:
        limiter.check("ip-one")
    assert exc.value.headers["Retry-After"] == "1"
    clock[0] = 110.0
    limiter.check("ip-one")
    assert limiter._windows == {"ip-one": (110.0, 1)}
    limiter.check("new-ip")
    assert len(limiter._windows) == 2


@pytest.mark.parametrize("bounds", [
    {"attempts": 0}, {"attempts": True}, {"window_seconds": 0},
    {"window_seconds": -1}, {"capacity": 0}, {"capacity": 1.5},
])
def test_limiter_requires_positive_bounds(bounds):
    with pytest.raises(ValueError, match="positive integers"):
        auth.LoginAttemptLimiter(**bounds)


def test_http_throttle_reset(client, configured, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(auth, "login_limiter", auth.LoginAttemptLimiter(attempts=1, window_seconds=10))
    assert_safe(client.post("/api/auth/login", json=login_body(configured)), 200)
    assert_safe(client.post("/api/auth/login", json=login_body(configured)), 429)
    clock[0] = 110.0
    assert_safe(client.post("/api/auth/login", json=login_body(configured)), 200)


@pytest.mark.parametrize(("method", "path"), [("GET", "/api/auth/login"), ("POST", "/api/auth/session")])
def test_method_errors_are_no_store(client, method, path):
    assert_safe(client.request(method, path), 405)


def test_login_and_session_contract(client, configured, caplog):
    response = client.post("/api/auth/login", json=login_body(configured))
    assert_safe(response, 200, configured.admin_password, configured.jwt_secret_key)
    result = response.json()
    assert set(result) == {"access_token", "token_type", "username", "role", "expires_at"}
    assert result["token_type"] == "bearer"
    assert result["username"] == configured.admin_username
    assert result["role"] == "admin"
    assert type(result["expires_at"]) is int
    payload = security.decode_access_token(result["access_token"])
    assert payload["exp"] == result["expires_at"]
    assert payload["iss"] == configured.jwt_issuer
    assert payload["aud"] == configured.jwt_audience
    assert payload["token_use"] == "admin_access"
    assert payload["exp"] - payload["iat"] == 600
    session_response = session(client, result["access_token"])
    assert_safe(session_response, 200, result["access_token"])
    assert session_response.json() == {
        "username": result["username"], "role": "admin", "expires_at": result["expires_at"],
    }
    assert configured.admin_password not in caplog.text
    assert configured.jwt_secret_key not in caplog.text
    assert result["access_token"] not in caplog.text


def test_username_change_revokes_old_single_admin_subject(client, configured):
    token = security.create_access_token(configured.admin_username, "admin")
    configured.admin_username = "replacement-configured-admin"
    assert_safe(session(client, token), 403)
