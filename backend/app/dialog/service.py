"""Caller dialog of a call-intake attempt: the operator says something (typed or recognized),
the caller answers through the ``DialogProvider`` of the session's dialog mode, both turns are
appended to ``attempts.dialog`` and announced with a ``dialog.turn`` event (PRD 9.3, 12).

Rows of ``attempts.dialog``::

    {role, text, topics, at, action_id, reply_id, method, audio, generated, heard}

``role``, ``text``, ``topics`` and ``at`` are what the evaluation engine reads
(``dialog_input``); the rest is bookkeeping. Voiced replies live under ``STORAGE_DIR/tts`` and
are synthesized once per approved reply (wave 8 will do it at approval time into the same path).

Every function works inside the caller's transaction and returns the events it appended.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.dialog import call
from app.domain.evaluation.schemas import CallIntakeScenario, DialogTurn
from app.domain.evaluation.text import detect_topics, normalize_text
from app.domain.reference_data import CALLER_TOPICS
from app.errors import ApiError
from app.events import append_event
from app.logging import get_logger
from app.models import (
    ATTEMPT_IN_PROGRESS,
    ATTEMPT_ISSUED,
    ATTEMPT_RECEIVED,
    CALL_ANSWERED,
    CALL_ENDED,
    CALL_IDLE,
    CALL_RINGING,
    MODE_CALL_INTAKE,
    Attempt,
    Role,
    ScenarioVersion,
    SessionEvent,
    TrainingSession,
    User,
)
from app.providers.dialog import (
    FALLBACK_METHODS,
    SERVICE_TOPICS,
    TOPIC_CODES,
    CallerReply,
    DialogContext,
    DialogProvider,
    get_dialog_provider,
)
from app.providers.stt import get_stt_provider
from app.providers.tts import AudioClip, get_tts_provider, save_clip
from app.training import service as training

log = get_logger(__name__)

EVENT_DIALOG_TURN = "dialog.turn"
TTS_SUBDIR = "tts"
# The last operator turns whose action_id is compared on a retry.
_RETRY_WINDOW = 5
_SAFE_NAME = re.compile(r"[^a-z0-9_.-]")


@dataclass
class TurnResult:
    operator: dict
    caller: dict
    applied: bool
    pending_reply: bool
    latency_ms: int
    events: list[SessionEvent] = field(default_factory=list)
    call_ended: bool = False  # the caller hung up after this reply (scenario «бросил трубку»)


# ---------------------------------------------------------------- access


async def dialog_attempt(
    session: AsyncSession, attempt_id: uuid.UUID, user: User, *, for_write: bool
) -> tuple[Attempt, TrainingSession, ScenarioVersion, CallIntakeScenario]:
    """The call-intake attempt with its scenario; writing is for the owning trainee only."""
    attempt = await training.get_attempt_for(session, attempt_id, user)
    if attempt.mode != MODE_CALL_INTAKE:
        raise ApiError(409, "not_call_intake", "Разговор с заявителем есть только в приёме вызова.")
    if for_write:
        if user.role != Role.student or attempt.student_id != user.id:
            raise ApiError(403, "forbidden", "Говорить с заявителем может только обучающийся.")
        if attempt.state in training.CLOSED_STATES:
            raise ApiError(409, "attempt_closed", "Вызов уже завершён, разговор закрыт.")
        if attempt.call_state == CALL_ENDED:
            raise ApiError(409, "call_ended", "Звонок завершён: заявителю больше не сказать.")
    ts = await session.get(TrainingSession, attempt.session_id)
    card = await training.load_scenario_card(session, attempt.scenario_id, attempt.scenario_version)
    scenario = CallIntakeScenario.model_validate(card.body)
    return attempt, ts, card.version, scenario


# ---------------------------------------------------------------- turns


def _turn(role: str, text: str, topics: list[str], at: datetime, **extra: object) -> dict:
    row = {"role": role, "text": text, "topics": topics, "at": at.astimezone(UTC).isoformat()}
    row.update({k: v for k, v in extra.items() if v is not None and v is not False})
    return row


def dialog_input(attempt: Attempt) -> list[dict]:
    """``attempts.dialog`` as the evaluation engine expects it (PRD 9.3)."""
    return [
        {"role": t["role"], "text": t["text"], "topics": t.get("topics", []), "at": t.get("at")}
        for t in attempt.dialog
    ]


def _context(attempt: Attempt, scenario: CallIntakeScenario) -> DialogContext:
    history = [DialogTurn.model_validate(t) for t in dialog_input(attempt)]
    used = {t["reply_id"] for t in attempt.dialog if t.get("reply_id")}
    return DialogContext(
        scenario=scenario,
        history=history,
        conversation_id=str(attempt.id),
        used_reply_ids=used,
    )


def _find_retry(attempt: Attempt, action_id: str | None) -> TurnResult | None:
    if not action_id:
        return None
    turns = attempt.dialog
    for i in range(len(turns) - 1, max(len(turns) - 2 * _RETRY_WINDOW, -1), -1):
        if turns[i].get("role") == "operator" and turns[i].get("action_id") == action_id:
            caller = turns[i + 1] if i + 1 < len(turns) else turns[i]
            return TurnResult(turns[i], caller, applied=False, pending_reply=False, latency_ms=0)
    return None


def _ensure_opening(attempt: Attempt, scenario: CallIntakeScenario, now: datetime) -> None:
    """The caller speaks first: the scenario's opening becomes turn 0 when the call is answered."""
    if attempt.dialog:
        return
    attempt.dialog = [_turn("caller", scenario.caller.opening, [], now, method="opening")]
    if attempt.answered_at is None:
        attempt.answered_at = now
    if attempt.call_state in (CALL_IDLE, CALL_RINGING):
        attempt.call_state = CALL_ANSWERED
    if attempt.state in (ATTEMPT_ISSUED, ATTEMPT_RECEIVED):
        attempt.state = ATTEMPT_IN_PROGRESS
        if attempt.received_at is None:
            attempt.received_at = now


