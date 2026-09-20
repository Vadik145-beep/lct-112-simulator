"""Administrator's user management (PRD 3, 13.7): the list with hidden names, creation with
a role and a service, blocking, password reset and the trainee's SIP account.

Names are personal data (152-ФЗ): the list carries initials only, the full name is given by
``reveal`` and that request is written to the audit log.
"""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.schemas import AdminUserIn, AdminUserOut, AdminUserPatch
from app.errors import ApiError
from app.models import Role, Service, User
from app.security import hash_password

# Bytes of randomness of a temporary password (url-safe text, 12 characters).
_TEMP_PASSWORD_BYTES = 9


def initials(full_name: str) -> str:
    parts = [p for p in full_name.replace("-", " ").split() if p]
    return " ".join(f"{p[0].upper()}." for p in parts) or "—"


def user_out(user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        login=user.login,
        display_name=initials(user.full_name),
        role=user.role,
        service_code=user.service_code,
        is_blocked=user.is_blocked,
        must_change_password=user.must_change_password,
        has_sip_account=user.sip_password_enc is not None,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def new_temporary_password() -> str:
    return secrets.token_urlsafe(_TEMP_PASSWORD_BYTES)


async def list_users(
    session: AsyncSession, *, role: Role | None = None, query: str | None = None
) -> list[User]:
    stmt = select(User).order_by(User.role, User.login)
    if role is not None:
        stmt = stmt.where(User.role == role)
    if query:
        stmt = stmt.where(User.login.ilike(f"%{query.strip()}%"))
    return list(await session.scalars(stmt))


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise ApiError(404, "user_not_found", "Пользователь не найден.")
    return user


async def _check_service(session: AsyncSession, code: str | None) -> None:
    if code is None:
        return
    if await session.get(Service, code) is None:
        raise ApiError(422, "service_not_found", f"Служба «{code}» не найдена в справочнике.")


def _check_service_for_role(role: Role, code: str | None) -> None:
    """A trainee is a dispatcher of one service (the journal tab they answer in); teachers
    and administrators have none (docs/BUGS.md, 4)."""
    if role == Role.student and not code:
        raise ApiError(422, "service_required", "Для обучающегося укажите службу ДДС.")
    if role != Role.student and code:
        raise ApiError(422, "service_not_applicable", "Служба задаётся только обучающемуся ДДС.")


async def create_user(session: AsyncSession, body: AdminUserIn) -> tuple[User, str]:
    """Creates the user; returns it with the temporary password (given or generated).
    Any first login has to change the password."""
    login = body.login.strip().lower()
    if await session.scalar(select(User).where(User.login == login)):
        raise ApiError(409, "login_taken", f"Логин «{login}» уже занят.")
    _check_service_for_role(body.role, body.service_code)
    await _check_service(session, body.service_code)
    password = body.password or new_temporary_password()
    user = User(
        login=login,
        full_name=body.full_name.strip(),
        role=body.role,
        service_code=body.service_code,
        password_hash=hash_password(password),
        must_change_password=True,
    )
    session.add(user)
    await session.flush()
    return user, password


async def update_user(session: AsyncSession, user: User, body: AdminUserPatch, actor: User) -> dict:
    """Applies the patch and returns what changed (for the audit)."""
    changes: dict = {}
    if body.full_name is not None and body.full_name.strip() != user.full_name:
        user.full_name = body.full_name.strip()
        changes["full_name"] = True
    if body.role is not None and body.role != user.role:
        if user.id == actor.id:
            raise ApiError(409, "self_role", "Свою роль изменить нельзя.")
        user.role = body.role
        changes["role"] = body.role.value
    if body.clear_service:
        user.service_code = None
        changes["service_code"] = None
    elif body.service_code is not None:
        await _check_service(session, body.service_code)
        user.service_code = body.service_code
        changes["service_code"] = body.service_code
    if user.role != Role.student and user.service_code and "role" in changes:
        # Promoted from trainee: the service goes with the old role.
        user.service_code = None
        changes["service_code"] = None
    _check_service_for_role(user.role, user.service_code)
    if body.is_blocked is not None and body.is_blocked != user.is_blocked:
        if user.id == actor.id:
            raise ApiError(409, "self_block", "Себя заблокировать нельзя.")
        user.is_blocked = body.is_blocked
        if body.is_blocked:
            # Open sessions of a blocked user end at once (refresh tokens become invalid).
            user.token_version += 1
        changes["is_blocked"] = body.is_blocked
    await session.flush()
    return changes


async def reset_password(session: AsyncSession, user: User) -> str:
    password = new_temporary_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.failed_attempts = 0
    user.locked_until = None
    user.token_version += 1
    await session.flush()
    return password
