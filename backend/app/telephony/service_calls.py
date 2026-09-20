"""API of the dispatcher's calls to service officers (issue #36, «звено Б → В»).

``POST /attempts/{id}/service-call`` starts a call to the officer of a service from the card;
``…/say`` is the text path (also used by the softphone panel as a fallback), ``…/end`` hangs
up. With telephony on, the start rings the trainee's phones from the officer's number
(``CallManager.dial_service``) and the conversation goes over the SIP leg; without it the
officer answers at once in the training panel.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from app.audit import write_audit
from app.auth.deps import ActiveUser, DbSession, client_ip
from app.db import SessionLocal
from app.dialog import officer
from app.dialog import service as dialog
from app.domain.evaluation.schemas import CardResponseScenario
from app.errors import ApiError
from app.events import publish_events
from app.models import MODE_CARD_RESPONSE, Attempt, Role, TrainingSession, User
from app.telephony import service as telephony
from app.training import present
from app.training import service as training
from app.training.schemas import (
    ServiceCallRequest,
    ServiceCallResponse,
    ServiceCallSayRequest,
)

router = APIRouter(tags=["telephony"])


async def _card_attempt(
    session: DbSession, attempt_id: uuid.UUID, user: User
) -> tuple[Attempt, TrainingSession, training.ScenarioCard, CardResponseScenario]:
    attempt = await training.get_attempt_for(session, attempt_id, user)
    if attempt.mode != MODE_CARD_RESPONSE:
        raise ApiError(409, "not_card_response", "Звонки в службы есть только в реагировании.")
    if user.role != Role.student or attempt.student_id != user.id:
        raise ApiError(
            403, "forbidden", "Звонить в службу может только обучающийся в своей карточке."
        )
    if attempt.state in training.CLOSED_STATES:
        raise ApiError(409, "card_closed", "Работа с карточкой завершена: звонить уже нельзя.")
    ts = await session.get(TrainingSession, attempt.session_id)
    card = await training.load_scenario_card(session, attempt.scenario_id, attempt.scenario_version)
    return attempt, ts, card, CardResponseScenario.model_validate(card.body)


async def _respond(
    session: DbSession,
    attempt: Attempt,
    ts: TrainingSession,
    body: dict,
    call_id: str,
    *,
    pending_reply: bool = False,
    latency_ms: int = 0,
    applied: bool = True,
) -> ServiceCallResponse:
    lookups = await present.load_lookups(session, {body.get("card", {}).get("incident_type")})
    student = await session.get(User, attempt.student_id)
    out = present.attempt_out(
        attempt, body, ts, student, lookups, seq=await present.last_seq(session, ts.id)
    )
    call = next(c for c in out.service_calls if c.id == call_id)
    return ServiceCallResponse(
        call=call, attempt=out, pending_reply=pending_reply, latency_ms=latency_ms, applied=applied
    )


@router.post("/attempts/{attempt_id}/service-call", response_model=ServiceCallResponse)
async def start_service_call(
    attempt_id: uuid.UUID,
    body: ServiceCallRequest,
    user: ActiveUser,
    session: DbSession,
    request: Request,
) -> ServiceCallResponse:
    """«Позвонить» a service from the card: one call at a time, only while the card is open."""
    attempt, ts, card, scenario = await _card_attempt(session, attempt_id, user)
    services = await training.load_services(session)
    service = services.get(body.service)
    if service is None:
        raise ApiError(422, "unknown_service", f"Службы «{body.service}» нет в справочнике.")
    live = telephony.telephony_active()
    call, events = await officer.start(
        session, attempt, ts, card.version, scenario, service.code, service.title, telephony=live
    )
    await write_audit(
        session,
        action="service_call.start",
        actor_id=user.id,
        actor_role=user.role,
        entity="attempt",
        entity_id=str(attempt.id),
        details={"service": service.code, "call_id": call["id"], "telephony": live},
        ip=client_ip(request),
    )
    await session.commit()
    await publish_events(events)
    if live:
        manager = telephony.get_service()
        dialled = manager is not None and await manager.calls.dial_service(
            attempt.id, call["id"], service.code, service.title
        )
        if not dialled:
            # The phone could not be rung: fall back to the text path right away.
            async with SessionLocal() as fresh:
                current = await fresh.get(Attempt, attempt.id)
                _, answered = await officer.answer(
                    fresh, current, ts, card.version, scenario, call["id"]
                )
                await fresh.commit()
            await publish_events(answered)
            await session.refresh(attempt)
    return await _respond(session, attempt, ts, card.body, call["id"])


@router.post(
    "/attempts/{attempt_id}/service-call/{call_id}/say", response_model=ServiceCallResponse
)
async def say_to_officer(
    attempt_id: uuid.UUID,
    call_id: str,
    body: ServiceCallSayRequest,
    user: ActiveUser,
    session: DbSession,
) -> ServiceCallResponse:
    """A phrase of the dispatcher typed in the panel (the fallback of the voice path)."""
    attempt, ts, card, scenario = await _card_attempt(session, attempt_id, user)
    result = await officer.say(
        session, attempt, ts, card.version, scenario, call_id, body.text, action_id=body.action_id
    )
    if result.applied:
        await session.commit()
        await publish_events(result.events)
    return await _respond(
        session,
        attempt,
        ts,
        card.body,
        call_id,
        pending_reply=result.pending_reply,
        latency_ms=result.latency_ms,
        applied=result.applied,
    )


@router.post(
    "/attempts/{attempt_id}/service-call/{call_id}/end", response_model=ServiceCallResponse
)
async def end_service_call(
    attempt_id: uuid.UUID, call_id: str, user: ActiveUser, session: DbSession, request: Request
) -> ServiceCallResponse:
    """«Завершить»: the dispatcher hangs up; the transcript and the facts stay on the card."""
    attempt, ts, card, _ = await _card_attempt(session, attempt_id, user)
    call, events = await officer.end(session, attempt, call_id, officer.END_HANGUP)
    if events:
        await write_audit(
            session,
            action="service_call.end",
            actor_id=user.id,
            actor_role=user.role,
            entity="attempt",
            entity_id=str(attempt.id),
            details={"service": call["service"], "call_id": call_id},
            ip=client_ip(request),
        )
    await session.commit()
    await publish_events(events)
    manager = telephony.get_service()
    if manager is not None:
        await manager.calls.hangup_service_call(call_id)
    return await _respond(session, attempt, ts, card.body, call_id)


@router.get("/attempts/{attempt_id}/service-call/{call_id}/recording", include_in_schema=False)
async def service_call_recording(
    attempt_id: uuid.UUID, call_id: str, user: ActiveUser, session: DbSession
) -> FileResponse:
    """WAV of the call to the officer for the owner and the teacher of the session."""
    attempt = await training.get_attempt_for(session, attempt_id, user)
    call = officer.find_call(attempt, call_id)
    if not call.get("recording_path"):
        raise ApiError(404, "recording_not_found", "Записи звонка нет.")
    root = (dialog.storage_root() / "recordings").resolve()
    path = (dialog.storage_root() / call["recording_path"]).resolve()
    if root not in path.parents or not path.is_file():
        raise ApiError(404, "recording_not_found", "Файл записи не найден.")
    return FileResponse(
        path, media_type="audio/wav", filename=f"service-call-{attempt.card_number}.wav"
    )


@router.get("/attempts/{attempt_id}/service-call/{call_id}", response_model=ServiceCallResponse)
async def get_service_call(
    attempt_id: uuid.UUID,
    call_id: str,
    user: ActiveUser,
    session: DbSession,
    after: Annotated[int | None, Query()] = None,
) -> ServiceCallResponse:
    """The call as it is now (the panel polls it during a SIP call for the transcript)."""
    del after
    attempt = await training.get_attempt_for(session, attempt_id, user)
    if attempt.mode != MODE_CARD_RESPONSE:
        raise ApiError(409, "not_card_response", "Звонки в службы есть только в реагировании.")
    officer.find_call(attempt, call_id)
    ts = await session.get(TrainingSession, attempt.session_id)
    card = await training.load_scenario_card(session, attempt.scenario_id, attempt.scenario_version)
    return await _respond(session, attempt, ts, card.body, call_id)
