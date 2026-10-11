"""Public account auth and invitation-only managed administrators."""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.api.auth import AuthRoute, LoginAttemptLimiter, MAX_LOGIN_BODY_BYTES
from app.core.account_security import (
    authenticate_account, create_account_token, require_account,
    validate_account_configuration, validate_new_password, validate_new_username,
)
from app.core.security import auth_error, hash_admin_password, require_admin
from app.services.account_store import (
    Account, AccountExists, AccountStoreUnavailable, InvalidInvitation, get_account_store,
)


account_limiter = LoginAttemptLimiter()
INVITATION_TTL_SECONDS = 24 * 60 * 60


class AccountRoute(AuthRoute):
    def get_route_handler(self):
        # Retain AuthRoute.handle's no-store wrapper, not its legacy login handler.
        original = APIRoute.get_route_handler(self)

        async def safe_handler(request: Request) -> Response:
            try:
                if request.method == "POST":
                    validate_account_configuration()
                    if self.path in (
                        "/api/accounts/signup", "/api/accounts/admin-signup", "/api/accounts/login",
                    ):
                        account_limiter.check(request.client.host if request.client else "unknown-peer")
                    content_length = request.headers.get("content-length")
                    if content_length is not None:
                        try:
                            length = int(content_length)
                        except ValueError:
                            raise auth_error(400, "Invalid authentication request.") from None
                        if length < 0:
                            raise auth_error(400, "Invalid authentication request.")
                        if length > MAX_LOGIN_BODY_BYTES:
                            raise auth_error(413, "Authentication request is too large.")
                    body = bytearray()
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > MAX_LOGIN_BODY_BYTES:
                            raise auth_error(413, "Authentication request is too large.")
                        body.extend(chunk)
                    request._body = bytes(body)
                return await original(request)
            except RequestValidationError:
                return JSONResponse({"detail": "Invalid authentication request."}, status_code=422)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except AccountExists:
                return JSONResponse({"detail": "Account already exists."}, status_code=409)
            except InvalidInvitation:
                return JSONResponse({"detail": "Invalid invitation."}, status_code=400)
            except AccountStoreUnavailable:
                return JSONResponse({"detail": "Authentication is unavailable."}, status_code=503)

        return safe_handler



router = APIRouter(prefix="/api/accounts", tags=["Accounts"], route_class=AccountRoute)


class StrictRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, str_strip_whitespace=False, hide_input_in_errors=True,
    )


class SignupRequest(StrictRequest):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("username")
    @classmethod
    def username_valid(cls, value: str) -> str:
        return validate_new_username(value)

    @field_validator("password")
    @classmethod
    def password_valid(cls, value: str) -> str:
        return validate_new_password(value)


class AdminSignupRequest(SignupRequest):
    invitation_code: str = Field(min_length=1, max_length=256)


class LoginRequest(StrictRequest):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("username", "password")
    @classmethod
    def utf8_valid(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Invalid authentication request.") from None
        return value


class InvitationRequest(StrictRequest):
    username: str = Field(min_length=3, max_length=50)

    @field_validator("username")
    @classmethod
    def username_valid(cls, value: str) -> str:
        return validate_new_username(value)


class AccountResponse(BaseModel):
    user_id: UUID
    username: str
    role: Literal["user", "admin"]


class AccountLoginResponse(AccountResponse):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: int


class AccountSessionResponse(AccountResponse):
    expires_at: int


class InvitationResponse(BaseModel):
    invitation_code: str
    username: str
    expires_at: int


def public_account(account: Account) -> dict:
    return {"user_id": account.user_id, "username": account.username, "role": account.role}


def invitation_digest(code: str) -> str:
    try:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        raise InvalidInvitation() from None


@router.post("/signup", response_model=AccountResponse, status_code=201)
def signup(request: SignupRequest):
    account = get_account_store().create_user(request.username, hash_admin_password(request.password))
    return public_account(account)


@router.post("/admin-signup", response_model=AccountResponse, status_code=201)
def admin_signup(request: AdminSignupRequest):
    account = get_account_store().create_admin(
        request.username, hash_admin_password(request.password), invitation_digest(request.invitation_code),
    )
    return public_account(account)


@router.post("/login", response_model=AccountLoginResponse)
def login(request: LoginRequest):
    account = authenticate_account(request.username, request.password)
    token, expires_at = create_account_token(account)
    return {**public_account(account), "access_token": token, "expires_at": expires_at}


@router.get("/session", response_model=AccountSessionResponse)
def session(account: dict = Depends(require_account)):
    return {
        "user_id": account["account_id"], "username": account["sub"],
        "role": account["role"], "expires_at": account["exp"],
    }


@router.post("/admin-invitations", response_model=InvitationResponse)
def admin_invitation(request: InvitationRequest, admin: dict = Depends(require_admin)):
    code = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + INVITATION_TTL_SECONDS
    get_account_store().invite(
        request.username, invitation_digest(code),
        datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
    )
    return {"invitation_code": code, "username": request.username, "expires_at": expires_at}
