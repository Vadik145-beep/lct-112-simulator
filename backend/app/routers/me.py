from fastapi import APIRouter

from app.auth.deps import CurrentUser
from app.auth.schemas import UserOut

router = APIRouter(tags=["me"])


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user, from_attributes=True)
