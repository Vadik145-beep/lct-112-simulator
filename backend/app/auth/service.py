"""Login, lockout and token issuing logic (transport-agnostic, see router.py)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.config import get_settings
from app.errors import ApiError
from app.models import Role, User
from app.security import create_token, hash_password, verify_password

DEMO_LOGINS: dict[Role, str] = {
    Role.student: "student1",
    Role.teacher: "teacher1",
    Role.admin: "admin",
}


def _now() -> datetime:
    return datetime.now(UTC)


def issue_tokens(user: User) -> tuple[str, str]:
    """Returns (access_token, refresh_token)."""
    access = create_token(user.id, user.role.value, user.token_version, "access")
    refresh = create_token(user.id, user.role.value, user.token_version, "refresh")
    return access, refresh


async def authenticate(session: AsyncSession, login: str, password: str, ip: str | None) -> User:
    """Checks credentials, applies the lockout policy and records the attempt in the audit log."""
    settings = get_settings()
    user = await session.scalar(select(User).where(User.login == login.strip().lower()))
    if user is None:
        await write_audit(session, action="login_failed", details={"login": login}, ip=ip)
        await session.commit()
        raise ApiError(401, "bad_credentials", "Неверный логин или пароль.")

    if user.is_blocked:
        await write_audit(
            session, action="login_blocked", actor_id=user.id, actor_role=user.role.value, ip=ip
        )
        await session.commit()
        raise ApiError(
            403, "user_blocked", "Учётная запись заблокирована администратором. Обратитесь к нему."
        )

    now = _now()
    if user.locked_until and user.locked_until > now:
        until = user.locked_until.astimezone(ZoneInfo(settings.tz)).strftime("%H:%M")
        await write_audit(
            session, action="login_locked", actor_id=user.id, actor_role=user.role.value, ip=ip
        )
        await session.commit()
        raise ApiError(
            423,
            "login_locked",
            f"Вход заблокирован до {until} после {settings.login_max_attempts} неудачных "
            "попыток. Подождите и попробуйте снова.",
        )

    if not verify_password(password, user.password_hash):
        user.failed_attempts += 1
        locked = user.failed_attempts >= settings.login_max_attempts
        if locked:
            user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
            user.failed_attempts = 0
        await write_audit(
            session,
            action="login_failed",
            actor_id=user.id,
            actor_role=user.role.value,
            details={"locked": locked},
            ip=ip,
        )
        await session.commit()
        if locked:
            raise ApiError(
                423,
                "login_locked",
                f"Вход заблокирован на {settings.login_lock_minutes} минут после "
                f"{settings.login_max_attempts} неудачных попыток.",
            )
        raise ApiError(401, "bad_credentials", "Неверный логин или пароль.")

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    await write_audit(session, action="login", actor_id=user.id, actor_role=user.role.value, ip=ip)
    await session.commit()
    return user


async def demo_login(session: AsyncSession, role: Role, ip: str | None) -> User:
    settings = get_settings()
    if not settings.demo_mode:
        raise ApiError(404, "not_found", "Демо-вход выключен (DEMO_MODE=false).")
    user = await session.scalar(select(User).where(User.login == DEMO_LOGINS[role]))
    if user is None or user.is_blocked:
        raise ApiError(
            503, "demo_user_missing", "Демо-пользователь не создан. Выполните python -m app.seed."
        )
    user.last_login_at = _now()
    await write_audit(
        session, action="login_demo", actor_id=user.id, actor_role=user.role.value, ip=ip
    )
    await session.commit()
    return user


async def change_password(
    session: AsyncSession, user: User, old_password: str, new_password: str, ip: str | None
) -> User:
    if not verify_password(old_password, user.password_hash):
        raise ApiError(400, "bad_old_password", "Текущий пароль указан неверно.")
    if old_password == new_password:
        raise ApiError(400, "same_password", "Новый пароль должен отличаться от текущего.")
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.token_version += 1
    await write_audit(
        session, action="password_changed", actor_id=user.id, actor_role=user.role.value, ip=ip
    )
    await session.commit()
    return user


async def record_logout(session: AsyncSession, user: User | None, ip: str | None) -> None:
    await write_audit(
        session,
        action="logout",
        actor_id=user.id if user else None,
        actor_role=user.role.value if user else None,
        ip=ip,
    )
    await session.commit()