async def ensure_opening(
    attempt: Attempt, version: ScenarioVersion, scenario: CallIntakeScenario, now: datetime
) -> dict:
    """Turn 0 with its voice file: what the call panel plays when the operator answers."""
    _ensure_opening(attempt, scenario, now)
    opening = attempt.dialog[0]
    if opening.get("method") == "opening" and not opening.get("audio"):
        audio = await opening_audio(version, scenario)
        if audio:
            attempt.dialog = [{**opening, "audio": audio}, *attempt.dialog[1:]]
            flag_modified(attempt, "dialog")
    return attempt.dialog[0]


async def opening_audio(version: ScenarioVersion, scenario: CallIntakeScenario) -> str | None:
    """Voice file of the scenario's opening, cached as ``opening`` in the version folder."""
    reply = CallerReply(
        text=scenario.caller.opening, topics=[], operator_topics=[], method="opening"
    )
    return await reply_audio(None, version, scenario, reply, stem="opening")


def provider_for(ts: TrainingSession) -> DialogProvider:
    return get_dialog_provider(ts.dialog_mode or None)


def cloud_lesson(ts: TrainingSession) -> bool:
    """The lesson's caller is played by Vapi (plan/track-c-vapi.md)."""
    return ts.dialog_mode == EXTERNAL_METHOD and get_settings().cloud_voice_enabled


async def _store_turns(
    session: AsyncSession,
    attempt: Attempt,
    ts: TrainingSession,
    version: ScenarioVersion,
    scenario: CallIntakeScenario,
    operator: dict,
    reply: CallerReply,
    now: datetime,
) -> TurnResult:
    audio = await reply_audio(attempt, version, scenario, reply)
    caller = _turn(
        "caller",
        reply.text,
        reply.topics,
        now,
        reply_id=reply.reply_id,
        method=reply.method,
        audio=audio,
        generated=reply.generated,
    )
    attempt.dialog = [*attempt.dialog, operator, caller]
    flag_modified(attempt, "dialog")
    pending = False
    if reply.generated:
        pending = _add_pending_reply(version, attempt, reply)
    await session.flush()
    event = await append_event(
        session,
        session_id=ts.id,
        type_=EVENT_DIALOG_TURN,
        student_id=attempt.student_id,
        payload={
            "attempt_id": attempt.id,
            "turns": len(attempt.dialog),
            "operator": operator,
            "caller": caller,
            "pending_reply": pending,
        },
    )
    drop_events = await call.caller_drops_after_turn(session, attempt, scenario)
    return TurnResult(
        operator,
        caller,
        applied=True,
        pending_reply=pending,
        latency_ms=reply.latency_ms,
        events=[event, *drop_events],
        call_ended=bool(drop_events),
    )


