"""Offline account contracts. No dotenv, live SDK clients, migrations, or network."""
from __future__ import annotations

import asyncio
import builtins
import hashlib
import importlib
import io
import json
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import dotenv
import dotenv.main
import httpx
import jwt
import pydantic_settings.sources.providers.dotenv as settings_dotenv
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic_settings.sources.providers.env import EnvSettingsSource


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
PASSWORD = "  exact🔒password\t "
BLANK_PASSWORD_CHARACTERS = (
    "\t", "\n", "\v", "\f", "\r", " ", "\u0085", "\u00a0", "\u1680",
    *map(chr, range(0x2000, 0x200B)), "\u2028", "\u2029", "\u202f", "\u205f", "\u3000",
    *map(chr, range(0x001C, 0x0020)), "\ufeff",
)


def deny_external(*args, **kwargs):
    raise AssertionError("Offline tests prohibit dotenv/network access.")


def protected_open(reader):
    def guarded(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            parts = os.fsdecode(file).replace("\\", "/").lower().split("/")
            if any(part == ".env" or part.startswith(".env.") for part in parts):
                raise AssertionError("Protected environment file.")
        return reader(file, *args, **kwargs)
    return guarded


@contextmanager
def offline_settings():
    with ExitStack() as stack:
        for target in (
            "dotenv.load_dotenv", "dotenv.dotenv_values", "dotenv.find_dotenv",
            "dotenv.main.load_dotenv", "dotenv.main.dotenv_values", "dotenv.main.find_dotenv",
            "dotenv.main.DotEnv._get_stream",
            "pydantic_settings.sources.providers.dotenv.read_env_file",
            "pydantic_settings.sources.providers.dotenv.DotEnvSettingsSource._read_env_file",
            "pydantic_settings.sources.providers.dotenv.DotEnvSettingsSource._static_read_env_file",
        ):
            stack.enter_context(patch(target, side_effect=deny_external))
        stack.enter_context(patch.object(settings_dotenv.DotEnvSettingsSource, "_read_env_files", return_value={}))
        stack.enter_context(patch.object(EnvSettingsSource, "_load_env_vars", return_value={}))
        stack.enter_context(patch.object(builtins, "open", protected_open(builtins.open)))
        stack.enter_context(patch.object(io, "open", protected_open(io.open)))
        # Windows asyncio needs its internal socketpair; block external client
        # entry points instead of socket.connect itself.
        stack.enter_context(patch.object(socket, "create_connection", deny_external))
        stack.enter_context(patch.object(socket, "getaddrinfo", deny_external))
        stack.enter_context(patch.object(httpx.HTTPTransport, "handle_request", deny_external))
        stack.enter_context(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", deny_external))
        yield


# Guard before config's compatibility settings object is constructed.
with offline_settings():
    config = importlib.import_module("app.core.config")
    security = importlib.import_module("app.core.security")
    account_security = importlib.import_module("app.core.account_security")
    storage = importlib.import_module("app.services.account_store")
    auth = importlib.import_module("app.api.auth")
    accounts = importlib.import_module("app.api.accounts")


@pytest.fixture(autouse=True)
def offline_guard():
    with offline_settings():
        yield


class FakeStore:
    """Contract fake: all writers share username transactions and rollback on failure."""
    def __init__(self):
        self.rows = {}
        self.invitations = {}
        self.lock = threading.Lock()
        self._username_locks = {}
        self.clock = time.time
        self.fail = False

    def check(self):
        if self.fail:
            raise storage.AccountStoreUnavailable("synthetic-secret-database-error")

    def find(self, username):
        self.check()
        return self.rows.get(username)

    def username_lock(self, username):
        with self.lock:
            return self._username_locks.setdefault(username, threading.Lock())

    @contextmanager
    def transaction(self, username):
        with self.username_lock(username), self.lock:
            yield

    def create_user(self, username, password_hash):
        self.check()
        with self.transaction(username):
            if any(
                invitation["username"] == username and not invitation["consumed"]
                and invitation["expires_at"] > self.clock()
                for invitation in self.invitations.values()
            ):
                raise storage.AccountExists()
            return self._insert(username, password_hash, "user")

    def _insert(self, username, password_hash, role):
        if username in self.rows:
            raise storage.AccountExists()
        account = storage.Account(str(uuid4()), username, role, True, password_hash)
        self.rows[username] = account
        return account

    def invite(self, username, invitation_hash, expires_at):
        self.check()
        with self.transaction(username):
            if username in self.rows or invitation_hash in self.invitations:
                raise storage.AccountExists()
            expiry = datetime.fromisoformat(expires_at).timestamp()
            if expiry <= self.clock():
                raise storage.AccountStoreUnavailable()
            self.invitations[invitation_hash] = {
                "username": username, "expires_at": expiry, "consumed": False,
            }

    def create_admin(self, username, password_hash, invitation_hash):
        self.check()
        with self.transaction(username):
            invitation = self.invitations.get(invitation_hash)
            if (
                invitation is None or invitation["username"] != username
                or invitation["consumed"] or invitation["expires_at"] <= self.clock()
            ):
                raise storage.InvalidInvitation()
            account = self._insert(username, password_hash, "admin")
            invitation["consumed"] = True
            return account


@pytest.fixture
def configured(monkeypatch):
    current = config.Settings(
        _env_file=None, admin_username="Bootstrap", admin_password="legacy synthetic password",
        jwt_secret_key="synthetic-account-signing-material-" + "x" * 64,
        jwt_issuer="offline-apm", jwt_audience="offline-admin", account_jwt_audience="offline-accounts",
        supabase_url="https://offline.invalid", supabase_secret_key="synthetic-service-role",
        access_token_expire_minutes=10,
    )
    for module in (config, security, auth, account_security, storage):
        monkeypatch.setattr(module, "settings", current)
    monkeypatch.setattr(accounts, "account_limiter", auth.LoginAttemptLimiter(attempts=1000))
    monkeypatch.setattr(auth, "login_limiter", auth.LoginAttemptLimiter(attempts=1000))
    return current


@pytest.fixture
def store(configured, monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(storage, "account_store", fake)
    return fake


@pytest.fixture
def app(store):
    application = FastAPI()
    application.include_router(auth.router)
    application.include_router(accounts.router)
    return application


@pytest.fixture
def client(app):
    with TestClient(app, client=("198.51.100.20", 23456)) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def password_hash():
    return security.hash_admin_password(PASSWORD)


def safe(response, code, *secrets):
    assert response.status_code == code, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    for secret in secrets:
        assert secret not in response.text
    if code >= 400:
        assert set(response.json()) == {"detail"}
    if code == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    return response.json()


def bearer(token):
    return {"Authorization": "Bearer " + token}


def bootstrap(client, configured):
    return safe(client.post("/api/auth/login", json={
        "username": configured.admin_username, "password": configured.admin_password,
    }), 200)["access_token"]


def invitation(client, token, username="managed-admin"):
    return safe(client.post("/api/accounts/admin-invitations", headers=bearer(token), json={
        "username": username,
    }), 200)


def seeded(store, password_hash, username="member", role="user"):
    with store.lock:
        return store._insert(username, password_hash, role)


def token_for(account):
    return account_security.create_account_token(account)[0]


def claims_for(account):
    return account_security.decode_account_token(token_for(account))


def signed(current, claims):
    return jwt.encode(claims, current.jwt_secret_key, algorithm=current.jwt_algorithm)


def test_dotenv_guards_and_settings_repr(configured):
    with pytest.raises(AssertionError):
        dotenv.load_dotenv()
    with pytest.raises(AssertionError):
        Path(".env").read_text()
    assert configured.supabase_secret_key not in repr(configured)
    assert configured.jwt_secret_key not in repr(configured)
    assert config.Settings(_env_file=None).account_jwt_audience == "apm-accounts"


def test_signup_login_session_persistence_contract(client, store, configured):
    created = safe(client.post("/api/accounts/signup", json={"username": "New.User", "password": PASSWORD}), 201, PASSWORD)
    assert created["username"] == "new.user" and created["role"] == "user"
    assert set(created) == {"user_id", "username", "role"}
    UUID(created["user_id"])
    record = store.rows["new.user"]
    assert record.password_hash.startswith("pbkdf2_sha256$600000$")
    assert security.verify_admin_password(PASSWORD, record.password_hash)
    assert record.password_hash not in repr(record)
    logged = safe(client.post("/api/accounts/login", json={"username": "NEW.USER", "password": PASSWORD}), 200, PASSWORD, record.password_hash)
    assert set(logged) == {"user_id", "username", "role", "access_token", "token_type", "expires_at"}
    assert logged["token_type"] == "bearer" and logged["user_id"] == created["user_id"]
    claims = account_security.decode_account_token(logged["access_token"])
    assert claims["aud"] == configured.account_jwt_audience
    assert claims["token_use"] == "account_access" and claims["account_id"] == created["user_id"]
    viewed = safe(client.get("/api/accounts/session", headers=bearer(logged["access_token"])), 200)
    assert viewed == {**created, "expires_at": logged["expires_at"]}
    assert "access_token" not in viewed
    safe(client.get("/api/auth/session", headers=bearer(logged["access_token"])), 403)
    safe(client.post("/api/auth/login", json={"username": "NEW.USER", "password": PASSWORD}), 401)
    safe(client.post("/api/accounts/admin-invitations", json={"username": "next-admin"}, headers=bearer(logged["access_token"])), 403)


@pytest.mark.parametrize("username", ["ab", "a" * 51, "_abc", "-abc", ".abc", "abc def", " abc", "abc ", "ümlaut", "Ａbc", "a\nxx", "a/xx", "a@xx", "a\ud800x", "BOOTSTRAP"])
def test_invalid_and_reserved_signup_names(client, username):
    safe(client.post("/api/accounts/signup", content=json.dumps({"username": username, "password": PASSWORD}), headers={"Content-Type": "application/json"}), 422, PASSWORD)


@pytest.mark.parametrize("username", ["abc", "A.B_C-9", "a" * 50])
def test_canonical_username_bounds(client, username):
    result = safe(client.post("/api/accounts/signup", json={"username": username, "password": PASSWORD}), 201)
    assert result["username"] == username.lower()


@pytest.mark.parametrize("password", ["x" * 11, "x" * 257, " " * 12, "\t" * 12, "x" * 12 + "\ud800", 123, None])
def test_password_validation_redacts(client, password):
    safe(client.post("/api/accounts/signup", content=json.dumps({"username": "valid-name", "password": password}), headers={"Content-Type": "application/json"}), 422)


@pytest.mark.parametrize("path", ["signup", "admin-signup"])
@pytest.mark.parametrize("character", BLANK_PASSWORD_CHARACTERS)
def test_new_account_rejects_all_policy_blank_passwords(client, store, path, character):
    for length in (12, 256):
        with pytest.raises(ValueError, match="Invalid password"):
            account_security.validate_new_password(character * length)
    body = {"username": "blank-password", "password": character * 12}
    if path == "admin-signup":
        body["invitation_code"] = "synthetic-secret-invitation"
    safe(client.post("/api/accounts/" + path, json=body), 422, "synthetic-secret-invitation")
    assert not store.rows and not store.invitations


@pytest.mark.parametrize("path", ["signup", "admin-signup"])
def test_new_account_rejects_mixed_policy_blank_password(client, path):
    password = "".join(BLANK_PASSWORD_CHARACTERS)
    body = {"username": "mixed-blank", "password": password}
    if path == "admin-signup":
        body["invitation_code"] = "synthetic-secret-invitation"
    safe(client.post("/api/accounts/" + path, json=body), 422, "synthetic-secret-invitation")


@pytest.mark.parametrize("path", ["signup", "admin-signup"])
@pytest.mark.parametrize("password", [
    "\ufeff" * 11 + "x", "\u001c\u001d\u001e\u001f" * 3 + "🔒",
    "\ufeff\u001c\u001d\u001e\u001f\u00a0\u0085\u2028\u2029\u3000 x\ufeff",
    "\ufeff" * 127 + "x" + "\ufeff" * 128,
])
def test_new_account_preserves_exact_valid_blank_character_mixtures(client, store, configured, path, password):
    assert account_security.validate_new_password(password) == password
    body = {"username": "mixed-password", "password": password}
    if path == "admin-signup":
        issued = invitation(client, bootstrap(client, configured), body["username"])
        body["invitation_code"] = issued["invitation_code"]
    created = safe(client.post("/api/accounts/" + path, json=body), 201)
    assert created["role"] == ("admin" if path == "admin-signup" else "user")
    record = store.rows[body["username"]]
    assert security.verify_admin_password(password, record.password_hash)
    assert not security.verify_admin_password(password.replace("x", "y").replace("🔒", "🔓"), record.password_hash)
    logged = safe(client.post("/api/accounts/login", json={
        "username": body["username"], "password": password,
    }), 200)
    assert logged["user_id"] == created["user_id"]
    if len(password) < 256:
        safe(client.post("/api/accounts/login", json={
            "username": body["username"], "password": password + "\ufeff",
        }), 401)
    else:
        safe(client.post("/api/accounts/login", json={
            "username": body["username"], "password": password[1:],
        }), 401)


def test_legacy_admin_feff_only_password_policy_is_unchanged(client, configured):
    password = "\ufeff" * 12
    encoded = security.hash_admin_password(password)
    assert security.verify_admin_password(password, encoded)
    configured.admin_password = password
    token = bootstrap(client, configured)
    safe(client.get("/api/auth/session", headers=bearer(token)), 200)


@pytest.mark.parametrize("password", ["🔒" * 12, "🔒" * 256, " x" + "p" * 10, "p" * 12 + " "])
def test_exact_unicode_password_codepoints(client, password):
    safe(client.post("/api/accounts/signup", json={"username": "unicode-user", "password": password}), 201)
    safe(client.post("/api/accounts/login", json={"username": "unicode-user", "password": password}), 200)
    safe(client.post("/api/accounts/login", json={"username": "unicode-user", "password": password + "x"}), 422 if len(password) == 256 else 401)


@pytest.mark.parametrize("path", ["signup", "admin-signup", "login", "admin-invitations"])
@pytest.mark.parametrize("extra", [{"role": "admin"}, {"user_id": "injected-secret"}, {"active": True}])
def test_no_public_privilege_parameters(client, configured, path, extra):
    body = {"username": "member", **extra}
    if path != "admin-invitations":
        body["password"] = PASSWORD
    if path == "admin-signup":
        body["invitation_code"] = "injected-secret"
    headers = bearer(bootstrap(client, configured)) if path == "admin-invitations" else {}
    safe(client.post("/api/accounts/" + path, json=body, headers=headers), 422, PASSWORD, "injected-secret")


def test_invitation_first_admin_reuse_binding_and_managed_chain(client, store, configured):
    token = bootstrap(client, configured)
    issued = invitation(client, token, "Managed-Admin")
    assert issued["username"] == "managed-admin"
    assert len(issued["invitation_code"]) == 43
    assert abs(issued["expires_at"] - int(time.time()) - 86400) <= 2
    digest = hashlib.sha256(issued["invitation_code"].encode()).hexdigest()
    assert digest in store.invitations
    assert issued["invitation_code"] not in repr(store.invitations)
    body = {"username": "managed-admin", "password": PASSWORD, "invitation_code": issued["invitation_code"]}
    safe(client.post("/api/accounts/admin-signup", json={**body, "username": "different-user"}), 400, PASSWORD, issued["invitation_code"])
    safe(client.post("/api/accounts/admin-signup", json={**body, "invitation_code": "wrong-code"}), 400)
    created = safe(client.post("/api/accounts/admin-signup", json=body), 201)
    assert created["role"] == "admin"
    safe(client.post("/api/accounts/admin-signup", json=body), 400)
    managed = safe(client.post("/api/auth/login", json={"username": "MANAGED-ADMIN", "password": PASSWORD}), 200)
    assert managed["username"] == "managed-admin" and managed["role"] == "admin"
    assert set(managed) == {"access_token", "token_type", "username", "role", "expires_at"}
    assert account_security.is_account_token(managed["access_token"])
    safe(client.get("/api/auth/session", headers=bearer(managed["access_token"])), 200)
    invitation(client, managed["access_token"], "second-admin")
    # Legacy case remains exact, never silently canonicalized.
    safe(client.post("/api/auth/login", json={"username": "bootstrap", "password": configured.admin_password}), 401)


def test_expired_invitation_and_no_existing_user_escalation(client, store, configured, password_hash):
    token = bootstrap(client, configured)
    issued = invitation(client, token)
    digest = accounts.invitation_digest(issued["invitation_code"])
    body = {"username": issued["username"], "password": PASSWORD, "invitation_code": issued["invitation_code"]}
    store.invitations[digest]["expires_at"] = time.time() - 1
    safe(client.post("/api/accounts/admin-signup", json=body), 400)
    store.invitations[digest]["expires_at"] = time.time() + 100
    seeded(store, password_hash, issued["username"])
    safe(client.post("/api/accounts/admin-signup", json=body), 409)
    assert store.rows[issued["username"]].role == "user"
    assert not store.invitations[digest]["consumed"]
    safe(client.post("/api/accounts/admin-invitations", headers=bearer(token), json={"username": issued["username"]}), 409)


def test_duplicate_canonical_signup_and_salted_passwords(client, store):
    safe(client.post("/api/accounts/signup", json={"username": "member-one", "password": PASSWORD}), 201)
    safe(client.post("/api/accounts/signup", json={"username": "MEMBER-ONE", "password": PASSWORD}), 409)
    safe(client.post("/api/accounts/signup", json={"username": "member-two", "password": PASSWORD}), 201)
    assert store.rows["member-one"].password_hash != store.rows["member-two"].password_hash


def test_atomic_invitation_race_and_duplicate_rollback_contract(store, password_hash):
    code_hash = "a" * 64
    expiry = datetime.fromtimestamp(time.time() + 100).isoformat()
    store.invite("race-admin", code_hash, expiry)
    other_hash = "b" * 64
    store.invite("race-admin", other_hash, expiry)
    barrier = threading.Barrier(8)

    def consume():
        barrier.wait(timeout=5)
        try:
            return store.create_admin("race-admin", password_hash, code_hash).role
        except storage.InvalidInvitation:
            return "invalid"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: consume(), range(8)))
    assert outcomes.count("admin") == 1 and outcomes.count("invalid") == 7
    with pytest.raises(storage.AccountExists):
        store.create_admin("race-admin", password_hash, other_hash)
    assert not store.invitations[other_hash]["consumed"]
    # A failed redemption does not release its outstanding reservation.
    del store.rows["race-admin"]  # Model a privileged operator removing the account.
    with pytest.raises(storage.AccountExists):
        store.create_user("race-admin", password_hash)
    assert store.create_admin("race-admin", password_hash, other_hash).role == "admin"


def test_outstanding_invitation_reserves_canonical_name(client, store, configured):
    issued = invitation(client, bootstrap(client, configured), "Reserved-Admin")
    digest = accounts.invitation_digest(issued["invitation_code"])
    safe(client.post("/api/accounts/signup", json={
        "username": "RESERVED-ADMIN", "password": PASSWORD,
    }), 409, PASSWORD, issued["invitation_code"])
    assert "reserved-admin" not in store.rows
    assert not store.invitations[digest]["consumed"]
    safe(client.post("/api/accounts/signup", json={
        "username": "unrelated-user", "password": PASSWORD,
    }), 201)
    redeemed = safe(client.post("/api/accounts/admin-signup", json={
        "username": issued["username"], "password": PASSWORD,
        "invitation_code": issued["invitation_code"],
    }), 201)
    assert redeemed["role"] == "admin"


def test_invitation_issuance_checks_account_atomically_not_app_lookup(client, store, configured, password_hash, monkeypatch):
    account = seeded(store, password_hash, "already-public")
    stale_lookup = Mock(return_value=None)
    monkeypatch.setattr(store, "find", stale_lookup)
    safe(client.post("/api/accounts/admin-invitations", headers=bearer(bootstrap(client, configured)), json={
        "username": account.username,
    }), 409)
    assert store.rows[account.username] == account and not store.invitations
    stale_lookup.assert_not_called()


def test_expired_reservation_allows_signup_without_escalation(client, store, configured):
    issued = invitation(client, bootstrap(client, configured), "expired-reservation")
    digest = accounts.invitation_digest(issued["invitation_code"])
    store.invitations[digest]["expires_at"] = time.time() - 1
    public = safe(client.post("/api/accounts/signup", json={
        "username": issued["username"], "password": PASSWORD,
    }), 201)
    assert public["role"] == "user"
    safe(client.post("/api/accounts/admin-signup", json={
        "username": issued["username"], "password": PASSWORD,
        "invitation_code": issued["invitation_code"],
    }), 400)
    assert store.rows[issued["username"]].role == "user"
    assert not store.invitations[digest]["consumed"]


def test_every_valid_unused_invitation_reserves_name(store, password_hash):
    expiry = datetime.fromtimestamp(time.time() + 100).isoformat()
    store.invite("multiple-invites", "d" * 64, expiry)
    store.invite("multiple-invites", "e" * 64, expiry)
    store.invitations["d" * 64]["expires_at"] = time.time() - 1
    with pytest.raises(storage.AccountExists):
        store.create_user("multiple-invites", password_hash)
    store.invitations["e" * 64]["consumed"] = True
    assert store.create_user("multiple-invites", password_hash).role == "user"


def ordered_writer_race(store, monkeypatch, first, second):
    """Hold the first username transaction while the other writer starts."""
    original = store.transaction
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()

    @contextmanager
    def held_transaction(username):
        with original(username):
            if not entered.is_set():
                entered.set()
                assert release.wait(timeout=5)
            yield

    monkeypatch.setattr(store, "transaction", held_transaction)

    def outcome(operation, started=None):
        if started is not None:
            started.set()
        try:
            result = operation()
            return result.role if isinstance(result, storage.Account) else "invited"
        except storage.AccountExists:
            return "exists"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(outcome, first)
        try:
            assert entered.wait(timeout=5)
            second_future = pool.submit(outcome, second, second_started)
            assert second_started.wait(timeout=5)
        finally:
            release.set()
        return first_future.result(timeout=5), second_future.result(timeout=5)


@pytest.mark.parametrize("first", ["invite", "user"])
def test_invitation_issuance_vs_public_signup_race(store, password_hash, monkeypatch, first):
    digest = "f" * 64
    expiry = datetime.fromtimestamp(time.time() + 100).isoformat()
    operations = {
        "invite": lambda: store.invite("issuance-race", digest, expiry),
        "user": lambda: store.create_user("issuance-race", password_hash),
    }
    second = "user" if first == "invite" else "invite"
    results = ordered_writer_race(store, monkeypatch, operations[first], operations[second])
    if first == "invite":
        assert results == ("invited", "exists")
        assert "issuance-race" not in store.rows and digest in store.invitations
        assert store.create_admin("issuance-race", password_hash, digest).role == "admin"
    else:
        assert results == ("user", "exists")
        assert store.rows["issuance-race"].role == "user" and not store.invitations


@pytest.mark.parametrize("first", ["admin", "user"])
def test_admin_redemption_vs_public_signup_race(store, password_hash, monkeypatch, first):
    digest = "1" * 64
    store.invite("redemption-race", digest, datetime.fromtimestamp(time.time() + 100).isoformat())
    operations = {
        "admin": lambda: store.create_admin("redemption-race", password_hash, digest),
        "user": lambda: store.create_user("redemption-race", password_hash),
    }
    second = "user" if first == "admin" else "admin"
    results = ordered_writer_race(store, monkeypatch, operations[first], operations[second])
    assert results == (("admin", "exists") if first == "admin" else ("exists", "admin"))
    assert store.rows["redemption-race"].role == "admin"
    assert store.invitations[digest]["consumed"]


@pytest.mark.parametrize("operation", ["user", "admin", "invite"])
def test_expiry_is_checked_after_username_lock_wait(store, password_hash, monkeypatch, operation):
    now = [1_600_000_000]
    store.clock = lambda: now[0]
    expiry = datetime.fromtimestamp(now[0] + 10).isoformat()
    digest = "2" * 64
    if operation != "invite":
        store.invite("expiry-wait", digest, expiry)
    original_lock = store.username_lock
    lock = original_lock("expiry-wait")
    attempted = threading.Event()

    def observed_lock(username):
        attempted.set()
        return original_lock(username)

    monkeypatch.setattr(store, "username_lock", observed_lock)

    def execute():
        try:
            if operation == "user":
                return store.create_user("expiry-wait", password_hash).role
            if operation == "admin":
                return store.create_admin("expiry-wait", password_hash, digest).role
            store.invite("expiry-wait", digest, expiry)
            return "invited"
        except storage.InvalidInvitation:
            return "invalid"
        except storage.AccountStoreUnavailable:
            return "unavailable"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with lock:
            future = pool.submit(execute)
            assert attempted.wait(timeout=5)
            now[0] += 11
        result = future.result(timeout=5)
    assert result == {"user": "user", "admin": "invalid", "invite": "unavailable"}[operation]
    if operation == "user":
        assert store.rows["expiry-wait"].role == "user"
    else:
        assert "expiry-wait" not in store.rows
    if operation == "invite":
        assert not store.invitations
        assert store.create_user("expiry-wait", password_hash).role == "user"
    else:
        assert not store.invitations[digest]["consumed"]


def test_failed_invitation_insert_does_not_reserve_other_username(store, password_hash):
    digest = "3" * 64
    expiry = datetime.fromtimestamp(time.time() + 100).isoformat()
    store.invite("original-reservation", digest, expiry)
    with pytest.raises(storage.AccountExists):
        store.invite("failed-reservation", digest, expiry)
    assert len(store.invitations) == 1
    assert store.create_user("failed-reservation", password_hash).role == "user"
    with pytest.raises(storage.AccountExists):
        store.create_user("original-reservation", password_hash)


def test_failed_admin_insert_keeps_reservation_and_invitation_unused(store, password_hash, monkeypatch):
    digest = "4" * 64
    store.invite("rollback-reservation", digest, datetime.fromtimestamp(time.time() + 100).isoformat())
    original = store._insert
    monkeypatch.setattr(store, "_insert", Mock(side_effect=storage.AccountStoreUnavailable()))
    with pytest.raises(storage.AccountStoreUnavailable):
        store.create_admin("rollback-reservation", password_hash, digest)
    assert not store.rows and not store.invitations[digest]["consumed"]
    monkeypatch.setattr(store, "_insert", original)
    with pytest.raises(storage.AccountExists):
        store.create_user("rollback-reservation", password_hash)
    assert store.create_admin("rollback-reservation", password_hash, digest).role == "admin"


def test_http_invitation_race(app, store, configured, password_hash):
    code = "parallel-synthetic-code"
    digest = accounts.invitation_digest(code)
    store.invite("http-race", digest, datetime.fromtimestamp(time.time() + 100).isoformat())
    barrier = threading.Barrier(2)

    def signup(_):
        with TestClient(app) as test_client:
            barrier.wait(timeout=5)
            response = test_client.post("/api/accounts/admin-signup", json={
                "username": "http-race", "password": PASSWORD, "invitation_code": code,
            })
            safe(response, response.status_code)
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(signup, range(2))) == [201, 400]


