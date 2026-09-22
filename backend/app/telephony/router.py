"""Telephony API (PRD 11): the softphone account of the trainee, the call that rings, the
call controls of the panel (answer, hang up, «нет контакта», «срыв звонка») and the
recording. The telephony settings of the administrator are served by ``app.admin``."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.audit import write_audit
from app.auth.deps import ActiveUser, DbSession, client_ip, require_role
from app.config import get_settings
from app.dialog import call as call_state
from app.dialog import service as dialog
from app.dialog.router import _dialog_out, _turn_out
from app.errors import ApiError
from app.events import publish_events
from app.models import (
    ACTIVE_ATTEMPT_STATES,
    CALL_END_HANGUP,
    CALL_ENDED,
    MODE_CALL_INTAKE,
    SESSION_RUNNING,
    Attempt,
    Role,
    Scenario,
    TrainingSession,
    User,
)
from app.telephony import cloud_web, sip
from app.telephony import service as telephony
from app.telephony import settings as telephony_settings
from app.telephony.calls import _caller_id
from app.telephony.schemas import (
    AnswerResponse,
    CallResponse,
    CurrentCallOut,
    SipAccountOut,
)
from app.training import present
from app.training import service as training

router = APIRouter(tags=["telephony"])

Student = Annotated[User, Depends(require_role(Role.student))]


# ---------------------------------------------------------------- softphone


@router.get("/me/sip", response_model=SipAccountOut)
async def my_sip_account(user: Student, session: DbSession) -> SipAccountOut:
    """Credentials of the trainee's softphone; the account is created on first request."""
    s = get_settings()
    account, created = await sip.ensure_account(session, user)
    config = await telephony_settings.load(session)
    await session.commit()
    service = telephony.get_service()
    if created and service is not None:
        await service.sync_endpoints()
    return SipAccountOut(
        enabled=s.telephony_enabled,
        connected=telephony.telephony_active(),
        ws_path=s.sip_ws_path,
        domain=config.sip_domain,
        username=account.webrtc_endpoint,
        password=account.password,
        display_name=user.full_name,
        phone_username=account.phone_endpoint,
    )


@router.get("/me/call", response_model=CurrentCallOut | None)
async def my_current_call(user: Student, session: DbSession) -> CurrentCallOut | None:
    """The call-intake attempt that rings or is in progress: what the panel shows when the
    SIP call carries no attempt id (fallback without telephony)."""
    attempt = await session.scalar(
        select(Attempt)
        .join(TrainingSession, TrainingSession.id == Attempt.session_id)
        .where(
            Attempt.student_id == user.id,
            Attempt.mode == MODE_CALL_INTAKE,
            Attempt.state.in_(ACTIVE_ATTEMPT_STATES),
            Attempt.call_state != CALL_ENDED,
            TrainingSession.status == SESSION_RUNNING,
        )
        # The newest one: that is the call the softphone rings with right now.
        .order_by(Attempt.issued_at.desc())
        .limit(1)
    )
    if attempt is None:
        return None
    card = await training.load_scenario_card(session, attempt.scenario_id, attempt.scenario_version)
    from app.domain.evaluation.schemas import CallIntakeScenario

    scenario = CallIntakeScenario.model_validate(card.body)
    _, number = _caller_id(scenario)
    title = (await session.get(Scenario, attempt.scenario_id)).title
    return CurrentCallOut(
        attempt_id=str(attempt.id),
        session_id=str(attempt.session_id),
        card_number=attempt.card_number,
        call_state=attempt.call_state,
        answered_at=attempt.answered_at,
        caller_number=number,
        scenario_title=title,
        seq=await present.last_seq(session, attempt.session_id),
    )


# ---------------------------------------------------------------- call control


@router.post("/attempts/{attempt_id}/answer", response_model=AnswerResponse)
async def answer(attempt_id: uuid.UUID, user: ActiveUser, session: DbSession) -> AnswerResponse:
    """The operator picks up: the caller's opening is returned (with its voice file) and the
    attempt becomes «в разговоре». With telephony the SIP call is answered in the softphone;
    this call only records the state."""
    attempt, ts, version, scenario = await dialog.dialog_attempt(
        session, attempt_id, user, for_write=True
    )
    opening, events = await call_state.answer(
        session, attempt, ts, version, scenario, telephony=telephony.telephony_active()
    )
    await session.commit()
    await publish_events(events)
    return AnswerResponse(
        opening=_turn_out(0, opening) if opening else None,
        dialog=await _dialog_out(session, attempt, ts, scenario),
    )


