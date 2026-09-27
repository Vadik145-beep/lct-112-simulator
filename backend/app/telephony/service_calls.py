"""API of the dispatcher's calls to service officers (issue #36, «звено Б → В»).

``POST /attempts/{id}/service-call`` starts a call to the officer of a service from the card;
``…/answer`` picks up the squad's incoming report in the card (issue #103), ``…/say`` is the
text path (also used by the softphone panel as a fallback), ``…/end`` hangs up. With
telephony on, the start rings the trainee's phones from the officer's number
(``CallManager.dial_service``) and the conversation goes over the SIP leg; without it the
officer answers at once in the training panel. A report is never answered for the trainee:
it rings in the card until «Ответить» (issue #103).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.audit import write_audit
from app.auth.deps import ActiveUser, DbSession, client_ip
from app.db import SessionLocal
from app.dialog import officer
from app.dialog import service as dialog
from app.domain.evaluation.schemas import CardResponseScenario
from app.domain.scenarios import caller_back
from app.errors import ApiError
from app.events import publish_events
from app.models import MODE_CARD_RESPONSE, Attempt, Role, TrainingSession, User
from app.providers.stt import Transcript, get_stt_provider
from app.telephony import service as telephony
from app.training import present
from app.training import service as training
from app.training.schemas import (
    ServiceCallRequest,
    ServiceCallResponse,
    ServiceCallSayRequest,
)

router = APIRouter(tags=["telephony"])

MAX_UTTERANCE_BYTES = 10 * 1024 * 1024
SILENCE_PEAK = 0.01  # below this the microphone sent nothing but noise floor


def caller_number(scenario: CardResponseScenario) -> str:
    """The phone of the card's caller: the dispatcher calls it back directly."""
    return (scenario.card.caller.phone or "").strip()


def nothing_recognized_message(transcript: Transcript) -> str:
    """Tells a silent microphone from speech the recognizer did not catch."""
    seconds = transcript.duration_seconds
    if not seconds:
        return "Речь не распознана: запись пустая. Удерживайте кнопку, пока говорите."
    if 0 <= transcript.peak < SILENCE_PEAK:
        return (
            f"Записано {seconds:.1f} с, но микрофон передал тишину. Проверьте в настройках "
            "браузера и системы, какой микрофон используется, и не выключен ли он."
        )
    return (
        f"Речь не распознана (записано {seconds:.1f} с, звук есть, слов не разобрано). "
        "Скажите фразу ещё раз громче и ближе к микрофону."
    )


async def card_attempt(
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
    heard_text: str | None = None,
) -> ServiceCallResponse:
    lookups = await present.load_lookups(session, {body.get("card", {}).get("incident_type")})
    student = await session.get(User, attempt.student_id)
    out = present.attempt_out(
        attempt, body, ts, student, lookups, seq=await present.last_seq(session, ts.id)
    )
    call = next(c for c in out.service_calls if c.id == call_id)
    return ServiceCallResponse(
        call=call,
        attempt=out,
        pending_reply=pending_reply,
        latency_ms=latency_ms,
        applied=applied,
        stt_available=get_stt_provider().method != "unavailable",
        heard_text=heard_text,
    )