@pytest.mark.parametrize("mutation", ["disabled", "deleted", "role", "id", "username"])
def test_session_rechecks_db_identity_role_and_active(client, store, password_hash, mutation):
    account = seeded(store, password_hash, role="admin")
    token = token_for(account)
    safe(client.get("/api/auth/session", headers=bearer(token)), 200)
    if mutation == "deleted":
        del store.rows[account.username]
    else:
        update = {
            "disabled": {"active": False}, "role": {"role": "user"},
            "id": {"user_id": str(uuid4())}, "username": {"username": "different-user"},
        }[mutation]
        store.rows[account.username] = replace(account, **update)
    safe(client.get("/api/accounts/session", headers=bearer(token)), 401)
    safe(client.get("/api/auth/session", headers=bearer(token)), 401)


@pytest.mark.parametrize(("claim", "value"), [
    ("sub", "MEMBER"), ("sub", "☃"), ("sub", "ab"), ("sub", None),
    ("account_id", "not-uuid"), ("account_id", None), ("account_id", 1),
    ("role", "owner"), ("role", None), ("role", ["admin"]),
    ("iat", True), ("iat", "123"), ("iat", 1.5), ("iat", -1),
    ("exp", True), ("exp", "123"), ("exp", 1.5),
    ("aud", "offline-admin"), ("aud", ["offline-accounts"]),
    ("iss", "wrong-issuer"), ("token_use", "admin_access"),
])
def test_malformed_claims(client, store, configured, password_hash, claim, value):
    account = seeded(store, password_hash, role="admin")
    claims = claims_for(account)
    claims[claim] = value
    safe(client.get("/api/accounts/session", headers=bearer(signed(configured, claims))), 401)