async def say(
    session: AsyncSession,
    attempt: Attempt,
    ts: TrainingSession,
    version: ScenarioVersion,
    scenario: CallIntakeScenario,
    text: str,
    *,
    action_id: str | None = None,
    heard: bool = False,
) -> TurnResult:
    """The operator's phrase and the caller's answer."""
    retry = _find_retry(attempt, action_id)
    if retry is not None:
        return retry
    now = training.utcnow()
    _ensure_opening(attempt, scenario, now)
    reply = await provider_for(ts).reply(_context(attempt, scenario), text)
    operator = _turn("operator", text, reply.operator_topics, now, action_id=action_id, heard=heard)
    return await _store_turns(session, attempt, ts, version, scenario, operator, reply, now)


async def ask_topic(
    session: AsyncSession,
    attempt: Attempt,
    ts: TrainingSession,
    version: ScenarioVersion,
    scenario: CallIntakeScenario,
    topic: str,
    *,
    action_id: str | None = None,
) -> TurnResult:
    """A topic button instead of a phrase: the operator's turn is the topic's question."""
    if topic not in TOPIC_CODES:
        raise ApiError(422, "unknown_topic", f"Неизвестная тема «{topic}».")
    retry = _find_retry(attempt, action_id)
    if retry is not None:
        return retry
    now = training.utcnow()
    _ensure_opening(attempt, scenario, now)
    reply = await provider_for(ts).reply_to_topic(_context(attempt, scenario), topic)
    operator = _turn("operator", topic_question(topic), [topic], now, action_id=action_id)
    return await _store_turns(session, attempt, ts, version, scenario, operator, reply, now)


_TOPIC_TITLES = {t["code"]: t["title"] for t in CALLER_TOPICS}


def topic_question(topic: str) -> str:
    """Text stored for a topic button press, so the transcript reads as a conversation."""
    return f"[{_TOPIC_TITLES.get(topic, topic)}]"


# ---------------------------------------------------------------- cloud voice (track C)

EXTERNAL_METHOD = "cloud"


def _is_opening(attempt: Attempt, role: str, text: str) -> bool:
    """The cloud provider reports the opening it spoke first; turn 0 already holds it."""
    if role != "caller" or len(attempt.dialog) != 1:
        return False
    return normalize_text(text) == normalize_text(attempt.dialog[0].get("text", ""))


async def external_turn(
    session: AsyncSession,
    attempt: Attempt,
    ts: TrainingSession,
    scenario: CallIntakeScenario,
    role: str,
    text: str,
    *,
    now: datetime | None = None,
    latency_ms: int | None = None,
) -> tuple[dict | None, list[SessionEvent]]:
    """One turn heard from the cloud voice provider (plan/track-c-vapi.md): the operator's
    phrase or the caller's reply as the provider transcribed it. Topics come from keywords;
    the evaluation engine reads them like any other turn. ``latency_ms`` of a caller's turn:
    from the end of the operator's phrase to the first sound of the reply."""
    if role not in ("operator", "caller"):
        raise ValueError(f"unknown dialog role: {role!r}")
    text = text.strip()
    if not text:
        return None, []
    now = now or training.utcnow()
    _ensure_opening(attempt, scenario, now)
    if _is_opening(attempt, role, text):
        return attempt.dialog[0], []
    topics = [t for t in detect_topics(text) if t not in SERVICE_TOPICS]
    turn = _turn(
        role,
        text,
        topics,
        now,
        method=EXTERNAL_METHOD,
        heard=role == "operator",
        latency_ms=latency_ms if role == "caller" else None,
    )
    attempt.dialog = [*attempt.dialog, turn]
    flag_modified(attempt, "dialog")
    await session.flush()
    event = await append_event(
        session,
        session_id=ts.id,
        type_=EVENT_DIALOG_TURN,
        student_id=attempt.student_id,
        payload={
            "attempt_id": attempt.id,
            "turns": len(attempt.dialog),
            "operator": turn if role == "operator" else None,
            "caller": turn if role == "caller" else None,
            "pending_reply": False,
        },
    )
    return turn, [event]


