from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from app.core.config import settings
from app.core.account_security import (
    authenticate_account, create_account_token, managed_admin_login_enabled,
    validate_admin_login_configuration,
)
from app.core.security import (
    NO_STORE_HEADERS,
    auth_error,
    authenticate_admin,
    create_access_token,
    decode_access_token,
    require_admin,
    validate_auth_configuration,
)


MAX_LOGIN_BODY_BYTES = 4096


class LoginAttemptLimiter:
    """Bounded, single-process protection; multi-worker deployments need a shared limiter."""

    def __init__(self, attempts: int = 5, window_seconds: int = 60, capacity: int = 1024):
        if any(type(value) is not int or value <= 0 for value in (attempts, window_seconds, capacity)):
            raise ValueError("Login limiter bounds must be positive integers.")
        self.attempts = attempts
        self.window_seconds = window_seconds
        self.capacity = capacity
        self._windows: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def check(self, client_ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._windows = {
                ip: entry for ip, entry in self._windows.items()
                if now - entry[0] < self.window_seconds
            }
            entry = self._windows.get(client_ip)
            if entry is None:
                if len(self._windows) >= self.capacity:
                    # Do not evict active IPs: churn must not reset a guessing budget.
                    oldest = min(start for start, _ in self._windows.values())
                    self._deny(oldest + self.window_seconds - now)
                entry = (now, 0)
            start, count = entry
            if count >= self.attempts:
                self._deny(start + self.window_seconds - now)
            self._windows[client_ip] = (start, count + 1)

    @staticmethod
    def _deny(seconds: float) -> None:
        raise auth_error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many authentication attempts. Try again later.",
            **{"Retry-After": str(max(1, math.ceil(seconds)))},
        )


login_limiter = LoginAttemptLimiter()


class AuthRoute(APIRoute):
    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def no_store_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).update(NO_STORE_HEADERS)
            await send(message)

        # Framework method errors otherwise escape to the app's exception handler.
        try:
            await super().handle(scope, receive, no_store_send)
        except HTTPException as exc:
            response = JSONResponse(
                {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
            )
            await response(scope, receive, no_store_send)

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def safe_auth_handler(request: Request) -> Response:
            try:
                if request.method == "POST" and self.path == "/api/auth/login":
                    validate_admin_login_configuration()
                    # Only the ASGI peer is used; arbitrary forwarding headers are ignored.
                    client_ip = request.client.host if request.client else "unknown-peer"
                    login_limiter.check(client_ip)
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
                    # FastAPI's parser must see the same bounded body, not reread the stream.
                    request._body = bytes(body)
                return await original(request)
            except RequestValidationError:
                # FastAPI's default errors include input values, including passwords.
                return JSONResponse({"detail": "Invalid authentication request."}, status_code=422)
            except HTTPException as exc:
                return JSONResponse(
                    {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
                )

        return safe_auth_handler


router = APIRouter(prefix="/api/auth", tags=["Authentication"], route_class=AuthRoute)


class AdminLoginRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, str_strip_whitespace=False, hide_input_in_errors=True
    )
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    username: str
    role: Literal["admin"] = "admin"
    expires_at: int


class AdminSessionResponse(BaseModel):
    username: str
    role: Literal["admin"] = "admin"
    expires_at: int


@router.post("/login", response_model=AdminLoginResponse)
def admin_login(request: AdminLoginRequest) -> AdminLoginResponse:
    """Keep exact bootstrap credentials; allow only registered admins as fallback."""
    validate_admin_login_configuration()
    bootstrap_present = settings.admin_password_hash is not None or bool(settings.admin_password)
    if bootstrap_present and authenticate_admin(request.username, request.password):
        token = create_access_token(username=settings.admin_username, role="admin")
        return AdminLoginResponse(
            access_token=token, username=settings.admin_username,
            expires_at=decode_access_token(token)["exp"],
        )
    if managed_admin_login_enabled():
        account = authenticate_account(request.username, request.password)
        if account.role == "admin":
            token, expires_at = create_account_token(account)
            return AdminLoginResponse(
                access_token=token, username=account.username, expires_at=expires_at,
            )
    raise auth_error(status.HTTP_401_UNAUTHORIZED, "Invalid username or password.")


@router.get("/session", response_model=AdminSessionResponse)
def admin_session(admin: dict = Depends(require_admin)) -> AdminSessionResponse:
    return AdminSessionResponse(username=admin["sub"], expires_at=admin["exp"])