@pytest.mark.parametrize("claim", ["sub", "account_id", "role", "iat", "exp", "iss", "aud", "token_use"])
def test_missing_claims(client, store, configured, password_hash, claim):
    claims = claims_for(seeded(store, password_hash))
    del claims[claim]
    safe(client.get("/api/accounts/session", headers=bearer(signed(configured, claims))), 401)


def test_expiry_future_issue_lifetime_and_signature(client, store, configured, password_hash):
    account = seeded(store, password_hash, role="admin")
    claims = claims_for(account)
    now = int(time.time())
    for changes in (
        {"iat": now - 100, "exp": now}, {"iat": now + 10, "exp": now + 100},
        {"iat": now, "exp": now}, {"exp": now + 601},
    ):
        safe(client.get("/api/accounts/session", headers=bearer(signed(configured, {**claims, **changes}))), 401)
    wrong = jwt.encode(claims, "wrong-synthetic-key" + "z" * 64, algorithm="HS256")
    safe(client.get("/api/accounts/session", headers=bearer(wrong)), 401)
    safe(client.get("/api/auth/session", headers=bearer(wrong)), 401)
    wrong_alg = jwt.encode(claims, configured.jwt_secret_key, algorithm="HS384")
    safe(client.get("/api/accounts/session", headers=bearer(wrong_alg)), 401)
    for token in ("invalid", "a.b.c", "x" * 4097):
        safe(client.get("/api/accounts/session", headers=bearer(token)), 401)
    with patch.object(account_security.time, "time", return_value=claims["exp"]):
        safe(client.get("/api/accounts/session", headers=bearer(signed(configured, claims))), 401)


