"""Calls of the dispatcher to service officers without telephony (issue #36): start, the
officer's greeting, phrases and facts, hang-up, the card closing an open call, the evaluation
and the review data; access rules."""

from __future__ import annotations

import uuid
from typing import ClassVar

import pytest
from httpx import AsyncClient

from app.config import get_settings
from app.db import SessionLocal
from app.models import MODE_CARD_RESPONSE, Attempt
from app.providers.stt import Transcript
from tests.api.conftest import DATA_DIR
from tests.api.test_dialog import make_attempt
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)

CARD = "card_moek_1_net_otopleniya"


async def start(client: AsyncClient, token: dict, attempt_id: uuid.UUID, service: str = "moek"):
    return await client.post(
        f"/api/attempts/{attempt_id}/service-call", headers=bearer(token), json={"service": service}
    )


async def say(
    client: AsyncClient, token: dict, attempt_id: uuid.UUID, call_id: str, text: str, **extra
):
    return await client.post(
        f"/api/attempts/{attempt_id}/service-call/{call_id}/say",
        headers=bearer(token),
        json={"text": text, **extra},
    )


async def answer(client: AsyncClient, token: dict, attempt_id: uuid.UUID, call_id: str):
    return await client.post(
        f"/api/attempts/{attempt_id}/service-call/{call_id}/answer", headers=bearer(token)
    )


async def end(client: AsyncClient, token: dict, attempt_id: uuid.UUID, call_id: str):
    return await client.post(
        f"/api/attempts/{attempt_id}/service-call/{call_id}/end", headers=bearer(token)
    )


async def status(client: AsyncClient, token: dict, attempt_id: uuid.UUID, **body):
    return await client.post(f"/api/attempts/{attempt_id}/status", headers=bearer(token), json=body)


