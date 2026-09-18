"""Analytics endpoints (PRD 11): group analytics with the readiness forecast for the
teacher and the forecast model card. The trainee's «Мой прогресс» (``/me/progress``) lives in
``app.training.progress`` and takes its ratings and dynamics from ``service.progress_extras``."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.analytics import service
from app.analytics.schemas import GroupAnalyticsOut, ReadinessModelOut
from app.auth.deps import DbSession, require_role
from app.models import Role, User
from app.training import sessions as lessons

router = APIRouter(tags=["analytics"])

Teacher = Annotated[User, Depends(require_role(Role.teacher))]


@router.get("/analytics/groups/{group_id}", response_model=GroupAnalyticsOut)
async def group_analytics(
    group_id: uuid.UUID,
    user: Teacher,
    session: DbSession,
    days: Annotated[int, Query(ge=1, le=service.MAX_PERIOD_DAYS)] = service.DEFAULT_PERIOD_DAYS,
) -> GroupAnalyticsOut:
    """Heat map, dynamics, typical errors and the readiness forecast of the teacher's group
    over the last ``days`` days."""
    group = await lessons.own_group(session, group_id, user)
    return await service.group_analytics(session, group, days=days)


@router.get("/analytics/readiness-model", response_model=ReadinessModelOut)
async def readiness_model(user: Teacher) -> ReadinessModelOut:
    """How far the forecast can be trusted: metrics on the held-out part of the cohort."""
    return service.readiness_model_info()