def test_unverified_claims_never_authorize_and_legacy_not_account_session(client, store, configured, password_hash):
    account = seeded(store, password_hash)
    claims = claims_for(account)
    # Signed role injection still fails the fresh DB role binding.
    claims["role"] = "admin"
    safe(client.get("/api/auth/session", headers=bearer(signed(configured, claims))), 401)
    legacy = bootstrap(client, configured)
    safe(client.get("/api/accounts/session", headers=bearer(legacy)), 401)
    safe(client.get("/api/auth/session", headers=bearer(legacy)), 200)
    for path in ("/api/accounts/session", "/api/accounts/admin-invitations"):
        if path.endswith("session"):
            safe(client.get(path), 401)
            safe(client.get(path, headers={"Authorization": "Basic nope"}), 401)
        else:
            safe(client.post(path, json={"username": "new-admin"}), 401)


def test_managed_admin_independent_of_bootstrap_password(client, store, configured, password_hash):
    seeded(store, password_hash, "managed-admin", "admin")
    configured.admin_password = ""
    managed = safe(client.post("/api/auth/login", json={"username": "MANAGED-ADMIN", "password": PASSWORD}), 200)
    safe(client.get("/api/auth/session", headers=bearer(managed["access_token"])), 200)
    invitation(client, managed["access_token"], "next-admin")
    configured.admin_username = ""
    safe(client.post("/api/auth/login", json={"username": "MANAGED-ADMIN", "password": PASSWORD}), 200)
    safe(client.get("/api/auth/session"), 503)
    configured.supabase_url = ""
    safe(client.post("/api/auth/login", json={"username": "MANAGED-ADMIN", "password": PASSWORD}), 503)


