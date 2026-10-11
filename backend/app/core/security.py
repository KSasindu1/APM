from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings


security_scheme = HTTPBearer(auto_error=False)
NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
HMAC_KEY_BYTES = {"HS256": 32, "HS384": 48, "HS512": 64}
MIN_PASSWORD_ITERATIONS = 600_000
MAX_PASSWORD_ITERATIONS = 2_000_000
MAX_ACCESS_TOKEN_MINUTES = 24 * 60


def auth_error(code: int, detail: str, **headers: str) -> HTTPException:
    if code == status.HTTP_401_UNAUTHORIZED:
        headers["WWW-Authenticate"] = "Bearer"
    return HTTPException(code, detail, headers={**headers, **NO_STORE_HEADERS})


def constant_time_equal(left: str, right: str) -> bool:
    """Compare UTF-8 bytes; invalid Unicode cannot turn a login into a 500."""
    try:
        return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
    except UnicodeEncodeError:
        return False


def _parse_password_hash(encoded: str) -> tuple[int, bytes, bytes]:
    try:
        if not isinstance(encoded, str) or len(encoded) > 256:
            raise ValueError
        scheme, rounds, salt_text, digest_text = encoded.split("$")
        if scheme != "pbkdf2_sha256" or not rounds.isascii() or not rounds.isdecimal():
            raise ValueError
        iterations = int(rounds)
        if str(iterations) != rounds or not (
            MIN_PASSWORD_ITERATIONS <= iterations <= MAX_PASSWORD_ITERATIONS
        ):
            raise ValueError
        salt = base64.b64decode(salt_text, validate=True)
        digest = base64.b64decode(digest_text, validate=True)
        if not (16 <= len(salt) <= 64) or len(digest) != 32:
            raise ValueError
        if (
            base64.b64encode(salt).decode("ascii") != salt_text
            or base64.b64encode(digest).decode("ascii") != digest_text
        ):
            raise ValueError
        return iterations, salt, digest
    except (ValueError, TypeError, binascii.Error):
        raise ValueError("Invalid administrator password hash.") from None


def hash_admin_password(password: str, *, iterations: int = MIN_PASSWORD_ITERATIONS) -> str:
    """Return a salted PBKDF2-SHA256 value for ADMIN_PASSWORD_HASH; never print it."""
    if type(iterations) is not int or not (
        MIN_PASSWORD_ITERATIONS <= iterations <= MAX_PASSWORD_ITERATIONS
    ):
        raise ValueError("Unsupported password hash iteration count.")
    if not isinstance(password, str) or not password.strip() or len(password) > 256:
        raise ValueError("Invalid administrator password.")
    try:
        password_bytes = password.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Invalid administrator password.") from None
    salt = secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iterations)
    return "$".join((
        "pbkdf2_sha256", str(iterations),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    ))


def verify_admin_password(password: str, encoded: str) -> bool:
    """Verify exact UTF-8 password bytes; malformed hash configuration raises ValueError."""
    iterations, salt, expected = _parse_password_hash(encoded)
    try:
        password_bytes = password.encode("utf-8")
    except UnicodeEncodeError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iterations)
    return hmac.compare_digest(actual, expected)


def validate_signing_configuration(audience: str) -> None:
    """Validate signing independently of bootstrap administrator credentials."""
    try:
        minimum = HMAC_KEY_BYTES.get(settings.jwt_algorithm)
        if minimum is None or not settings.jwt_secret_key.strip():
            raise ValueError
        if len(settings.jwt_secret_key.encode("utf-8")) < minimum:
            raise ValueError
        ttl = settings.access_token_expire_minutes
        if type(ttl) is not int or not (1 <= ttl <= MAX_ACCESS_TOKEN_MINUTES):
            raise ValueError
        if not settings.jwt_issuer.strip() or not audience.strip():
            raise ValueError
        settings.jwt_issuer.encode("utf-8")
        audience.encode("utf-8")
    except (ValueError, TypeError, AttributeError):
        raise auth_error(503, "Authentication is unavailable.") from None


