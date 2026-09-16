"""Telephony API without Asterisk (TELEPHONY_ENABLED=false): softphone credentials, the
current call, the call controls of the panel and their marks, the recording, the
administrator's telephony settings."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config import get_settings
from app.db import SessionLocal
from app.models import Attempt
from tests.api.conftest import DATA_DIR
from tests.api.test_dialog import make_attempt, say
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)
DROPS_CALL = "call_2-2_skandal_sryv_zvonka"


async def student(client: AsyncClient) -> dict:
    return await login(client, "student1")


async def call_action(client: AsyncClient, token: dict, attempt_id: uuid.UUID, action: str):
    return await client.post(f"/api/attempts/{attempt_id}/{action}", headers=bearer(token))


class TestSipAccount:
    async def test_account_is_created_once_and_stays_stable(self, client: AsyncClient):
        token = await student(client)
        first = await client.get("/api/me/sip", headers=bearer(token))
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["enabled"] is False
        assert body["username"] == "stu-student1"
        assert body["phone_username"] == "phone-student1"
        assert body["ws_path"] == "/ws/sip"
        assert len(body["password"]) >= 20
        second = await client.get("/api/me/sip", headers=bearer(token))
        assert second.json()["password"] == body["password"]

    async def test_only_students_have_softphones(self, client: AsyncClient):
        token = await login(client, "teacher1")
        r = await client.get("/api/me/sip", headers=bearer(token))
        assert r.status_code == 403


class TestCurrentCall:
    async def test_no_call(self, client: AsyncClient):
        token = await student(client)
        # student1 may have a running card-response session: no call-intake attempt = null.
        r = await client.get("/api/me/call", headers=bearer(token))
        assert r.status_code == 200

    async def test_ringing_attempt_is_current(self, client: AsyncClient):
        attempt_id = await make_attempt()
        token = await student(client)
        r = await client.get("/api/me/call", headers=bearer(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["attempt_id"] == str(attempt_id)
        assert body["call_state"] == "idle"
        assert body["caller_number"].isdigit()
        assert body["scenario_title"]


class TestCallControl:
    async def test_answer_returns_opening_and_marks_answered(self, client: AsyncClient):
        attempt_id = await make_attempt()
        token = await student(client)
        r = await call_action(client, token, attempt_id, "answer")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["opening"]["role"] == "caller"
        assert body["opening"]["index"] == 0
        assert body["opening"]["text"]
        assert body["dialog"]["call"]["state"] == "answered"
        assert body["dialog"]["call"]["telephony"] is False
        assert body["dialog"]["answered_at"]
        # Answering twice changes nothing.
        again = await call_action(client, token, attempt_id, "answer")
        assert again.status_code == 200
        assert len(again.json()["dialog"]["turns"]) == 1

    async def test_hangup_closes_the_dialog(self, client: AsyncClient):
        attempt_id = await make_attempt()
        token = await student(client)
        await call_action(client, token, attempt_id, "answer")
        r = await call_action(client, token, attempt_id, "hangup")
        assert r.status_code == 200, r.text
        call = r.json()["dialog"]["call"]
        assert call["state"] == "ended"
        assert call["end_reason"] == "hangup"
        assert call["ended_at"]
        # No more talking after the call ended.
        r = await say(client, token, attempt_id, "Алло?")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "call_ended"
        r = await call_action(client, token, attempt_id, "answer")
        assert r.status_code == 409
        # The current call is gone.
        r = await client.get("/api/me/call", headers=bearer(token))
        assert r.json() is None or r.json()["attempt_id"] != str(attempt_id)

    @pytest.mark.parametrize(
        ("action", "reason", "mark"),
        [
            ("no-contact", "no_contact", "no_contact_marked"),
            ("call-dropped", "call_dropped", "call_dropped_marked"),
        ],
    )
    async def test_marks_end_the_call_and_are_stored(
        self, client: AsyncClient, action: str, reason: str, mark: str
    ):
        attempt_id = await make_attempt()
        token = await student(client)
        r = await call_action(client, token, attempt_id, action)
        assert r.status_code == 200, r.text
        call = r.json()["dialog"]["call"]
        assert call["state"] == "ended"
        assert call["end_reason"] == reason
        assert call[mark] is True
        async with SessionLocal() as session:
            attempt = await session.get(Attempt, attempt_id)
            assert getattr(attempt, mark) is True
            assert attempt.call_end_reason == reason

    async def test_caller_hangs_up_after_second_question(self, client: AsyncClient):
        attempt_id = await make_attempt(DROPS_CALL)
        token = await student(client)
        first = await say(client, token, attempt_id, "Что случилось?")
        assert first.status_code == 200, first.text
        assert first.json()["call_ended"] is False
        second = await say(client, token, attempt_id, "Назовите адрес")
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["call_ended"] is True
        assert body["dialog"]["call"]["state"] == "ended"
        assert body["dialog"]["call"]["end_reason"] == "caller_hangup"
        third = await say(client, token, attempt_id, "Алло?")
        assert third.status_code == 409

    async def test_teacher_cannot_control_the_call(self, client: AsyncClient):
        attempt_id = await make_attempt()
        token = await login(client, "teacher1")
        r = await call_action(client, token, attempt_id, "hangup")
        assert r.status_code == 403

    async def test_card_response_attempt_has_no_call(self, client: AsyncClient):
        attempt_id = await make_attempt("card_2-1_zadymlenie_musoroprovoda", mode="card_response")
        token = await student(client)
        r = await call_action(client, token, attempt_id, "hangup")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "not_call_intake"


class TestRecording:
    async def test_missing_recording(self, client: AsyncClient):
        attempt_id = await make_attempt()
        token = await student(client)
        r = await client.get(f"/api/attempts/{attempt_id}/recording", headers=bearer(token))
        assert r.status_code == 404

    async def test_recording_is_served_to_owner_and_teacher(self, client: AsyncClient):
        attempt_id = await make_attempt()
        root = Path(get_settings().storage_dir) / "recordings"
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{attempt_id.hex}.wav").write_bytes(b"RIFF" + bytes(40))
        async with SessionLocal() as session:
            attempt = await session.get(Attempt, attempt_id)
            attempt.recording_path = f"recordings/{attempt_id.hex}.wav"
            await session.commit()
        for who in ("student1", "teacher1"):
            token = await login(client, who)
            r = await client.get(f"/api/attempts/{attempt_id}/recording", headers=bearer(token))
            assert r.status_code == 200, (who, r.text)
            assert r.headers["content-type"].startswith("audio/wav")
        token = await login(client, "student2")
        r = await client.get(f"/api/attempts/{attempt_id}/recording", headers=bearer(token))
        assert r.status_code == 404


class TestAdminSettings:
    async def test_defaults_and_patch(self, client: AsyncClient):
        token = await login(client, "admin")
        r = await client.get("/api/admin/settings", headers=bearer(token))
        assert r.status_code == 200, r.text
        telephony = r.json()["telephony"]
        assert telephony["enabled"] is False
        assert telephony["ring_timeout_seconds"] == 45
        assert telephony["webrtc_codecs"] == ["opus", "g722", "alaw", "ulaw"]

        r = await client.patch(
            "/api/admin/settings",
            headers=bearer(token),
            json={"telephony": {"ring_timeout_seconds": 20, "webrtc_codecs": ["Opus", "alaw"]}},
        )
        assert r.status_code == 200, r.text
        telephony = r.json()["telephony"]
        assert telephony["ring_timeout_seconds"] == 20
        assert telephony["webrtc_codecs"] == ["opus", "alaw"]
        r = await client.get("/api/admin/settings", headers=bearer(token))
        assert r.json()["telephony"]["ring_timeout_seconds"] == 20

    async def test_validation_and_access(self, client: AsyncClient):
        token = await login(client, "admin")
        r = await client.patch(
            "/api/admin/settings",
            headers=bearer(token),
            json={"telephony": {"phone_codecs": ["mp3"]}},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "bad_settings"
        r = await client.patch(
            "/api/admin/settings",
            headers=bearer(token),
            json={"telephony": {"ring_timeout_seconds": 1}},
        )
        assert r.status_code == 422
        teacher = await login(client, "teacher1")
        r = await client.get("/api/admin/settings", headers=bearer(teacher))
        assert r.status_code == 403