@pytest.mark.parametrize(("field", "value"), [
    ("jwt_secret_key", ""), ("jwt_secret_key", "x" * 31), ("jwt_secret_key", "\ud800" * 64),
    ("jwt_algorithm", "none"), ("access_token_expire_minutes", True),
    ("access_token_expire_minutes", 1441), ("jwt_issuer", ""),
    ("account_jwt_audience", ""), ("account_jwt_audience", "offline-admin"),
])
def test_account_signing_config_fails_closed(client, configured, field, value):
    setattr(configured, field, value)
    safe(client.post("/api/accounts/signup", json={"username": "member", "password": PASSWORD}), 503)
    safe(client.get("/api/accounts/session"), 503)


def test_missing_storage_config_is_lazy_closed_and_bootstrap_still_works(client, configured, monkeypatch):
    real_store = storage.AccountStore()
    monkeypatch.setattr(storage, "account_store", real_store)
    configured.supabase_url = ""
    configured.supabase_secret_key = ""
    assert real_store._client is None
    safe(client.post("/api/accounts/signup", json={"username": "member", "password": PASSWORD}), 503)
    safe(client.post("/api/accounts/login", json={"username": "member", "password": PASSWORD}), 503)
    token = bootstrap(client, configured)
    safe(client.post("/api/accounts/admin-invitations", headers=bearer(token), json={"username": "first-admin"}), 503)
    safe(client.get("/api/auth/session", headers=bearer(token)), 200)
    assert real_store._client is None


