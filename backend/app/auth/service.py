"""Login, lockout and token issuing logic (transport-agnostic, see router.py)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.config import get_settings
from app.errors import ApiError
from app.models import Role, User
from app.security import create_token, hash_password_async, verify_password_async

DEMO_LOGINS: dict[Role, str] = {
    Role.student: "student1",
    Role.teacher: "teacher1",
    Role.admin: "admin",
}
# The stand's accounts on a clean install (app.seed.clean_install_users).
STAND_LOGINS: dict[Role, str] = {
    Role.teacher: "teacher",
    Role.student: "student",
    Role.admin: "admin",
}
# The second trainee of the stand: the video «преподаватель + 2 обучаемых» (recommendation 9),
# shown and entered like the others (20.10.2026).
STAND_EXTRA_LOGINS: tuple[tuple[Role, str], ...] = ((Role.student, "student2"),)
# QUICK_LOGIN without SHOW_DEMO_CREDENTIALS: the administrator gets no button — his password
# is not public then.
QUICK_LOGINS: dict[Role, str] = {
    Role.teacher: "teacher",
    Role.student: "student",
}


def passwordless_logins() -> dict[Role, str]:
    """Accounts behind the «Войти как …» buttons of the login page; empty when they are off."""
    settings = get_settings()
    if settings.demo_mode:
        return DEMO_LOGINS
    if settings.quick_login:
        return STAND_LOGINS if settings.show_demo_credentials else QUICK_LOGINS
    return {}


def shown_credentials() -> list[tuple[Role, str]]:
    """Accounts whose login and password the login page shows (SHOW_DEMO_CREDENTIALS); their
    password is SEED_PASSWORD."""
    settings = get_settings()
    if not settings.show_demo_credentials:
        return []
    if settings.demo_mode:
        return list(DEMO_LOGINS.items())
    return [*STAND_LOGINS.items(), *STAND_EXTRA_LOGINS]


def quick_accounts() -> list[tuple[Role, str]]:
    """Every account a «Войти» button of the login page enters: one per role, and on the stand
    showing its credentials the second trainee too."""
    accounts = list(passwordless_logins().items())
    settings = get_settings()
    if accounts and settings.show_demo_credentials and not settings.demo_mode:
        accounts += list(STAND_EXTRA_LOGINS)
    return accounts


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

    if not await verify_password_async(password, user.password_hash):
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


async def demo_login(
    session: AsyncSession, role: Role, ip: str | None, login: str | None = None
) -> User:
    """One-click entry: the role's account, or ``login`` when it is one of the quick ones (the
    second trainee of the stand)."""
    target = login or passwordless_logins().get(role)
    if target is None or (role, target) not in quick_accounts():
        raise ApiError(404, "not_found", "Вход без пароля для этой роли выключен.")
    user = await session.scalar(select(User).where(User.login == target))
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
    if user.login in {login for _, login in shown_credentials()}:
        raise ApiError(
            409,
            "shown_password",
            "Пароль этой учётной записи показан на странице входа и общий для всех, "
            "его менять нельзя.",
        )
    if not await verify_password_async(old_password, user.password_hash):
        raise ApiError(400, "bad_old_password", "Текущий пароль указан неверно.")
    if old_password == new_password:
        raise ApiError(400, "same_password", "Новый пароль должен отличаться от текущего.")
    user.password_hash = await hash_password_async(new_password)
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
