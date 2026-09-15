import uuid
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth import service
from app.auth.deps import CurrentUser, DbSession, client_ip
from app.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    MessageResponse,
    TokenResponse,
    UserOut,
)
from app.config import get_settings
from app.errors import ApiError
from app.models import Role, User
from app.security import decode_token

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/api/auth"
_optional_bearer = HTTPBearer(auto_error=False)


def _set_refresh_cookie(response: Response, refresh: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=settings.refresh_token_days * 24 * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )


def _token_response(response: Response, user: User) -> TokenResponse:
    access, refresh = service.issue_tokens(user)
    _set_refresh_cookie(response, refresh)
    return TokenResponse(
        access_token=access,
        expires_in=get_settings().access_token_minutes * 60,
        user=UserOut.model_validate(user, from_attributes=True),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest, request: Request, response: Response, session: DbSession
) -> TokenResponse:
    user = await service.authenticate(session, body.login, body.password, client_ip(request))
    return _token_response(response, user)


@router.post("/demo/{role}", response_model=TokenResponse)
async def demo(
    role: Role, request: Request, response: Response, session: DbSession
) -> TokenResponse:
    user = await service.demo_login(session, role, client_ip(request))
    return _token_response(response, user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    session: DbSession,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> TokenResponse:
    payload = decode_token(refresh_token, "refresh") if refresh_token else None
    if payload is None:
        raise ApiError(401, "refresh_invalid", "Сессия истекла. Войдите заново.")
    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None or user.is_blocked or user.token_version != payload.get("tv"):
        response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
        raise ApiError(401, "refresh_invalid", "Сессия недействительна. Войдите заново.")
    return _token_response(response, user)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    response: Response,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)],
) -> MessageResponse:
    user = None
    if credentials:
        payload = decode_token(credentials.credentials, "access")
        if payload:
            user = await session.get(User, uuid.UUID(payload["sub"]))
    await service.record_logout(session, user, client_ip(request))
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
    return MessageResponse(message="Вы вышли из системы.")


@router.post("/change-password", response_model=TokenResponse)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    session: DbSession,
    user: CurrentUser,
) -> TokenResponse:
    user = await service.change_password(
        session, user, body.old_password, body.new_password, client_ip(request)
    )
    return _token_response(response, user)
