"""POST /grammar/check — grammar check on demand (teacher). The response carries
``method`` so the client knows whether LanguageTool answered or the text is unchecked."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.deps import require_role
from app.models import Role, User
from app.providers.grammar import DEFAULT_LANGUAGE, MAX_TEXT_LENGTH, get_grammar_provider

router = APIRouter(tags=["grammar"])


class GrammarCheckIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    language: str = DEFAULT_LANGUAGE


class GrammarMatchOut(BaseModel):
    offset: int
    length: int
    message: str
    rule_id: str
    category: str
    replacements: list[str]


class GrammarCheckOut(BaseModel):
    method: str
    available: bool
    error_count: int
    matches: list[GrammarMatchOut]


@router.post("/grammar/check", response_model=GrammarCheckOut)
async def grammar_check(
    body: GrammarCheckIn,
    user: Annotated[User, Depends(require_role(Role.teacher, Role.admin))],
) -> GrammarCheckOut:
    result = await get_grammar_provider().check(body.text, body.language)
    return GrammarCheckOut(
        method=result.method,
        available=result.available,
        error_count=result.error_count,
        matches=[GrammarMatchOut(**m.__dict__) for m in result.matches],
    )