async def test_dds_lesson_carries_its_dialog_mode(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Режим диалога — настройка занятия ДДС: им отвечают дежурный службы и старший группы.
    Облачный голос доступен и здесь, но только там, где установка его включила."""
    token = await login(client, "teacher1")
    groups = await client.get("/api/groups", headers=bearer(token))
    assert groups.status_code == 200, groups.text
    rows = groups.json()
    group_id = (rows["items"] if isinstance(rows, dict) else rows)[0]["id"]
    body = {
        "title": "Режим в ДДС",
        "mode": MODE_CARD_RESPONSE,
        "group_id": group_id,
        "difficulty": 1,
        "norm_seconds": 30,
        "pass_threshold": 70,
        "dialog_mode": "buttons",
        "voice_enabled": False,
    }
    created = await client.post("/api/sessions", headers=bearer(token), json=body)
    assert created.status_code == 201, created.text
    assert created.json()["dialog_mode"] == "buttons"
    assert created.json()["voice_enabled"] is False

    settings = get_settings()
    monkeypatch.setattr(settings, "cloud_voice_enabled", True)
    cloud = await client.post(
        "/api/sessions",
        headers=bearer(token),
        json={**body, "title": "Облачный ДДС", "dialog_mode": "cloud"},
    )
    assert cloud.status_code == 201, cloud.text
    assert cloud.json()["dialog_mode"] == "cloud"

    monkeypatch.setattr(settings, "cloud_voice_enabled", False)
    refused = await client.post(
        "/api/sessions",
        headers=bearer(token),
        json={**body, "title": "Облако выключено", "dialog_mode": "cloud"},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "cloud_voice_disabled"


async def test_cloud_lesson_does_not_greet_twice(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """В облачном занятии «Позвонить» не пишет своё приветствие: первую фразу говорит
    облако (иначе в стенограмме два приветствия, как было у заявителя до #104)."""
    monkeypatch.setattr(get_settings(), "cloud_voice_enabled", True)
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="cloud")
    token = await login(client, "student1")
    r = await start(client, token, attempt_id)
    assert r.status_code == 200, r.text
    assert r.json()["call"]["turns"] == []
    assert r.json()["call"]["answered"] is False


async def test_call_is_voiced_even_when_the_old_flag_is_off(client: AsyncClient) -> None:
    """Звонок звучит всегда, когда есть чем озвучить: поле voice_enabled осталось от убранной
    галочки и ни на что не влияет (docs/DECISIONS.md, 23.09.2026)."""
    attempt_id = await make_attempt(
        CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons", voice_enabled=False
    )
    token = await login(client, "student1")
    r = await start(client, token, attempt_id)
    assert r.status_code == 200, r.text
    greeting = r.json()["call"]["turns"][0]
    assert "слушаю" in greeting["text"]
    assert greeting["audio_url"] is not None


async def test_text_call_passes_facts_and_lands_in_the_evaluation(client: AsyncClient) -> None:
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    token = await login(client, "student1")

    r = await start(client, token, attempt_id)
    assert r.status_code == 200, r.text
    data = r.json()
    call = data["call"]
    assert call["service"] == "moek" and call["service_title"]
    assert call["answered"] is True and call["telephony"] is False
    assert call["turns"][0]["role"] == "caller" and "слушаю" in call["turns"][0]["text"]
    # Наряд диспетчер не передаёт: его называет дежурный (решение 23.09.2026).
    assert call["facts_required"] == ["address", "incident_type", "injured"]
    assert data["attempt"]["received_at"] is not None  # the call opened the card
    assert data["attempt"]["service_calls_required"] == ["moek"]
    call_id = call["id"]

    # A second call while this one is open is refused.
    r = await start(client, token, attempt_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "call_in_progress"

    r = await say(
        client, token, attempt_id, call_id, "Улица Молостовых, дом 10, корпус 1", action_id="s-1"
    )
    assert r.status_code == 200, r.text
    assert r.json()["call"]["facts_passed"] == ["address"]
    assert r.json()["call"]["turns"][-1]["role"] == "caller"
    # The same action id is answered once.
    r = await say(
        client, token, attempt_id, call_id, "Улица Молостовых, дом 10, корпус 1", action_id="s-1"
    )
    assert r.json()["applied"] is False
    assert len(r.json()["call"]["turns"]) == 3
    r = await say(
        client, token, attempt_id, call_id, "Нет отопления в трёх домах, пострадавших нет"
    )
    assert set(r.json()["call"]["facts_passed"]) == {"address", "incident_type", "injured"}
    r = await say(client, token, attempt_id, call_id, "Наряд МОЭК-4127")
    assert r.json()["call"]["facts_passed"] == [
        "address",
        "incident_type",
        "injured",
        "order_number",
    ]

    r = await end(client, token, attempt_id, call_id)
    assert r.status_code == 200, r.text
    assert r.json()["call"]["ended_at"] is not None
    assert r.json()["call"]["end_reason"] == "hangup"
    assert r.json()["call"]["seconds"] is not None
    r = await say(client, token, attempt_id, call_id, "Ещё кое-что")
    assert r.status_code == 409 and r.json()["error"]["code"] == "call_ended"

    # The chain closes the card; the evaluation counts the call.
    for body in (
        {"status": "accepted"},
        {
            "status": "response_started",
            "order_number": "МОЭК-4127",
            "comment": "Бригада направлена",
        },
        {"status": "arrived"},
        {"status": "works_started", "comment": "Проверка ИТП"},
        {"status": "works_done", "comment": "Отопление восстановлено"},
    ):
        r = await status(client, token, attempt_id, **body)
        assert r.status_code == 200, r.text
    evaluation = r.json()["attempt"]["evaluation"]
    component = evaluation["components"]["service_call"]
    assert component["score"] == component["max"] > 0
    assert component["items"][0]["facts_missing"] == []
    assert "service_not_informed" not in {e["code"] for e in evaluation["errors"]}
    calls = r.json()["attempt"]["service_calls"]
    assert len(calls) == 1 and calls[0]["facts_passed"] == [
        "address",
        "incident_type",
        "injured",
        "order_number",
    ]

    # Closed card: no more calls.
    r = await start(client, token, attempt_id)
    assert r.status_code == 409


async def test_closing_the_card_ends_an_open_call_and_a_silent_call_scores_nothing(
    client: AsyncClient,
) -> None:
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    token = await login(client, "student1")
    call_id = (await start(client, token, attempt_id)).json()["call"]["id"]
    await say(client, token, attempt_id, call_id, "Алло, дежурный?")
    r = await status(client, token, attempt_id, status="accepted")
    assert r.status_code == 200
    r = await client.post(f"/api/attempts/{attempt_id}/finish", headers=bearer(token))
    assert r.status_code == 200, r.text
    attempt = r.json()["attempt"]
    assert attempt["service_calls"][0]["ended_at"] is not None
    assert attempt["service_calls"][0]["end_reason"] == "card_closed"
    evaluation = attempt["evaluation"]
    component = evaluation["components"]["service_call"]
    # Greeting the officer and passing nothing scores as if the call was never made (#127).
    assert component["score"] == 0
    assert component["items"][0]["facts_missing"] == [
        "address",
        "incident_type",
        "injured",
    ]
    codes = {e["code"] for e in evaluation["errors"]}
    assert "service_call_silent" in codes
    assert "service_not_informed" not in codes  # the dispatcher did call

    # A card accepted without any call: zero and the other detector.
    other = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    r = await status(client, token, other, status="accepted")
    r = await client.post(f"/api/attempts/{other}/finish", headers=bearer(token))
    evaluation = r.json()["attempt"]["evaluation"]
    assert evaluation["components"]["service_call"]["score"] == 0
    codes = {e["code"] for e in evaluation["errors"]}
    assert "service_not_informed" in codes
    assert "service_call_silent" not in codes


async def test_service_call_access_and_validation(client: AsyncClient) -> None:
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    token = await login(client, "student1")
    r = await start(client, token, attempt_id, service="nope")
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_service"
    other = await login(client, "student2")
    r = await start(client, other, attempt_id)
    assert r.status_code in (403, 404)
    teacher = await login(client, "teacher1")
    r = await start(client, teacher, attempt_id)
    assert r.status_code == 403
    # A 112 call attempt has no service calls.
    intake = await make_attempt()
    r = await start(client, token, intake)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_card_response"
    # The teacher can read the call of their student.
    call_id = (await start(client, token, attempt_id)).json()["call"]["id"]
    r = await client.get(
        f"/api/attempts/{attempt_id}/service-call/{call_id}", headers=bearer(teacher)
    )
    assert r.status_code == 200 and r.json()["call"]["id"] == call_id
    r = await client.get(
        f"/api/attempts/{attempt_id}/service-call/{uuid.uuid4().hex}", headers=bearer(token)
    )
    assert r.status_code == 404
    async with SessionLocal() as session:
        stored = await session.get(Attempt, attempt_id)
        assert stored.service_calls[0]["id"] == call_id
        assert stored.dialog == []


async def test_utterance_without_stt_says_to_type(client: AsyncClient) -> None:
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    token = await login(client, "student1")
    started = (await start(client, token, attempt_id)).json()
    assert started["stt_available"] is False
    call_id = started["call"]["id"]
    r = await client.post(
        f"/api/attempts/{attempt_id}/service-call/{call_id}/utterance",
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
            return Transcript(
                "Улица Молостовых, дом 10, корпус 1, пострадавших нет", "whisper", 1.5, 300
            )

    monkeypatch.setattr("app.telephony.service_calls.get_stt_provider", lambda: FakeSTT())
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    token = await login(client, "student1")
    started = (await start(client, token, attempt_id)).json()
    assert started["stt_available"] is True
    call_id = started["call"]["id"]
    r = await client.post(
        f"/api/attempts/{attempt_id}/service-call/{call_id}/utterance",
        headers=bearer(token),
        files={"file": ("q.webm", b"\x1aE\xdf\xa3....", "audio/webm")},
        data={"action_id": "u-1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["heard_text"].startswith("Улица Молостовых")
    assert "улица Молостовых" in FakeSTT.hints and "район Ивановское" in FakeSTT.hints
    dispatcher = [t for t in body["call"]["turns"] if t["role"] == "operator"]
    assert dispatcher[-1]["heard"] is True
    assert set(body["call"]["facts_passed"]) == {"address", "injured"}
    # A retry with the same action id does not ask the officer twice.
    r = await client.post(
        f"/api/attempts/{attempt_id}/service-call/{call_id}/utterance",
        headers=bearer(token),
        files={"file": ("q.webm", b"\x1aE\xdf\xa3....", "audio/webm")},
        data={"action_id": "u-1"},
    )
    assert r.status_code == 200 and r.json()["applied"] is False
    assert len(r.json()["call"]["turns"]) == 3
    # Empty audio and a closed call are refused.
    r = await client.post(
        f"/api/attempts/{attempt_id}/service-call/{call_id}/utterance",
        headers=bearer(token),
        files={"file": ("q.wav", b"", "audio/wav")},
    )
    assert r.status_code == 422
    await end(client, token, attempt_id, call_id)
    r = await client.post(
        f"/api/attempts/{attempt_id}/service-call/{call_id}/utterance",
        headers=bearer(token),
        files={"file": ("q.wav", b"RIFF....WAVE", "audio/wav")},
    )
    assert r.status_code == 409


async def test_repeat_call_does_not_take_the_card_again(client: AsyncClient) -> None:
    """The dispatcher calls the service he has passed the card to (замечание 27.09.2026): the
    officer says the card is with them and where the squad is, asks for nothing, requires
    nothing; the first call still counts in the evaluation."""
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    token = await login(client, "student1")

    first = (await start(client, token, attempt_id)).json()["call"]
    assert "repeat" not in first or not first.get("repeat")
    r = await say(
        client,
        token,
        attempt_id,
        first["id"],
        "Улица Молостовых, дом 10, корпус 1, нет отопления в трёх домах, пострадавших нет",
    )
    assert set(r.json()["call"]["facts_passed"]) >= {"address", "incident_type", "injured"}
    await end(client, token, attempt_id, first["id"])

    r = await start(client, token, attempt_id)
    assert r.status_code == 200, r.text
    again = r.json()["call"]
    opening = again["turns"][0]["text"]
    assert "уже приняли" in opening and "слушаю" in opening
    assert again["facts_required"] == []
    # Whatever the dispatcher says, the officer does not ask for the card again.
    for text in ("Алло, как там у вас?", "Улица Молостовых, дом 10", "Пострадавших нет"):
        r = await say(client, token, attempt_id, again["id"], text)
        assert r.status_code == 200, r.text
        reply = r.json()["call"]["turns"][-1]
        assert reply["role"] == "caller"
        assert not reply["text"].rstrip().endswith("?"), reply["text"]
    await end(client, token, attempt_id, again["id"])

    # A call to another service is a first call there.
    other = (await start(client, token, attempt_id, "mosvodokanal")).json()["call"]
    assert "уже приняли" not in other["turns"][0]["text"]
    await end(client, token, attempt_id, other["id"])

    for body in (
        {"status": "accepted"},
        {"status": "response_started", "order_number": "МОЭК-4127", "comment": "Бригада"},
        {"status": "arrived"},
        {"status": "works_started", "comment": "Проверка ИТП"},
        {"status": "works_done", "comment": "Отопление восстановлено"},
    ):
        r = await status(client, token, attempt_id, **body)
        assert r.status_code == 200, r.text
    component = r.json()["attempt"]["evaluation"]["components"]["service_call"]
    assert component["items"][0]["facts_missing"] == []


async def test_lesson_without_the_phone_box_calls_in_the_browser(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Telephony is up, the lesson has no «звонок на телефон» box: the officer answers in the
    browser at once, as on a stand without telephony (решение пользователя 27.09.2026)."""
    from app.telephony import service as telephony

    monkeypatch.setattr(telephony, "telephony_active", lambda: True)
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    token = await login(client, "student1")
    r = await start(client, token, attempt_id)
    assert r.status_code == 200, r.text
    call = r.json()["call"]
    assert call["telephony"] is False
    assert call["answered"] is True and call["turns"][0]["role"] == "caller"
    await end(client, token, attempt_id, call["id"])
