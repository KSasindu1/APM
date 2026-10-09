
from hmac import compare_digest

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import create_access_token


router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


@router.post(
    "/login",
    response_model=AdminLoginResponse,
)
async def admin_login(
    request: AdminLoginRequest,
) -> AdminLoginResponse:
    """Authenticate the configured administrator."""

    username_matches = compare_digest(
        request.username,
        settings.admin_username,
    )

    password_matches = compare_digest(
        request.password,
        settings.admin_password,
    )

    if not (username_matches and password_matches):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(
        username=settings.admin_username,
        role="admin",
    )

    return AdminLoginResponse(
        access_token=token,
        token_type="bearer",
        username=settings.admin_username,
        role="admin",
    )