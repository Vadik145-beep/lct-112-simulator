"""Trainee API of the caller dialog in the call-intake mode (PRD 11): the transcript, a typed
phrase, a spoken phrase (recognized by the ``stt`` service), a topic button, and the voiced
replies as media files."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse

from app.auth.deps import ActiveUser, DbSession
from app.config import get_settings
from app.dialog import service as dialog
from app.dialog.schemas import (
    AskTopicRequest,
    CallOut,
    DialogOut,
    DialogTurnOut,
    SayRequest,
    TopicOut,
    TurnResponse,
)
from app.domain.evaluation.schemas import CallIntakeScenario
from app.domain.reference_data import CALLER_TOPICS
from app.errors import ApiError
from app.events import publish_events
from app.models import Attempt, TrainingSession
from app.providers.stt import get_stt_provider
from app.training import present

router = APIRouter(tags=["dialog"])

MAX_UTTERANCE_BYTES = 10 * 1024 * 1024
MEDIA_PREFIX = "/api/media/"


def _turn_out(index: int, turn: dict) -> DialogTurnOut:
    audio = turn.get("audio")
    return DialogTurnOut(
        index=index,
        role=turn["role"],
        text=turn["text"],
        topics=turn.get("topics", []),
        at=turn.get("at"),
        reply_id=turn.get("reply_id"),
        method=turn.get("method"),
        audio_url=f"{MEDIA_PREFIX}{audio}" if audio else None,
        generated=bool(turn.get("generated")),
        heard=bool(turn.get("heard")),
        latency_ms=turn.get("latency_ms"),
    )


def call_out(attempt: Attempt, ts: TrainingSession | None = None) -> CallOut:
    from app.telephony import settings as telephony_settings
    from app.telephony.calls import lesson_phone_only
    from app.telephony.service import telephony_active

    phone = telephony_settings.normalize_phone(ts.phone) if lesson_phone_only(ts) else ""
    return CallOut(
        state=attempt.call_state,
        end_reason=attempt.call_end_reason,
        ended_at=attempt.call_ended_at,
        call_dropped_marked=attempt.call_dropped_marked,
        no_contact_marked=attempt.no_contact_marked,
        telephony=telephony_active(),
        recording_available=bool(attempt.recording_path),
        phone=phone or None,
    )


async def _dialog_out(
    session: DbSession, attempt: Attempt, ts: TrainingSession, scenario: CallIntakeScenario
) -> DialogOut:
    covered = dialog.covered_topics(attempt, ts)
    required = set(scenario.required_topics)
    provider = dialog.provider_for(ts)
    # In the cloud mode (plan/track-c-vapi.md) the caller lives in Vapi; the local provider
    # only stands in when the cloud is unreachable, and its replies count as fallbacks.
    mode = dialog.EXTERNAL_METHOD if dialog.cloud_lesson(ts) else provider.mode
    return DialogOut(
        attempt_id=str(attempt.id),
        mode=mode,
        requested_mode=ts.dialog_mode or get_settings().dialog_mode,
        fallback_replies=dialog.fallback_replies(attempt, mode),
        stt_available=dialog.stt_available(),
        tts_available=dialog.tts_available(),
        answered_at=attempt.answered_at,
        call=call_out(attempt, ts),
        turns=[_turn_out(i, t) for i, t in enumerate(attempt.dialog)],
        topics=[
            TopicOut(
                code=t["code"],
                title=t["title"],
                required=t["code"] in required,
                covered=t["code"] in covered,
            )
            for t in CALLER_TOPICS
        ],
        required_topics=scenario.required_topics,
        seq=await present.last_seq(session, ts.id),
    )


async def _respond(
    session: DbSession,
    attempt: Attempt,
    ts: TrainingSession,
    scenario: CallIntakeScenario,
    result: dialog.TurnResult,
    heard_text: str | None = None,
) -> TurnResponse:
    if result.applied:
        await session.commit()
        await publish_events(result.events)
    turns = attempt.dialog
    op_index = next(
        (i for i in range(len(turns) - 1, -1, -1) if turns[i] is result.operator),
        max(len(turns) - 2, 0),
    )
    return TurnResponse(
        operator=_turn_out(op_index, result.operator),
        caller=_turn_out(op_index + 1, result.caller),
        applied=result.applied,
        pending_reply=result.pending_reply,
        latency_ms=result.latency_ms,
        heard_text=heard_text,
        call_ended=result.call_ended,
        dialog=await _dialog_out(session, attempt, ts, scenario),
    )


@router.get("/attempts/{attempt_id}/dialog", response_model=DialogOut)
async def get_dialog(attempt_id: uuid.UUID, user: ActiveUser, session: DbSession) -> DialogOut:
    """The transcript so far with the topics covered (teacher sees it read-only)."""
    attempt, ts, _, scenario = await dialog.dialog_attempt(
        session, attempt_id, user, for_write=False
    )
    return await _dialog_out(session, attempt, ts, scenario)


@router.post("/attempts/{attempt_id}/say", response_model=TurnResponse)
async def say(
    attempt_id: uuid.UUID, body: SayRequest, user: ActiveUser, session: DbSession
) -> TurnResponse:
    """A typed phrase of the operator; the caller answers in the session's dialog mode."""
    attempt, ts, version, scenario = await dialog.dialog_attempt(
        session, attempt_id, user, for_write=True
    )
    result = await dialog.say(
        session, attempt, ts, version, scenario, body.text.strip(), action_id=body.action_id
    )
    return await _respond(session, attempt, ts, scenario, result)