def test_storage_failures_sanitized_every_path(client, store, configured, password_hash):
    account = seeded(store, password_hash, role="admin")
    token = token_for(account)
    legacy = bootstrap(client, configured)
    store.fail = True
    for path, body, headers in (
        ("signup", {"username": "member-two", "password": PASSWORD}, {}),
        ("admin-signup", {"username": "member-two", "password": PASSWORD, "invitation_code": "synthetic-secret-code"}, {}),
        ("login", {"username": "member", "password": PASSWORD}, {}),
        ("admin-invitations", {"username": "member-two"}, bearer(legacy)),
    ):
        safe(client.post("/api/accounts/" + path, json=body, headers=headers), 503, "synthetic-secret-database-error", PASSWORD, "synthetic-secret-code")
    safe(client.get("/api/accounts/session", headers=bearer(token)), 503)
    safe(client.get("/api/auth/session", headers=bearer(token)), 503)
    safe(client.post("/api/auth/login", json={"username": "member", "password": PASSWORD}), 503)


def test_missing_user_dummy_hash_and_disabled_password_check(client, store, password_hash, monkeypatch):
    verify = Mock(wraps=account_security.verify_admin_password)
    monkeypatch.setattr(account_security, "verify_admin_password", verify)
    safe(client.post("/api/accounts/login", json={"username": "nonexistent", "password": PASSWORD}), 401)
    assert verify.call_count == 1 and verify.call_args.args[0] == PASSWORD
    assert verify.call_args.args[1].startswith("pbkdf2_sha256$600000$")
    account = seeded(store, password_hash)
    store.rows[account.username] = replace(account, active=False)
    safe(client.post("/api/accounts/login", json={"username": account.username, "password": PASSWORD}), 401)
    assert verify.call_count == 2 and verify.call_args.args[1] == password_hash


