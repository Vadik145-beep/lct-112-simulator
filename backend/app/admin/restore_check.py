"""Checks a restored database (scripts/restore_test.sh): the user exists, the password
verifies, the audit chain is whole and the training tables are readable.

    DATABASE_URL=... python -m app.admin.restore_check --login admin

Exit code 0 = the restored copy is usable; the numbers are printed for the log.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.audit import verify_chain
from app.config import get_settings
from app.models import Attempt, AuditLog, Scenario, TrainingSession, User
from app.security import verify_password


async def check(login: str, password: str) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            user = await session.scalar(select(User).where(User.login == login))
            if user is None:
                print(f"пользователь {login} не найден в восстановленной базе")
                return 1
            if not verify_password(password, user.password_hash):
                print(f"пароль пользователя {login} не подходит")
                return 1
            ok, broken = await verify_chain(session)
            counts = {}
            for name, model in (
                ("users", User),
                ("scenarios", Scenario),
                ("sessions", TrainingSession),
                ("attempts", Attempt),
                ("audit", AuditLog),
            ):
                counts[name] = await session.scalar(select(func.count()).select_from(model))
            print(
                f"вход {login}: ок; аудит: {'цел' if ok else f'нарушен на {broken}'}; "
                + ", ".join(f"{k}={v}" for k, v in counts.items())
            )
            return 0 if ok else 1
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", default="admin")
    parser.add_argument("--password", default=None, help="по умолчанию SEED_PASSWORD")
    args = parser.parse_args()
    password = args.password or get_settings().seed_password
    sys.exit(asyncio.run(check(args.login, password)))


if __name__ == "__main__":
    main()