def external_turns(attempt: Attempt) -> list[dict]:
    return [t for t in attempt.dialog if t.get("method") == EXTERNAL_METHOD]


def covered_topics(attempt: Attempt) -> set[str]:
    return {topic for turn in attempt.dialog for topic in turn.get("topics", [])}


def _add_pending_reply(version: ScenarioVersion, attempt: Attempt, reply: CallerReply) -> bool:
    """hybrid / generate: a new reply goes into the scenario version «на утверждение»
    (``approved: false``); the teacher approves or deletes it in wave 8."""
    replies = list(version.body.get("replies") or [])
    next_id = max((int(r.get("id", 0)) for r in replies), default=0) + 1
    replies.append(
        {
            "id": next_id,
            "topic": reply.topics[0] if reply.topics else "unknown",
            "text": reply.text,
            "audio": None,
            "approved": False,
            "source": "generated",
            "attempt_id": str(attempt.id),
        }
    )
    version.body = {**version.body, "replies": replies}
    flag_modified(version, "body")
    return True


# ---------------------------------------------------------------- audio


def storage_root() -> Path:
    return Path(get_settings().storage_dir)


def _safe(name: str) -> str:
    return _SAFE_NAME.sub("", name.lower())


async def reply_audio(
    attempt: Attempt | None,
    version: ScenarioVersion,
    scenario: CallIntakeScenario,
    reply: CallerReply,
    stem: str | None = None,
) -> str | None:
    """Path (relative to STORAGE_DIR) of the reply's audio: the stored recording of an
    approved reply, or a Piper synthesis cached per scenario version and reply; ``None``
    without a voice."""
    if reply.audio:
        return reply.audio
    tts = get_tts_provider()
    if tts.method == "text" or not reply.text:
        return None
    folder = Path(TTS_SUBDIR) / _safe(str(version.scenario_id)) / f"v{version.version}"
    if stem is None:
        if reply.reply_id:
            stem = f"r{reply.reply_id}"
        elif attempt is not None:
            stem = f"a{_safe(str(attempt.id))}-{len(attempt.dialog)}"
        else:
            return None
    existing = _existing_audio(folder / stem)
    if existing:
        return existing
    clip = await tts.synthesize(
        reply.text, scenario.caller.voice, scenario.caller.noise, scenario.difficulty
    )
    if clip is None:
        return None
    return _save_audio(clip, folder / stem)


def _existing_audio(stem: Path) -> str | None:
    root = storage_root()
    for suffix in (".mp3", ".wav"):
        if (root / stem).with_suffix(suffix).exists():
            return stem.with_suffix(suffix).as_posix()
    return None


def _save_audio(clip: AudioClip, stem: Path) -> str:
    paths = save_clip(clip, storage_root() / stem)
    chosen = paths.get("mp3") or paths["wav"]
    return stem.with_suffix(chosen.suffix).as_posix()


def media_file(relative: str) -> Path:
    """Resolves a media path from ``attempts.dialog[].audio`` inside STORAGE_DIR/tts only."""
    root = (storage_root() / TTS_SUBDIR).resolve()
    candidate = (storage_root() / relative).resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise ApiError(404, "media_not_found", "Аудиофайл не найден.")
    return candidate


def audio_source_file(relative: str) -> Path | None:
    """The WAV of a voiced reply if it exists next to the MP3 (better for telephony), else
    the file itself; ``None`` when nothing is on disk."""
    path = storage_root() / relative
    wav = path.with_suffix(".wav")
    if wav.is_file():
        return wav
    return path if path.is_file() else None


def fallback_replies(attempt: Attempt, mode: str) -> int:
    """Caller's turns answered without the model in a model mode (docs/BUGS.md, 10). In the
    cloud mode: replies the local stand-by gave while Vapi was unreachable."""
    if mode == "buttons":
        return 0
    if mode == EXTERNAL_METHOD:
        return sum(
            1
            for turn in attempt.dialog[1:]
            if turn.get("role") == "caller" and turn.get("method") != EXTERNAL_METHOD
        )
    return sum(
        1
        for turn in attempt.dialog
        if turn.get("role") == "caller" and turn.get("method") in FALLBACK_METHODS
    )


def stt_available() -> bool:
    return get_stt_provider().method != "unavailable"


def tts_available() -> bool:
    return get_tts_provider().method != "text"