def test_slow_hash_work_off_event_loop(client, monkeypatch):
    threads = []
    original = accounts.hash_admin_password

    def observed(password):
        threads.append(threading.current_thread().name)
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        return original(password)

    monkeypatch.setattr(accounts, "hash_admin_password", observed)
    safe(client.post("/api/accounts/signup", json={"username": "thread-user", "password": PASSWORD}), 201)
    assert threads and all("worker" in name.lower() for name in threads)


@pytest.mark.parametrize("path", ["signup", "admin-signup", "login", "admin-invitations"])
def test_no_store_validation_method_and_body_errors(client, path):
    url = "/api/accounts/" + path
    safe(client.put(url), 405)
    safe(client.post(url, content="{\"password\":\"secret-marker\"", headers={"Content-Type": "application/json"}), 422, "secret-marker")
    safe(client.post(url, content="secret-marker" * 400), 413, "secret-marker")
    safe(client.post(url, content="{}", headers={"Content-Length": "bad"}), 400)
    safe(client.post(url, content="{}", headers={"Content-Length": "-1"}), 400)


def test_stream_body_limit(app):
    messages = []
    reads = 0

    async def receive():
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": b"x" * 4097, "more_body": True}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/api/accounts/signup", "raw_path": b"/api/accounts/signup",
        "query_string": b"", "headers": [], "client": ("198.51.100.30", 123), "server": ("test", 80),
    }
    asyncio.run(app(scope, receive, send))
    assert reads == 1
    assert messages[0]["status"] == 413
    assert (b"cache-control", b"no-store") in messages[0]["headers"]


def test_peer_limiter_shared_across_signup_adminsignup_login(client, app, monkeypatch):
    monkeypatch.setattr(accounts, "account_limiter", auth.LoginAttemptLimiter(attempts=2))
    safe(client.post("/api/accounts/signup", json={}), 422)
    safe(client.post("/api/accounts/admin-signup", json={}), 422)
    limited = client.post("/api/accounts/login", json={}, headers={"X-Forwarded-For": "new-peer"})
    safe(limited, 429)
    assert int(limited.headers["retry-after"]) >= 1
    with TestClient(app, client=("198.51.100.99", 123)) as other:
        safe(other.post("/api/accounts/login", json={}), 422)
    with patch.object(auth.time, "monotonic", return_value=time.monotonic() + 61):
        safe(client.post("/api/accounts/login", json={}), 422)


@pytest.mark.parametrize("role", ["user", "admin"])
def test_accounts_do_not_acquire_monitoring_scopes_or_membership(store, password_hash, role):
    from app.agents.monitoring.agent import MonitoringTokenAuthenticator
    from app.agents.monitoring.schemas import MonitoringPrincipal

    account = seeded(store, password_hash, role=role)
    token = token_for(account)
    claims = account_security.decode_account_token(token)
    assert "project_ids" not in claims and "permissions" not in claims
    scoped_token = "offline-monitoring-specific-token-" + "s" * 40
    principal = MonitoringPrincipal(
        subject="scoped-monitor", project_ids=frozenset({"P001"}),
        permissions=frozenset({"monitoring:read"}),
    )
    authenticator = MonitoringTokenAuthenticator({scoped_token: principal})
    assert authenticator.authenticate(token) is None
    assert authenticator.authenticate(scoped_token) is not None


def test_corrupt_stored_password_hash_is_unavailable_not_leaked(client, store, password_hash):
    account = seeded(store, password_hash)
    store.rows[account.username] = replace(account, password_hash="synthetic-secret-broken-hash")
    safe(client.post("/api/accounts/login", json={"username": account.username, "password": PASSWORD}), 503, PASSWORD, "synthetic-secret-broken-hash")


def test_session_method_errors_no_store(client):
    safe(client.post("/api/accounts/session", json={}), 405)


def test_migration_privileges_and_atomic_constraints_static():
    sql = (BACKEND / "sql" / "auth_accounts.sql").read_text(encoding="utf-8")
    assert sql.strip().startswith("-- Apply") and "BEGIN;" in sql and sql.strip().endswith("COMMIT;")
    for table in ("auth_accounts", "auth_admin_invitations"):
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in sql
    assert "FROM PUBLIC, anon, authenticated, service_role" in sql
    assert "GRANT SELECT ON TABLE public.auth_accounts TO service_role" in sql
    signatures = (
        "create_user_account(text, text)",
        "issue_admin_invitation(text, text, timestamptz)",
        "consume_admin_invitation(text, text, text)",
    )
    revokes = sql.split("REVOKE ALL ON FUNCTION", 1)[1].split(";", 1)[0]
    assert "FROM PUBLIC, anon, authenticated, service_role" in revokes
    for signature in signatures:
        assert "public." + signature in revokes
        assert f"GRANT EXECUTE ON FUNCTION public.{signature} TO service_role" in sql
    assert sql.count("SET search_path = pg_catalog") == 3 and sql.count("SECURITY DEFINER") == 3
    assert "GRANT INSERT" not in sql
    assert len([line for line in sql.splitlines() if line.startswith("GRANT ")]) == 4
    assert "consumed_at IS NULL" in sql and "expires_at > clock_timestamp()" in sql
    assert "AND username = p_username" in sql and "VALUES (p_username, p_password_hash, 'admin')" in sql
    lock = "PERFORM pg_advisory_xact_lock(hashtextextended('apm-auth-account:' || p_username, 0));"
    assert sql.count(lock) == 3
    functions = {
        name: sql.split(f"CREATE FUNCTION public.{name}(", 1)[1].split("$$;", 1)[0]
        for name in ("create_user_account", "issue_admin_invitation", "consume_admin_invitation")
    }
    for body in functions.values():
        assert "clock_timestamp()" in body and "now()" not in body
        assert "EXCEPTION WHEN" not in body and "ON CONFLICT" not in body
        assert "UPDATE public.auth_accounts" not in body
        assert body.index(lock) < body.index("INSERT INTO public.")
    create_user = functions["create_user_account"]
    assert create_user.index(lock) < create_user.index("SELECT 1 FROM public.auth_admin_invitations")
    assert "WHERE username = p_username" in create_user
    assert "consumed_at IS NULL" in create_user and "expires_at > clock_timestamp()" in create_user
    assert "ERRCODE = '23505'" in create_user
    assert "VALUES (p_username, p_password_hash, 'user')" in create_user
    issue = functions["issue_admin_invitation"]
    assert issue.index(lock) < issue.index("SELECT 1 FROM public.auth_accounts WHERE username = p_username")
    assert "p_expires_at <= clock_timestamp()" in issue and "ERRCODE = '23505'" in issue
    consume = functions["consume_admin_invitation"]
    assert consume.index(lock) < consume.index("UPDATE public.auth_admin_invitations") < consume.index("INSERT INTO public.auth_accounts")
    assert "p_role" not in sql and "invitation_code" not in sql
    assert "CREATE POLICY" not in sql and "GRANT UPDATE" not in sql and "GRANT DELETE" not in sql
    assert "user_id uuid PRIMARY KEY DEFAULT gen_random_uuid()" in sql
    assert "username text NOT NULL UNIQUE" in sql
    assert "invitation_hash text PRIMARY KEY CHECK (invitation_hash ~ '^[0-9a-f]{64}$')" in sql
    assert "role IN ('user', 'admin')" in sql and "active boolean NOT NULL DEFAULT true" in sql


