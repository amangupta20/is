"""Authentication routes for assistant-core."""

from __future__ import annotations

import hmac
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from assistant_core.api.dependencies import require_session_auth
from assistant_core.auth.session import SESSION_MAX_AGE_SECONDS, create_session_token
from assistant_core.config import Settings

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Login request payload."""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=1_000)


class LoginResponse(BaseModel):
    """Login response payload with session token."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    token: str


class CheckAuthResponse(BaseModel):
    """Session check response payload."""

    model_config = ConfigDict(extra="forbid")

    authenticated: bool = True


class LogoutResponse(BaseModel):
    """Logout response payload."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


@router.post("/login", response_model=LoginResponse)
async def login(
    login_request: LoginRequest,
    request: Request,
    response: Response,
) -> LoginResponse:
    """Authenticate with admin password and establish a session."""
    settings: Settings = request.app.state.settings
    expected_password = settings.effective_admin_password

    if not hmac.compare_digest(login_request.password, expected_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid password",
        )

    token = create_session_token(settings.hmac_secret)
    response.set_cookie(
        key="assistant_session",
        value=token,
        httponly=True,
        samesite="strict",
        secure=False,
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
    )
    return LoginResponse(status="ok", token=token)


@router.get(
    "/check",
    response_model=CheckAuthResponse,
    dependencies=[Depends(require_session_auth)],
)
async def check_auth() -> CheckAuthResponse:
    """Check whether the current session is valid."""
    return CheckAuthResponse(authenticated=True)


@router.post("/logout", response_model=LogoutResponse)
async def logout(response: Response) -> LogoutResponse:
    """Clear the session cookie."""
    response.delete_cookie(
        key="assistant_session",
        path="/",
        httponly=True,
        samesite="strict",
        secure=False,
    )
    return LogoutResponse(status="ok")