@router.post("/attempts/{attempt_id}/ask-topic", response_model=TurnResponse)
async def ask_topic(
    attempt_id: uuid.UUID, body: AskTopicRequest, user: ActiveUser, session: DbSession
) -> TurnResponse:
    """A topic button («Адрес», «Пострадавшие»…): works without any model."""
    attempt, ts, version, scenario = await dialog.dialog_attempt(
        session, attempt_id, user, for_write=True
    )
    result = await dialog.ask_topic(
        session, attempt, ts, version, scenario, body.topic, action_id=body.action_id
    )
    return await _respond(session, attempt, ts, scenario, result)


@router.post("/attempts/{attempt_id}/utterance", response_model=TurnResponse)
async def utterance(
    attempt_id: uuid.UUID,
    user: ActiveUser,
    session: DbSession,
    file: Annotated[UploadFile, File(description="Речь оператора: WAV, WebM/Opus или OGG")],
    action_id: Annotated[str | None, Form(max_length=64)] = None,
) -> TurnResponse:
    """A spoken phrase: recognized by the ``stt`` service with the scenario's streets as
    hints, then handled like ``say``. Without the service the answer is 503 and the operator
    types instead."""
    attempt, ts, version, scenario = await dialog.dialog_attempt(
        session, attempt_id, user, for_write=True
    )
    retry = dialog._find_retry(attempt, action_id)
    if retry is not None:
        return await _respond(session, attempt, ts, scenario, retry)
    audio = await file.read()
    if not audio:
        raise ApiError(422, "empty_audio", "Пустая запись: скажите фразу ещё раз.")
    if len(audio) > MAX_UTTERANCE_BYTES:
        raise ApiError(413, "audio_too_large", "Запись больше 10 МБ.")
    address = scenario.reference_card.address
    hints = [h for h in (address.street, address.city, address.district) if h]
    transcript = await get_stt_provider().transcribe(audio, file.filename or "audio.wav", hints)
    if not transcript.available:
        raise ApiError(
            503,
            "stt_unavailable",
            "Распознавание речи недоступно: сервис stt не запущен. Введите фразу текстом.",
        )
    if not transcript.text:
        raise ApiError(422, "nothing_recognized", "Речь не распознана: повторите громче.")
    result = await dialog.say(
        session, attempt, ts, version, scenario, transcript.text, action_id=action_id, heard=True
    )
    return await _respond(session, attempt, ts, scenario, result, heard_text=transcript.text)


@router.get("/media/{path:path}", include_in_schema=False)
async def media(path: str, user: ActiveUser) -> FileResponse:
    """Voiced replies (``attempts.dialog[].audio``) for signed-in users."""
    return FileResponse(dialog.media_file(path))
