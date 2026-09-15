"""Creates demo users. Idempotent: existing users keep their passwords and flags.

Run: python -m app.seed
"""

import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.logging import configure_logging, get_logger
from app.models import Role, User
from app.security import hash_password

log = get_logger(__name__)

# Service profiles are assigned to students so wave 3 can route cards by service.
# Codes are those of the ``services`` table (see app.importers.classifier.SERVICE_COLUMNS).
_STUDENT_SERVICES = ["territorial_oiv", "gkh", "gormost", "mosvodostok", "mosgaz", "moslift"]


def demo_users() -> list[dict]:
    users = [
        {"login": "admin", "full_name": "Администратор системы", "role": Role.admin},
        {"login": "teacher1", "full_name": "Иванова Мария Петровна", "role": Role.teacher},
        {"login": "teacher2", "full_name": "Сидоров Алексей Николаевич", "role": Role.teacher},
    ]
    surnames = [
        "Кузнецов",
        "Смирнова",
        "Попов",
        "Васильева",
        "Петров",
        "Соколова",
        "Михайлов",
        "Новикова",
        "Фёдоров",
        "Морозова",
        "Волков",
        "Алексеева",
    ]
    for i, surname in enumerate(surnames, start=1):
        users.append(
            {
                "login": f"student{i}",
                "full_name": f"{surname} Обучающийся {i}",
                "role": Role.student,
                "service_code": _STUDENT_SERVICES[(i - 1) % len(_STUDENT_SERVICES)],
            }
        )
    return users


async def seed() -> int:
    settings = get_settings()
    password_hash = hash_password(settings.seed_password)
    created = 0
    async with SessionLocal() as session:
        existing = set(await session.scalars(select(User.login)))
        for spec in demo_users():
            if spec["login"] in existing:
                continue
            session.add(
                User(
                    password_hash=password_hash,
                    must_change_password=not settings.demo_mode,
                    **spec,
                )
            )
            created += 1
        await session.commit()
    log.info("seed finished", created=created, skipped=len(demo_users()) - created)
    return created


if __name__ == "__main__":
    configure_logging(get_settings().log_level)
    asyncio.run(seed())
