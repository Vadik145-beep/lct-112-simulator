from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.audit import write_audit
from app.auth.deps import ActiveUser, CurrentUser, DbSession, client_ip
from app.auth.schemas import UserOut
from app.errors import ApiError
from app.models import Role
from app.telephony.settings import normalize_phone
from app.training.progress import ProgressOut, build_progress

router = APIRouter(tags=["me"])


class PhoneIn(BaseModel):
    # Empty = remove the number.
    phone: str = Field(default="", max_length=40)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user, from_attributes=True)


@router.put("/me/phone", response_model=UserOut)
async def set_my_phone(
    body: PhoneIn, user: ActiveUser, session: DbSession, request: Request
) -> UserOut:
    """The trainee's own phone: lessons with calls to the phone ring it (docs/MULTIFON.md)."""
    phone = None
    if body.phone.strip():
        phone = normalize_phone(body.phone)
        if not phone:
            raise ApiError(
                422,
                "bad_phone",
                "Нужен российский номер мобильного: 11 цифр, например 8 922 000-00-00.",
            )
    user.phone = phone
    await write_audit(
        session,
        action="user.phone",
        actor_id=user.id,
        actor_role=user.role,
        entity="user",
        entity_id=str(user.id),
        details={"set": phone is not None},
        ip=client_ip(request),
    )
    await session.commit()
    return UserOut.model_validate(user, from_attributes=True)


@router.get("/me/progress", response_model=ProgressOut)
async def my_progress(user: ActiveUser, session: DbSession) -> ProgressOut:
    """«Мой прогресс» (PRD 13.7): scores per lesson, time, frequent errors, recommendations."""
    if user.role != Role.student:
        raise ApiError(403, "forbidden", "Прогресс есть только у обучающихся.")
    return await build_progress(session, user)