@router.post("/attempts/{attempt_id}/service-call", response_model=ServiceCallResponse)
async def start_service_call(
    attempt_id: uuid.UUID,
    body: ServiceCallRequest,
    user: ActiveUser,
    session: DbSession,
    request: Request,
) -> ServiceCallResponse:
    """«Позвонить» from the card: one call at a time, only while the card is open. The target
    is a service of the strip or the caller of the card himself (``officer.CALLER_TARGET``,
    ответ заказчика 23.09.2026: «диспетчер ДДС может напрямую выйти на заявителя»)."""
    attempt, ts, card, scenario = await card_attempt(session, attempt_id, user)
    to_caller = body.service == officer.CALLER_TARGET
    if to_caller:
        code, title = officer.CALLER_TARGET, caller_back.caller_name(scenario)
        if not caller_number(scenario):
            raise ApiError(
                422, "no_caller_phone", "В карточке нет телефона заявителя: звонить некуда."
            )
    else:
        services = await training.load_services(session)
        service = services.get(body.service)
        if service is None:
            raise ApiError(422, "unknown_service", f"Службы «{body.service}» нет в справочнике.")
        code, title = service.code, service.title
    live = telephony.for_session(ts)
    # В облачном занятии без телефонии трубку снимает браузерный звонок: первую фразу
    # говорит сам провайдер (см. cloud_router.start_service_web_call).
    cloud = dialog.cloud_lesson(ts) and not live
    call, events = await officer.start(
        session,
        attempt,
        ts,
        card.version,
        scenario,
        code,
        title,
        telephony=live,
        answer_now=not cloud,
        kind=officer.KIND_CALLER if to_caller else officer.KIND_OUTGOING,
    )
    await write_audit(
        session,
        action="service_call.start",
        actor_id=user.id,
        actor_role=user.role,
        entity="attempt",
        entity_id=str(attempt.id),
        details={"service": code, "call_id": call["id"], "telephony": live},
        ip=client_ip(request),
    )
    await session.commit()
    await publish_events(events)
    if live:
        manager = telephony.get_service()
        dialled = manager is not None and await manager.calls.dial_service(
            attempt.id,
            call["id"],
            code,
            title,
            kind=call["kind"],
            number=caller_number(scenario) if to_caller else None,
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
    attempt, ts, card, scenario = await card_attempt(session, attempt_id, user)
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
    "/attempts/{attempt_id}/service-call/{call_id}/utterance", response_model=ServiceCallResponse
)
async def speak_to_officer(
    attempt_id: uuid.UUID,
    call_id: str,
    user: ActiveUser,
    session: DbSession,
    file: Annotated[UploadFile, File(description="Речь диспетчера: WAV, WebM/Opus или OGG")],
    action_id: Annotated[str | None, Form(max_length=64)] = None,
) -> ServiceCallResponse:
    """A spoken phrase from the browser microphone (no telephony): recognised by the ``stt``
    service with the card's street as a hint, then handled like ``say``. Without the service
    the answer is 503 and the dispatcher types instead — as in the 112 operator's card."""
    attempt, ts, card, scenario = await card_attempt(session, attempt_id, user)
    record = officer.find_call(attempt, call_id)
    if record.get("ended_at"):
        raise ApiError(409, "call_ended", "Звонок завершён: дежурному больше не сказать.")
    audio = await file.read()
    if not audio:
        raise ApiError(422, "empty_audio", "Пустая запись: скажите фразу ещё раз.")
    if len(audio) > MAX_UTTERANCE_BYTES:
        raise ApiError(413, "audio_too_large", "Запись больше 10 МБ.")
    address = scenario.card.address
    hints = [h for h in (address.street, address.district, record.get("service_title")) if h]
    transcript = await get_stt_provider().transcribe(audio, file.filename or "audio.wav", hints)
    if not transcript.available:
        raise ApiError(
            503,
            "stt_unavailable",
            "Распознавание речи недоступно: сервис stt не запущен. Введите фразу текстом.",
        )
    if not transcript.text:
        raise ApiError(422, "nothing_recognized", nothing_recognized_message(transcript))
    result = await officer.say(
        session,
        attempt,
        ts,
        card.version,
        scenario,
        call_id,
        transcript.text,
        action_id=action_id,
        heard=True,
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
        heard_text=transcript.text,
    )


@router.post(
    "/attempts/{attempt_id}/service-call/{call_id}/answer", response_model=ServiceCallResponse
)
async def answer_service_call(
    attempt_id: uuid.UUID, call_id: str, user: ActiveUser, session: DbSession, request: Request
) -> ServiceCallResponse:
    """«Ответить» on the squad's incoming report (issue #103). With telephony the trainee
    answers the phone and Asterisk reports it; this is the path of the card without
    telephony. Answering twice changes nothing."""
    attempt, ts, card, scenario = await card_attempt(session, attempt_id, user)
    call, events = await officer.answer(session, attempt, ts, card.version, scenario, call_id)
    if events:
        await write_audit(
            session,
            action="service_call.answer",
            actor_id=user.id,
            actor_role=user.role,
            entity="attempt",
            entity_id=str(attempt.id),
            details={"service": call["service"], "call_id": call_id, "kind": call.get("kind")},
            ip=client_ip(request),
        )
    await session.commit()
    await publish_events(events)
    return await _respond(session, attempt, ts, card.body, call_id)


@router.post(
    "/attempts/{attempt_id}/service-call/{call_id}/end", response_model=ServiceCallResponse
)
async def end_service_call(
    attempt_id: uuid.UUID, call_id: str, user: ActiveUser, session: DbSession, request: Request
) -> ServiceCallResponse:
    """«Завершить»: the dispatcher hangs up; the transcript and the facts stay on the card."""
    attempt, ts, card, _ = await card_attempt(session, attempt_id, user)
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
