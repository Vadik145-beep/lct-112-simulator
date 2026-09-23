"""Caller dialog API of the call-intake mode: typed phrases, topic buttons, speech, retries,
access, pending replies of the hybrid mode, voiced replies."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import ClassVar

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.dialog import service as dialog_service
from app.models import (
    ATTEMPT_ISSUED,
    MODE_CALL_INTAKE,
    MODE_CARD_RESPONSE,
    SESSION_RUNNING,
    Attempt,
    Group,
    Scenario,
    ScenarioVersion,
    SessionEvent,
    TrainingSession,
    User,
)
from app.providers.dialog import ButtonsDialog, CallerReply, DialogContext, SelectDialog
from app.providers.llm import LlamaCppChat
from app.providers.stt import Transcript
from app.training.service import utcnow
from tests.api.conftest import DATA_DIR
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)
MODELS_DIR = Path(os.environ["MODELS_DIR"])
GAS_PIPE = "call_31-3_svist_gazovoy_truby"


async def make_attempt(
    scenario_key: str = GAS_PIPE,
    *,
    mode: str = MODE_CALL_INTAKE,
    dialog_mode: str = "select",
    voice_enabled: bool = True,
) -> uuid.UUID:
    """A running session of «Учебная-1» with one attempt for student1."""
    async with SessionLocal() as session:
        teacher = await session.scalar(select(User).where(User.login == "teacher1"))
        student = await session.scalar(select(User).where(User.login == "student1"))
        group = await session.scalar(select(Group).where(Group.title == "Учебная-1"))
        scenario = await session.scalar(select(Scenario).where(Scenario.seed_key == scenario_key))
        assert scenario is not None, scenario_key
        ts = TrainingSession(
            title="Приём вызова",
            teacher_id=teacher.id,
            group_id=group.id,
            mode=mode,
            scenario_ids=[scenario.id],
            norm_seconds=90,
            dialog_mode=dialog_mode,
            voice_enabled=voice_enabled,
            status=SESSION_RUNNING,
            started_at=utcnow(),
        )
        session.add(ts)
        await session.flush()
        attempt = Attempt(
            session_id=ts.id,
            student_id=student.id,
            scenario_id=scenario.id,
            scenario_version=scenario.current_version,
            mode=mode,
            card_number="1",
            issued_at=utcnow(),
            state=ATTEMPT_ISSUED,
        )
        session.add(attempt)
        await session.commit()
        return attempt.id


async def say(client: AsyncClient, token: dict, attempt_id: uuid.UUID, text: str, **extra):
    return await client.post(
        f"/api/attempts/{attempt_id}/say", headers=bearer(token), json={"text": text, **extra}
    )


async def test_say_opens_the_call_and_answers(client: AsyncClient) -> None:
    attempt_id = await make_attempt()
    token = await login(client, "student1")
    r = await say(client, token, attempt_id, "Назовите адрес, пожалуйста")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True
    assert body["operator"]["text"] == "Назовите адрес, пожалуйста"
    assert "address" in body["operator"]["topics"]
    assert body["caller"]["topics"] == ["address"]
    assert body["caller"]["reply_id"] == 2
    # The reply has other wordings and one of them is played at random (pick_variant).
    assert "Вавилова" in body["caller"]["text"]
    # Without a model server the session's «select» degrades to buttons.
    assert body["caller"]["method"] == "buttons"
    assert body["dialog"]["mode"] == "buttons"
    turns = body["dialog"]["turns"]
    assert [t["role"] for t in turns] == ["caller", "operator", "caller"]
    assert turns[0]["text"].startswith("Здравствуйте, у меня на кухне")
    assert body["dialog"]["answered_at"] is not None
    covered = {t["code"] for t in body["dialog"]["topics"] if t["covered"]}
    assert covered == {"address"}
    required = {t["code"] for t in body["dialog"]["topics"] if t["required"]}
    assert required == set(body["dialog"]["required_topics"])

    async with SessionLocal() as session:
        attempt = await session.get(Attempt, attempt_id)
        assert len(attempt.dialog) == 3
        assert attempt.answered_at is not None
        events = list(
            await session.scalars(
                select(SessionEvent).where(SessionEvent.session_id == attempt.session_id)
            )
        )
        assert [e.type for e in events] == ["dialog.turn"]
        assert events[0].payload["caller"]["reply_id"] == 2
        assert events[0].student_id == attempt.student_id


async def test_topic_button_and_unknown_topic(client: AsyncClient) -> None:
    attempt_id = await make_attempt()
    token = await login(client, "student1")
    r = await client.post(
        f"/api/attempts/{attempt_id}/ask-topic", headers=bearer(token), json={"topic": "injured"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["caller"]["reply_id"] == 4
    assert r.json()["operator"]["text"] == "[Пострадавшие]"
    r = await client.post(
        f"/api/attempts/{attempt_id}/ask-topic", headers=bearer(token), json={"topic": "weather"}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unknown_topic"


async def test_retry_with_same_action_id_does_not_ask_twice(client: AsyncClient) -> None:
    attempt_id = await make_attempt()
    token = await login(client, "student1")
    first = await say(client, token, attempt_id, "Как вас зовут?", action_id="a-1")
    second = await say(client, token, attempt_id, "Как вас зовут?", action_id="a-1")
    assert first.json()["applied"] is True
    assert second.json()["applied"] is False
    assert second.json()["caller"] == first.json()["caller"]
    assert len(second.json()["dialog"]["turns"]) == 3


async def test_access_rules(client: AsyncClient) -> None:
    attempt_id = await make_attempt()
    teacher = await login(client, "teacher1")
    r = await client.get(f"/api/attempts/{attempt_id}/dialog", headers=bearer(teacher))
    assert r.status_code == 200
    assert r.json()["turns"] == []
    r = await say(client, teacher, attempt_id, "Адрес?")
    assert r.status_code == 403
    other = await login(client, "student2")
    r = await say(client, other, attempt_id, "Адрес?")
    assert r.status_code == 404
    card_attempt = await make_attempt("card_2-1_zadymlenie_musoroprovoda", mode=MODE_CARD_RESPONSE)
    student = await login(client, "student1")
    r = await say(client, student, card_attempt, "Адрес?")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "not_call_intake"


async def test_utterance_without_stt_says_to_type(client: AsyncClient) -> None:
    attempt_id = await make_attempt()
    token = await login(client, "student1")
    r = await client.post(
        f"/api/attempts/{attempt_id}/utterance",
        headers=bearer(token),
        files={"file": ("q.wav", b"RIFF....WAVE", "audio/wav")},
    )
    assert r.status_code == 503
    assert "текстом" in r.json()["error"]["message"]


async def test_utterance_with_recognized_speech(client: AsyncClient, monkeypatch) -> None:
    class FakeSTT:
        method = "whisper"
        hints: ClassVar[list[str]] = []

        async def transcribe(self, audio, filename="audio.wav", hints=()):
            FakeSTT.hints = list(hints)
            return Transcript("Кто нибудь пострадал?", "whisper", 1.5, 300)

    monkeypatch.setattr("app.dialog.router.get_stt_provider", lambda: FakeSTT())
    attempt_id = await make_attempt()
    token = await login(client, "student1")
    r = await client.post(
        f"/api/attempts/{attempt_id}/utterance",
        headers=bearer(token),
        files={"file": ("q.wav", b"RIFF....WAVE", "audio/wav")},
        data={"action_id": "u-1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["heard_text"] == "Кто нибудь пострадал?"
    assert body["operator"]["heard"] is True
    assert body["caller"]["topics"] == ["injured"]
    assert "улица Вавилова" in FakeSTT.hints
    r = await client.post(
        f"/api/attempts/{attempt_id}/utterance",
        headers=bearer(token),
        files={"file": ("q.wav", b"", "audio/wav")},
    )
    assert r.status_code == 422


async def test_unreachable_model_is_reported_not_hidden(client: AsyncClient, monkeypatch) -> None:
    """docs/BUGS.md 10: a «select» lesson whose model server is down still answers (by
    keywords), but the dialog says so: ``requested_mode`` stays «select» and
    ``fallback_replies`` counts the replies made without the model."""
    dead = LlamaCppChat("http://127.0.0.1:9", name="llm-dialog", timeout=0.5)
    provider = SelectDialog(dead, ButtonsDialog())
    monkeypatch.setattr(dialog_service, "provider_for", lambda ts: provider)
    attempt_id = await make_attempt(dialog_mode="select")
    token = await login(client, "student1")
    r = await say(client, token, attempt_id, "Назовите адрес")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["caller"]["method"] == "buttons"
    assert body["dialog"]["mode"] == "select"
    assert body["dialog"]["requested_mode"] == "select"
    assert body["dialog"]["fallback_replies"] == 1
    r = await say(client, token, attempt_id, "Кто пострадал?")
    assert r.json()["dialog"]["fallback_replies"] == 2
    # A lesson in «buttons» is not degraded: nothing to warn about.
    monkeypatch.undo()
    r = await client.get(f"/api/attempts/{attempt_id}/dialog", headers=bearer(token))
    assert r.json()["mode"] == "buttons"
    assert r.json()["fallback_replies"] == 0


async def test_models_availability_for_the_teacher(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    r = await client.get("/api/models", headers=bearer(teacher))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"dialog", "generation", "stt", "tts", "cloud"}
    assert all(isinstance(v, bool) for v in body.values())
    assert body["dialog"] is False  # no model server in tests
    student = await login(client, "student1")
    r = await client.get("/api/models", headers=bearer(student))
    assert r.status_code == 403


async def test_hybrid_generated_reply_waits_for_approval(client: AsyncClient, monkeypatch) -> None:
    class Generating(ButtonsDialog):
        mode = "hybrid"

        async def reply(self, ctx: DialogContext, operator_text: str) -> CallerReply:
            return CallerReply(
                text="Соседи? Не знаю, я один дома.",
                topics=["unknown"],
                operator_topics=[],
                generated=True,
                method="hybrid/generate",
            )

    monkeypatch.setattr(dialog_service, "provider_for", lambda ts: Generating())
    attempt_id = await make_attempt(dialog_mode="hybrid")
    token = await login(client, "student1")
    r = await say(client, token, attempt_id, "Соседи дома?")
    assert r.status_code == 200, r.text
    assert r.json()["pending_reply"] is True
    assert r.json()["caller"]["generated"] is True
    async with SessionLocal() as session:
        attempt = await session.get(Attempt, attempt_id)
        version = await session.scalar(
            select(ScenarioVersion).where(
                ScenarioVersion.scenario_id == attempt.scenario_id,
                ScenarioVersion.version == attempt.scenario_version,
            )
        )
        new = [r for r in version.body["replies"] if not r["approved"]]
        assert len(new) == 1
        assert new[0]["text"] == "Соседи? Не знаю, я один дома."
        assert new[0]["source"] == "generated"
        assert new[0]["id"] == max(r["id"] for r in version.body["replies"])
    # The unapproved reply is not offered to the caller afterwards.
    monkeypatch.undo()
    r = await say(client, token, attempt_id, "Какой адрес?")
    assert r.json()["caller"]["reply_id"] == 2


@pytest.mark.skipif(
    not (MODELS_DIR / "tts" / "ru_RU-denis-medium.onnx").exists(),
    reason="голоса Piper не скачаны (scripts/fetch_models.sh --only tts)",
)
async def test_reply_is_voiced_and_served(client: AsyncClient) -> None:
    attempt_id = await make_attempt()
    token = await login(client, "student1")
    r = await say(client, token, attempt_id, "Назовите адрес")
    url = r.json()["caller"]["audio_url"]
    assert url and url.startswith("/api/media/tts/")
    assert r.json()["dialog"]["tts_available"] is True
    media = await client.get(url, headers=bearer(token))
    assert media.status_code == 200
    assert media.headers["content-type"].startswith("audio/")
    assert len(media.content) > 10_000
    # The same reply is not synthesized again: the second attempt reuses the file.
    again = await say(client, token, await make_attempt(), "Назовите адрес")
    assert again.json()["caller"]["audio_url"] == url
    assert (await client.get("/api/media/tts/../../.env", headers=bearer(token))).status_code == 404
    assert (await client.get(url)).status_code == 401


async def test_teacher_sets_dialog_mode_and_voice(client: AsyncClient) -> None:
    from tests.api.test_sessions import create_session

    teacher = await login(client, "teacher1")
    created = await create_session(client, teacher, dialog_mode="hybrid", voice_enabled=True)
    assert created["dialog_mode"] == "hybrid"
    assert created["voice_enabled"] is True
    r = await client.patch(
        f"/api/sessions/{created['id']}", headers=bearer(teacher), json={"dialog_mode": "magic"}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bad_dialog_mode"
    r = await client.patch(
        f"/api/sessions/{created['id']}", headers=bearer(teacher), json={"dialog_mode": "buttons"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["dialog_mode"] == "buttons"
