"""Trainee API of the operator-112 card (PRD 11): the draft while the call goes on and the
final save that scores the attempt."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request

from app.audit import write_audit
from app.auth.deps import ActiveUser, DbSession, client_ip
from app.events import publish_events
from app.intake import service as intake
from app.intake.schemas import DraftRequest, DraftResponse, SubmitRequest, SubmitResponse
from app.training import present
from app.training.router import _attempt_out

router = APIRouter(tags=["intake"])


@router.put("/attempts/{attempt_id}/draft", response_model=DraftResponse)
async def save_draft(
    attempt_id: uuid.UUID, body: DraftRequest, user: ActiveUser, session: DbSession
) -> DraftResponse:
    """Autosave of the card (the client sends it at most every 2 s). Nothing is scored."""
    attempt, _ = await intake.own_call_attempt(session, attempt_id, user)
    saved_at, events = await intake.save_draft(session, attempt, body.card, body.updated_at)
    await session.commit()
    await publish_events(events)
    return DraftResponse(saved_at=saved_at, seq=await present.last_seq(session, attempt.session_id))


@router.post("/attempts/{attempt_id}/submit", response_model=SubmitResponse)
async def submit_card(
    attempt_id: uuid.UUID,
    body: SubmitRequest,
    user: ActiveUser,
    session: DbSession,
    request: Request,
) -> SubmitResponse:
    """«Сохранить»: closes the call, scores the card within the request (PRD 9.3) and issues
    the next call. Idempotent by ``client_submission_id``."""
    attempt, ts = await intake.own_call_attempt(session, attempt_id, user)
    result = await intake.submit_card(
        session, attempt, ts, user, body.card, body.client_submission_id
    )
    if result.applied:
        await write_audit(
            session,
            action="attempt.submit",
            actor_id=user.id,
            actor_role=user.role,
            entity="attempt",
            entity_id=str(attempt.id),
            details={"incident_type": body.card.incident_type, "services": body.card.services},
            ip=client_ip(request),
        )
        await session.commit()
        await publish_events(result.events)
    return SubmitResponse(
        attempt=await _attempt_out(session, attempt, ts),
        applied=result.applied,
        issued=[str(a.id) for a in result.issued],
    )
