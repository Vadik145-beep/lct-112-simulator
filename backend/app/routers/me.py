from fastapi import APIRouter

from app.auth.deps import ActiveUser, CurrentUser, DbSession
from app.auth.schemas import UserOut
from app.errors import ApiError
from app.models import Role
from app.training.progress import ProgressOut, build_progress

router = APIRouter(tags=["me"])


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user, from_attributes=True)


@router.get("/me/progress", response_model=ProgressOut)
async def my_progress(user: ActiveUser, session: DbSession) -> ProgressOut:
    """«Мой прогресс» (PRD 13.7): scores per lesson, time, frequent errors, recommendations."""
    if user.role != Role.student:
        raise ApiError(403, "forbidden", "Прогресс есть только у обучающихся.")
    return await build_progress(session, user)
