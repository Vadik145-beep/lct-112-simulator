"""Empty role cabinets. Real content arrives in later waves; the endpoints already
enforce roles so the frontend can show "no access" and tests can check 403."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.deps import require_role
from app.models import Role, User

router = APIRouter(tags=["cabinets"])


class CabinetOut(BaseModel):
    role: Role
    title: str
    sections: list[str]


@router.get("/student/cabinet", response_model=CabinetOut)
async def student_cabinet(
    user: Annotated[User, Depends(require_role(Role.student))],
) -> CabinetOut:
    return CabinetOut(
        role=user.role, title="Кабинет обучающегося", sections=["Мои задания", "Прогресс"]
    )


@router.get("/teacher/cabinet", response_model=CabinetOut)
async def teacher_cabinet(
    user: Annotated[User, Depends(require_role(Role.teacher))],
) -> CabinetOut:
    return CabinetOut(
        role=user.role,
        title="Кабинет преподавателя",
        sections=["Группы", "Занятия", "Сценарии", "Отчёты"],
    )


@router.get("/admin/cabinet", response_model=CabinetOut)
async def admin_cabinet(
    user: Annotated[User, Depends(require_role(Role.admin))],
) -> CabinetOut:
    return CabinetOut(
        role=user.role,
        title="Кабинет администратора",
        sections=["Пользователи", "Состояние", "Аудит", "Резервные копии"],
    )
