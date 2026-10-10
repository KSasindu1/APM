"""Account-specific signing, validation, and fresh database authorization."""
from __future__ import annotations

import base64
import re
import threading
import time
from uuid import UUID

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials

from app.core.config import settings
from app.core.security import (
    MIN_PASSWORD_ITERATIONS, auth_error, security_scheme,
    validate_signing_configuration, verify_admin_password,
)
from app.services.account_store import Account, AccountStoreUnavailable, get_account_store


USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{2,49}", re.ASCII)
MAX_ACCOUNT_TOKEN_BYTES = 4096
_dummy_hash: str | None = None
_dummy_lock = threading.Lock()


def canonical_username(username: str) -> str:
    if not isinstance(username, str) or not username.isascii():
        raise ValueError("Invalid username.")
    canonical = username.lower()
    if USERNAME_PATTERN.fullmatch(canonical) is None:
        raise ValueError("Invalid username.")
    return canonical


def validate_new_username(username: str) -> str:
    canonical = canonical_username(username)
    if canonical == settings.admin_username.lower():
        raise ValueError("Invalid username.")
    return canonical


def validate_new_password(password: str) -> str:
    if (
        not isinstance(password, str) or not 12 <= len(password) <= 256
        or all(character.isspace() or character == "\ufeff" for character in password)
    ):
        raise ValueError("Invalid password.")
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Invalid password.") from None
    return password


def validate_account_configuration() -> None:
    validate_signing_configuration(settings.account_jwt_audience)
    if settings.account_jwt_audience == settings.jwt_audience:
        raise auth_error(503, "Authentication is unavailable.")


def dummy_verify(password: str) -> None:
    # Construct the dummy once without expensive hash generation on the first miss.
    global _dummy_hash
    with _dummy_lock:
        if _dummy_hash is None:
            _dummy_hash = "$".join((
                "pbkdf2_sha256", str(MIN_PASSWORD_ITERATIONS),
                base64.b64encode(bytes(32)).decode("ascii"),
                base64.b64encode(bytes(32)).decode("ascii"),
            ))
    verify_admin_password(password, _dummy_hash)


def authenticate_account(username: str, password: str) -> Account:
    validate_account_configuration()
    try:
        canonical = canonical_username(username)
    except ValueError:
        canonical = None
    try:
        account = get_account_store().find(canonical) if canonical is not None else None
        if account is None:
            dummy_verify(password)
            matches = False
        else:
            matches = verify_admin_password(password, account.password_hash)
    except (AccountStoreUnavailable, ValueError, TypeError):
        raise auth_error(503, "Authentication is unavailable.") from None
    if not matches or account is None or not account.active:
        raise auth_error(401, "Invalid username or password.")
    return account


def create_account_token(account: Account) -> tuple[str, int]:
    validate_account_configuration()
    now = int(time.time())
    expires_at = now + settings.access_token_expire_minutes * 60
    payload = {
        "sub": account.username, "account_id": account.user_id, "role": account.role,
        "iat": now, "exp": expires_at, "iss": settings.jwt_issuer,
        "aud": settings.account_jwt_audience, "token_use": "account_access",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm), expires_at


def is_account_token(token: str) -> bool:
    """Untrusted routing hint ONLY; the selected decoder still verifies everything."""
    if not isinstance(token, str) or len(token) > MAX_ACCOUNT_TOKEN_BYTES:
        return False
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload.get("token_use") == "account_access"
    except (jwt.InvalidTokenError, ValueError, TypeError, UnicodeError):
        return False


def decode_account_token(token: str) -> dict:
    validate_account_configuration()
    try:
        if not isinstance(token, str) or len(token) > MAX_ACCOUNT_TOKEN_BYTES:
            raise jwt.InvalidTokenError
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer, audience=settings.account_jwt_audience,
            options={
                "require": ["sub", "account_id", "role", "iat", "exp", "iss", "aud", "token_use"],
                "verify_iat": False, "verify_exp": False, "strict_aud": True,
            },
        )
        now = time.time()
        if (
            canonical_username(payload["sub"]) != payload["sub"]
            or not isinstance(payload["account_id"], str)
            or str(UUID(payload["account_id"])) != payload["account_id"]
            or payload["role"] not in ("user", "admin")
            or type(payload["iat"]) is not int or type(payload["exp"]) is not int
            or payload["iat"] > now or payload["iat"] < 0
            or payload["exp"] <= now or payload["exp"] <= payload["iat"]
            or payload["exp"] - payload["iat"] > settings.access_token_expire_minutes * 60
            or payload["token_use"] != "account_access"
        ):
            raise jwt.InvalidTokenError
        return payload
    except (jwt.InvalidTokenError, ValueError, TypeError, UnicodeError, AttributeError):
        raise auth_error(401, "Invalid authentication token.") from None


def verify_account_token(token: str) -> dict:
    payload = decode_account_token(token)
    try:
        account = get_account_store().find(payload["sub"])
    except AccountStoreUnavailable:
        raise auth_error(503, "Authentication is unavailable.") from None
    if (
        account is None or not account.active
        or account.user_id != payload["account_id"] or account.username != payload["sub"]
        or account.role != payload["role"]
    ):
        raise auth_error(401, "Invalid authentication token.")
    return payload


def require_account(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> dict:
    validate_account_configuration()
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise auth_error(401, "Authentication is required.")
    return verify_account_token(credentials.credentials)


def managed_admin_login_enabled() -> bool:
    return bool(settings.supabase_url and settings.supabase_secret_key)


def validate_admin_login_configuration() -> None:
    from app.core.security import validate_auth_configuration

    # Only absent bootstrap credentials may bypass bootstrap validation, never a
    # malformed explicitly configured password hash or signing configuration.
    if (
        managed_admin_login_enabled() and settings.admin_password_hash is None
        and not settings.admin_password
    ):
        validate_account_configuration()
    else:
        validate_auth_configuration()
