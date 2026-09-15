"""FastAPI dependencies: current user and role checks."""

import uuid
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import ApiError
from app.models import Role, User
from app.security import decode_token

_bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-real-ip")
    if forwarded:
        return forwarded
    return request.client.host if request.client else None


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """User from the Bearer access token. Allowed even if the password must be changed."""
    if credentials is None:
        raise ApiError(401, "unauthorized", "Требуется вход в систему.")
    payload = decode_token(credentials.credentials, "access")
    if payload is None:
        raise ApiError(401, "token_invalid", "Сессия истекла или недействительна. Войдите заново.")
    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None or user.is_blocked or user.token_version != payload.get("tv"):
        raise ApiError(401, "token_invalid", "Сессия недействительна. Войдите заново.")
    return user


async def get_active_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Like get_current_user but refuses users who still have to change their password."""
    if user.must_change_password:
        raise ApiError(
            403,
            "password_change_required",
            "Перед началом работы нужно сменить пароль.",
        )
    return user


def require_role(*roles: Role) -> Callable[..., Coroutine[Any, Any, User]]:
    async def _dep(user: Annotated[User, Depends(get_active_user)]) -> User:
        if user.role not in roles:
            raise ApiError(403, "forbidden", "Нет доступа: действие недоступно для вашей роли.")
        return user

    return _dep


CurrentUser = Annotated[User, Depends(get_current_user)]
ActiveUser = Annotated[User, Depends(get_active_user)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