async def _end_call(
    session: DbSession, attempt_id: uuid.UUID, user: User, reason: str, request: Request
) -> CallResponse:
    attempt = await training.get_attempt_for(session, attempt_id, user)
    if attempt.mode != MODE_CALL_INTAKE:
        raise ApiError(409, "not_call_intake", "Управление вызовом есть только в приёме вызова.")
    if user.role != Role.student or attempt.student_id != user.id:
        raise ApiError(403, "forbidden", "Управлять вызовом может только обучающийся.")
    if attempt.state not in ACTIVE_ATTEMPT_STATES:
        raise ApiError(409, "attempt_closed", "Карточка уже закрыта.")
    ts = await session.get(TrainingSession, attempt.session_id)
    card = await training.load_scenario_card(session, attempt.scenario_id, attempt.scenario_version)
    from app.domain.evaluation.schemas import CallIntakeScenario

    scenario = CallIntakeScenario.model_validate(card.body)
    if reason == "no_contact":
        events = await call_state.mark_no_contact(session, attempt)
    elif reason == "call_dropped":
        events = await call_state.mark_call_dropped(session, attempt)
    else:
        events = await call_state.end(session, attempt, reason)
    if events:
        # Ending a call is an action on the card (unlike dialog turns, which are training
        # content and are not audited — decision of wave 5).
        await write_audit(
            session,
            action="call.end",
            actor_id=user.id,
            actor_role=user.role,
            entity="attempt",
            entity_id=str(attempt.id),
            details={"reason": reason},
            ip=client_ip(request),
        )
    await session.commit()
    await publish_events(events)
    service = telephony.get_service()
    if service is not None:
        await service.calls.hangup(attempt.id, reason)
    web_calls = cloud_web.get_calls()
    if web_calls is not None:
        await web_calls.hangup(attempt.id)
    return CallResponse(dialog=await _dialog_out(session, attempt, ts, scenario))


@router.post("/attempts/{attempt_id}/hangup", response_model=CallResponse)
async def hangup(
    attempt_id: uuid.UUID, user: ActiveUser, session: DbSession, request: Request
) -> CallResponse:
    """«Завершить»: the operator ends the call; the card stays open for filling in."""
    return await _end_call(session, attempt_id, user, CALL_END_HANGUP, request)


@router.post("/attempts/{attempt_id}/no-contact", response_model=CallResponse)
async def no_contact(
    attempt_id: uuid.UUID, user: ActiveUser, session: DbSession, request: Request
) -> CallResponse:
    """«Нет контакта»: the caller cannot be reached; the mark goes to the evaluation."""
    return await _end_call(session, attempt_id, user, "no_contact", request)


@router.post("/attempts/{attempt_id}/call-dropped", response_model=CallResponse)
async def call_dropped(
    attempt_id: uuid.UUID, user: ActiveUser, session: DbSession, request: Request
) -> CallResponse:
    """«Срыв звонка»: the caller hung up; the mark goes to the evaluation."""
    return await _end_call(session, attempt_id, user, "call_dropped", request)


@router.get("/attempts/{attempt_id}/recording", include_in_schema=False)
async def recording(attempt_id: uuid.UUID, user: ActiveUser, session: DbSession) -> FileResponse:
    """WAV of the call for the owner and the teacher of the session."""
    attempt = await training.get_attempt_for(session, attempt_id, user)
    if not attempt.recording_path:
        raise ApiError(404, "recording_not_found", "Записи звонка нет.")
    root = (dialog.storage_root() / "recordings").resolve()
    path = (dialog.storage_root() / attempt.recording_path).resolve()
    if root not in path.parents or not path.is_file():
        raise ApiError(404, "recording_not_found", "Файл записи не найден.")
    return FileResponse(path, media_type="audio/wav", filename=f"call-{attempt.card_number}.wav")
