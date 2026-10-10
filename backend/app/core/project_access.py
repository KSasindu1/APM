"""Server-verified actors for browser project access and trusted planning services."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Header
from fastapi.security import HTTPAuthorizationCredentials

from app.core.account_security import is_account_token, verify_account_token
from app.core.config import get_settings
from app.core.security import auth_error, constant_time_equal, require_admin


@dataclass(frozen=True)
class ProjectActor:
    role: Literal["user", "admin", "service"]
    user_id: str | None
    subject: str

    @property
    def owner_filter(self) -> str | None:
        if self.role == "user":
            # Never turn an incomplete regular-user identity into an unscoped query.
            if self.user_id is None:
                raise auth_error(401, "Invalid authentication token.")
            return self.user_id
        return None


def verify_api_key(x_api_key: str | None) -> None:
    expected_key = get_settings().planning_api_key
    if not isinstance(expected_key, str) or not expected_key.strip():
        raise auth_error(503, "Service authentication is unavailable.")
    if not x_api_key or not constant_time_equal(x_api_key, expected_key):
        raise auth_error(401, "Invalid service credentials.")


def _bearer_credentials(authorization: str | None) -> HTTPAuthorizationCredentials:
    if authorization is None:
        raise auth_error(401, "Authentication is required.")
    scheme, separator, token = authorization.partition(" ")
    if (
        scheme.lower() != "bearer" or not separator or not token
        or len(token) > 4096 or any(character.isspace() for character in token)
    ):
        raise auth_error(401, "Invalid authentication token.")
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def require_project_actor(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> ProjectActor:
    # Presence, not successful HTTPBearer parsing, makes Authorization authoritative.
    if authorization is not None:
        credentials = _bearer_credentials(authorization)
        if is_account_token(credentials.credentials):
            payload = verify_account_token(credentials.credentials)
            return ProjectActor(payload["role"], payload["account_id"], payload["sub"])
        payload = require_admin(credentials)
        return ProjectActor("admin", None, payload["sub"])

    verify_api_key(x_api_key)
    return ProjectActor("service", None, "planning_service")


def require_project_admin(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ProjectActor:
    # Assignment never accepts a service key; require_admin refreshes managed roles.
    payload = require_admin(_bearer_credentials(authorization))
    return ProjectActor("admin", payload.get("account_id"), payload["sub"])


def require_planning_service(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> ProjectActor:
    if authorization is not None:
        raise auth_error(403, "Service authentication is required.")
    verify_api_key(x_api_key)
    return ProjectActor("service", None, "planning_service")