def validate_auth_configuration() -> None:
    """Fail closed at request time without disclosing which secret is missing."""
    try:
        username = settings.admin_username
        # Python len counts Unicode codepoints, not UTF-16 units or UTF-8 bytes.
        # Include U+FEFF, which browser trim removes but Python strip preserves.
        if (
            not isinstance(username, str)
            or not 1 <= len(username) <= 100
            or username != username.strip().strip("\ufeff")
            or any(ord(character) < 32 or ord(character) == 127 for character in username)
        ):
            raise ValueError
        username.encode("utf-8")
        if settings.admin_password_hash is not None:
            # An explicitly configured empty/malformed hash must never downgrade.
            _parse_password_hash(settings.admin_password_hash)
        elif not settings.admin_password.strip() or len(settings.admin_password) > 256:
            raise ValueError
        else:
            settings.admin_password.encode("utf-8")
        validate_signing_configuration(settings.jwt_audience)
    except (ValueError, TypeError, AttributeError):
        raise auth_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Authentication is unavailable.",
        ) from None


def authenticate_admin(username: str, password: str) -> bool:
    validate_auth_configuration()
    username_matches = constant_time_equal(username, settings.admin_username)
    if settings.admin_password_hash is not None:
        password_matches = verify_admin_password(password, settings.admin_password_hash)
    else:
        # ADMIN_PASSWORD is an explicitly legacy fallback only when the hash is absent.
        password_matches = constant_time_equal(password, settings.admin_password)
    return username_matches & password_matches


def create_access_token(username: str, role: str) -> str:
    """Mint a signed, scoped access token using only a trusted configured algorithm."""
    validate_auth_configuration()
    if not isinstance(username, str) or not username.strip() or not isinstance(role, str) or not role.strip():
        raise ValueError("A token subject and role are required.")
    now = int(time.time())
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + settings.access_token_expire_minutes * 60,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "token_use": "admin_access",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Verify the signature and claim contract before any authorization decision."""
    validate_auth_configuration()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require": ["sub", "role", "iat", "exp", "iss", "aud", "token_use"],
                # PyJWT coerces NumericDates; we require actual JSON integers instead.
                "verify_iat": False,
                "verify_exp": False,
                "strict_aud": True,
            },
        )
        now = time.time()
        if (
            not isinstance(payload["sub"], str) or not payload["sub"].strip()
            or not isinstance(payload["role"], str) or not payload["role"].strip()
            or type(payload["iat"]) is not int
            or type(payload["exp"]) is not int
            or payload["exp"] <= payload["iat"]
            or payload["iat"] > now
            or payload["exp"] <= now
            or payload["token_use"] != "admin_access"
        ):
            raise jwt.InvalidTokenError
        return payload
    except (jwt.InvalidTokenError, ValueError, TypeError, UnicodeError):
        raise auth_error(
            status.HTTP_401_UNAUTHORIZED, "Invalid authentication token."
        ) from None


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> dict:
    """Authorize the configured administrator or a freshly verified managed admin."""
    if credentials is not None and credentials.scheme.lower() == "bearer":
        from app.core.account_security import is_account_token, verify_account_token

        if is_account_token(credentials.credentials):
            payload = verify_account_token(credentials.credentials)
            if payload["role"] != "admin":
                raise auth_error(403, "Administrator access is required.")
            return payload
    validate_auth_configuration()
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise auth_error(status.HTTP_401_UNAUTHORIZED, "Authentication is required.")
    payload = decode_access_token(credentials.credentials)
    if payload["role"] != "admin" or not constant_time_equal(payload["sub"], settings.admin_username):
        raise auth_error(
            status.HTTP_403_FORBIDDEN, "Administrator access is required."
        )
    return payload