class FakeSDKError(Exception):
    def __init__(self, code):
        super().__init__("synthetic-secret-sdk-error")
        self.code = code


@pytest.fixture
def sdk(configured, monkeypatch, password_hash):
    row = {"user_id": str(uuid4()), "username": "sdk-member", "role": "user", "active": True, "password_hash": password_hash}
    query = Mock()
    for method in ("select", "eq", "limit"):
        getattr(query, method).return_value = query
    query.execute.return_value = SimpleNamespace(data=[row])
    client = Mock()
    client.table.return_value = query
    client.rpc.return_value = query
    create = Mock(return_value=client)
    import supabase
    monkeypatch.setattr(supabase, "create_client", create)
    return storage.AccountStore(), client, query, create, row


def test_sdk_lazy_service_client_and_calls(sdk, password_hash):
    store, client, query, create, row = sdk
    assert store._client is None and not create.called
    account = store.find("sdk-member")
    assert account.user_id == row["user_id"]
    options = create.call_args.kwargs["options"]
    assert options.persist_session is False and options.auto_refresh_token is False
    client.table.assert_called_with("auth_accounts")
    query.eq.assert_called_with("username", "sdk-member")
    store.create_user("sdk-member", password_hash)
    client.rpc.assert_called_with("create_user_account", {
        "p_username": "sdk-member", "p_password_hash": password_hash,
    })
    store.create_admin("sdk-member", password_hash, "a" * 64)
    client.rpc.assert_called_with("consume_admin_invitation", {
        "p_username": "sdk-member", "p_password_hash": password_hash, "p_invitation_hash": "a" * 64,
    })
    store.invite("sdk-admin", "b" * 64, "2030-01-01T00:00:00+00:00")
    client.rpc.assert_called_with("issue_admin_invitation", {
        "p_username": "sdk-admin", "p_invitation_hash": "b" * 64,
        "p_expires_at": "2030-01-01T00:00:00+00:00",
    })
    assert client.rpc.call_count == 3 and client.table.call_count == 1
    assert not query.insert.called
    assert create.call_count == 1
    assert not client.auth.method_calls


@pytest.mark.parametrize(("code", "expected"), [("23505", storage.AccountExists), ("P0001", storage.InvalidInvitation), ("42P01", storage.AccountStoreUnavailable), ("42501", storage.AccountStoreUnavailable)])
def test_sdk_rpc_safe_error_mapping(sdk, password_hash, code, expected):
    store, _, query, _, _ = sdk
    query.execute.side_effect = FakeSDKError(code)
    with pytest.raises(expected) as exc:
        store.create_admin("sdk-admin", password_hash, "a" * 64)
    assert "synthetic-secret" not in str(exc.value)


@pytest.mark.parametrize("operation", ["user", "invite"])
@pytest.mark.parametrize("code", ["23505", "23514", "42501"])
def test_sdk_synchronized_writer_errors_are_sanitized(sdk, password_hash, operation, code):
    store, _, query, _, _ = sdk
    query.execute.side_effect = FakeSDKError(code)
    expected = storage.AccountExists if code == "23505" else storage.AccountStoreUnavailable
    with pytest.raises(expected) as exc:
        if operation == "user":
            store.create_user("sdk-member", password_hash)
        else:
            store.invite("sdk-member", "a" * 64, "2030-01-01T00:00:00+00:00")
    assert "synthetic-secret" not in str(exc.value)


@pytest.mark.parametrize("operation", ["find", "user", "invite"])
def test_sdk_missing_migration_errors(sdk, password_hash, operation):
    store, _, query, _, _ = sdk
    query.execute.side_effect = FakeSDKError("42P01")
    with pytest.raises(storage.AccountStoreUnavailable):
        if operation == "find":
            store.find("sdk-member")
        elif operation == "user":
            store.create_user("sdk-member", password_hash)
        else:
            store.invite("sdk-member", "a" * 64, "2030-01-01T00:00:00+00:00")


@pytest.mark.parametrize(("field", "value"), [("user_id", "broken"), ("username", "UPPERCASE"), ("active", "true"), ("role", "owner"), ("password_hash", None)])
def test_sdk_corrupt_rows_fail_closed(sdk, field, value):
    store, _, _, _, row = sdk
    row[field] = value
    with pytest.raises(storage.AccountStoreUnavailable):
        store.find("sdk-member")


def test_sdk_empty_lookup_and_client_initialization_error(sdk):
    store, _, query, create, _ = sdk
    query.execute.return_value = SimpleNamespace(data=[])
    assert store.find("sdk-member") is None
    store._client = None
    create.side_effect = RuntimeError("synthetic-secret-client-error")
    with pytest.raises(storage.AccountStoreUnavailable) as exc:
        store.find("sdk-member")
    assert "synthetic-secret" not in str(exc.value)
