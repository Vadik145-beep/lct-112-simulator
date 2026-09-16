"""Call-intake mode end to end on the API (plan wave 7): a lesson in the mode, one call at a
time, the draft, «сохранить» with the score, idempotency, the reference after closing, the
other-region detector, access rules and the sweep that leaves calls alone."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Attempt, AuditLog, Evaluation, Scenario, SessionEvent
from app.training.service import sweep_not_notified, utcnow
from tests.api.conftest import DATA_DIR
from tests.api.test_sessions import create_session, journal, start
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)

GAS_PIPE = "call_31-3_svist_gazovoy_truby"
VOLZHSKY = "call_1-3_rebenok_velosiped_volzhskiy"
DROPPED = "call_2-2_skandal_sryv_zvonka"

GAS_PIPE_CARD = {
    "signs_path": ["Запах газа в помещении", "От газововго оборудования"],
    "incident_type": "13.2.4.0",
    "flags": {"injured": False, "no_access": False, "threat": False},
    "services": ["101", "mosgaz", "mosoblgaz", "mayor_office", "territorial_oiv"],
    "address": {
        "street": "улица Вавилова",
        "house": "81",
        "building": "1",
        "apartment": "5",
        "entrance": "1",
        "floor": "2",
        "code": "5В",
        "okrug": "ЮЗАО",
        "district": "Ломоносовский район",
    },
    "caller": {"name": "Петров Иван Сергеевич", "role": "жилец", "phone": "916-320-12-83"},
    "description": "Свист на газовой трубе в квартире, запах газа. 03 не требуется.",
}


async def scenario_ids(*keys: str) -> list[str]:
    async with SessionLocal() as session:
        rows = {
            s.seed_key: str(s.id)
            for s in await session.scalars(select(Scenario).where(Scenario.seed_key.in_(keys)))
        }
    return [rows[k] for k in keys]


async def call_session(client: AsyncClient, teacher: dict, *keys: str, **overrides) -> dict:
    """A running call-intake lesson for «Учебная-1» with the given scenario queue."""
    settings = {
        "title": "Приём вызова",
        "mode": "call_intake",
        "difficulty": 3,
        "service_profile": [],
        "norm_seconds": 90,
        "scenario_ids": await scenario_ids(*keys),
        "dialog_mode": "select",
        "voice_enabled": False,
        **overrides,
    }
    created = await create_session(client, teacher, **settings)
    await start(client, teacher, created["id"])
    return created


async def current_call(client: AsyncClient, student: dict, session_id: str) -> dict:
    items = (await journal(client, student, session_id))["items"]
    active = [i for i in items if i["state"] in ("issued", "received", "in_progress")]
    assert len(active) == 1, items
    return active[0]


async def submit(client: AsyncClient, token: dict, attempt_id: str, card: dict, sid: str):
    return await client.post(
        f"/api/attempts/{attempt_id}/submit",
        headers=bearer(token),
        json={"card": card, "client_submission_id": sid},
    )


async def test_lesson_in_call_intake_mode_issues_one_call_at_a_time(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await call_session(client, teacher, GAS_PIPE, VOLZHSKY)
    assert created["mode"] == "call_intake"
    assert created["dialog_mode"] == "select"
    student = await login(client, "student1")
    assignments = await client.get("/api/me/assignments", headers=bearer(student))
    mine = next(a for a in assignments.json() if a["id"] == created["id"])
    assert mine["mode"] == "call_intake"
    # Difficulty 3 would put three cards in a journal; a call comes one at a time.
    call = await current_call(client, student, created["id"])
    assert call["state"] == "issued"
    r = await client.get(f"/api/attempts/{call['attempt_id']}", headers=bearer(student))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session"]["mode"] == "call_intake"
    assert body["intake"]["caller_phone"] == "916-320-12-83"
    assert body["intake"]["draft"] is None
    assert body["intake"]["title"] is None  # no spoilers before the card is saved
    assert body["reference"] is None
    assert "address" in body["intake"]["required_topics"]


async def test_full_call_draft_submit_and_review(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await call_session(client, teacher, GAS_PIPE, VOLZHSKY)
    student = await login(client, "student1")
    attempt_id = (await current_call(client, student, created["id"]))["attempt_id"]

    # The conversation: the address question gets the address reply.
    r = await client.post(
        f"/api/attempts/{attempt_id}/say",
        headers=bearer(student),
        json={"text": "Скажите адрес, пожалуйста"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["caller"]["topics"] == ["address"]
    assert r.json()["dialog"]["answered_at"] is not None

    # A draft while the call goes on: stored, the teacher's monitoring gets a stage.
    partial = {**GAS_PIPE_CARD, "description": "Свист на трубе"}
    r = await client.put(
        f"/api/attempts/{attempt_id}/draft", headers=bearer(student), json={"card": partial}
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/attempts/{attempt_id}", headers=bearer(student))
    assert r.json()["intake"]["draft"]["description"] == "Свист на трубе"
    assert r.json()["intake"]["draft"]["updated_at"]
    assert r.json()["state"] == "in_progress"

    # «Сохранить»: scored at once, the next call rings.
    r = await submit(client, student, attempt_id, GAS_PIPE_CARD, "sub-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True
    assert len(body["issued"]) == 1
    attempt = body["attempt"]
    assert attempt["state"] == "evaluated"
    assert attempt["submitted_at"] is not None
    assert attempt["intake"]["title"] == "Свист от газовой трубы в квартире"
    assert attempt["reference"]["incident_type"] == "13.2.4.0"
    evaluation = attempt["evaluation"]
    assert evaluation["checked_text"].startswith("Свист на газовой трубе")
    components = evaluation["components"]
    assert set(components) == {
        "survey_card",
        "flags_services",
        "address",
        "required_topics",
        "description",
        "time",
        "typical_errors",
        "grammar",
    }
    assert components["survey_card"]["score"] == components["survey_card"]["max"]
    assert components["address"]["score"] == components["address"]["max"]
    assert components["time"]["score"] == components["time"]["max"]
    # The opening turn carries no labels: the engine infers its topics from the text.
    assert "address" in components["required_topics"]["items"][0]["covered"]
    assert "address_not_asked" not in {e["code"] for e in evaluation["errors"]}
    services = components["flags_services"]["items"][1]
    assert services["missing"]  # not every service of the reference was added by hand

    # The next call is the second scenario of the queue.
    call = await current_call(client, student, created["id"])
    assert call["attempt_id"] == body["issued"][0]

    async with SessionLocal() as session:
        row = await session.get(Evaluation, uuid.UUID(attempt_id))
        assert row is not None and row.total == evaluation["total"]
        stored = await session.get(Attempt, uuid.UUID(attempt_id))
        assert stored.client_submission_id == "sub-1"
        assert stored.draft["services"] == GAS_PIPE_CARD["services"]
        types = [
            e.type
            for e in await session.scalars(
                select(SessionEvent)
                .where(SessionEvent.session_id == uuid.UUID(created["id"]))
                .order_by(SessionEvent.seq)
            )
        ]
    assert types[-4:] == [
        "attempt.submitted",
        "attempt.evaluated",
        "attempt.issued",
        "attempt.issued",
    ] or ("attempt.submitted" in types and "attempt.evaluated" in types)

    # The teacher sees the review with the transcript.
    r = await client.get(f"/api/attempts/{attempt_id}", headers=bearer(teacher))
    assert r.status_code == 200
    assert r.json()["evaluation"]["total"] == evaluation["total"]
    r = await client.get(f"/api/attempts/{attempt_id}/dialog", headers=bearer(teacher))
    assert [t["role"] for t in r.json()["turns"]] == ["caller", "operator", "caller"]


async def test_submit_is_idempotent_and_closed_card_refuses_changes(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await call_session(client, teacher, GAS_PIPE)
    student = await login(client, "student1")
    attempt_id = (await current_call(client, student, created["id"]))["attempt_id"]
    first = await submit(client, student, attempt_id, GAS_PIPE_CARD, "same-id")
    assert first.status_code == 200, first.text
    again = await submit(client, student, attempt_id, GAS_PIPE_CARD, "same-id")
    assert again.status_code == 200
    assert again.json()["applied"] is False
    assert (
        again.json()["attempt"]["evaluation"]["total"]
        == first.json()["attempt"]["evaluation"]["total"]
    )
    other = await submit(client, student, attempt_id, GAS_PIPE_CARD, "another-id")
    assert other.status_code == 409
    assert other.json()["error"]["code"] == "card_closed"
    r = await client.put(
        f"/api/attempts/{attempt_id}/draft", headers=bearer(student), json={"card": GAS_PIPE_CARD}
    )
    assert r.status_code == 409
    # The queue had one scenario: nothing else rings.
    assert first.json()["issued"] == []


async def test_other_region_detector(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await call_session(client, teacher, VOLZHSKY)
    student = await login(client, "student1")
    attempt_id = (await current_call(client, student, created["id"]))["attempt_id"]
    card = {
        "signs_path": ["Травма"],
        "incident_type": "22.53.0.0",
        "flags": {"injured": True},
        "services": ["103"],
        "address": {"street": "улица Карла Маркса"},
        "caller": {"name": "Смирнова Анна", "role": "мать"},
        "description": "Ребёнок 11 лет упал с велосипеда.",
    }
    r = await submit(client, student, attempt_id, card, "s-1")
    assert r.status_code == 200, r.text
    codes = {e["code"] for e in r.json()["attempt"]["evaluation"]["errors"]}
    assert "region_not_clarified" in codes

    created = await call_session(client, teacher, VOLZHSKY)
    attempt_id = (await current_call(client, student, created["id"]))["attempt_id"]
    card["address"] = {**card["address"], "region": "Волгоградская область", "city": "Волжский"}
    r = await submit(client, student, attempt_id, card, "s-2")
    assert r.status_code == 200, r.text
    codes = {e["code"] for e in r.json()["attempt"]["evaluation"]["errors"]}
    assert "region_not_clarified" not in codes


async def test_dropped_call_needs_the_mark(client: AsyncClient) -> None:
    """Scenario «бросил трубку»: without «срыв звонка» the detector fires; with the mark
    (set by the call panel endpoint) it does not, and the mark is audited."""
    teacher = await login(client, "teacher1")
    created = await call_session(client, teacher, DROPPED)
    student = await login(client, "student1")
    attempt_id = (await current_call(client, student, created["id"]))["attempt_id"]
    card = {
        "signs_path": [
            "Нарушение общественного порядка",
            "Скандал в помещении, семейные конфликты",
        ],
        "incident_type": "15.11.7.0",
        "flags": {},
        "services": ["102"],
        "address": {"street": "улица Народного Ополчения", "house": "5", "structure": "1"},
        "caller": {"role": "продавец"},
        "description": "Скандал в магазине, клиент угрожает.",
    }
    r = await submit(client, student, attempt_id, card, "d-1")
    assert r.status_code == 200, r.text
    codes = {e["code"] for e in r.json()["attempt"]["evaluation"]["errors"]}
    assert "no_call_dropped_mark" in codes

    created = await call_session(client, teacher, DROPPED)
    attempt_id = (await current_call(client, student, created["id"]))["attempt_id"]
    r = await client.post(f"/api/attempts/{attempt_id}/call-dropped", headers=bearer(student))
    assert r.status_code == 200, r.text
    r = await submit(client, student, attempt_id, card, "d-2")
    codes = {e["code"] for e in r.json()["attempt"]["evaluation"]["errors"]}
    assert "no_call_dropped_mark" not in codes
    async with SessionLocal() as session:
        row = await session.scalar(
            select(AuditLog)
            .where(AuditLog.action == "call.end", AuditLog.entity_id == attempt_id)
            .order_by(AuditLog.id.desc())
        )
        assert row is not None and row.details["reason"] == "call_dropped"


async def test_access_rules(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await call_session(client, teacher, GAS_PIPE)
    student = await login(client, "student1")
    attempt_id = (await current_call(client, student, created["id"]))["attempt_id"]
    r = await submit(client, teacher, attempt_id, GAS_PIPE_CARD, "t-1")
    assert r.status_code == 403
    other = await login(client, "student2")
    r = await submit(client, other, attempt_id, GAS_PIPE_CARD, "o-1")
    assert r.status_code == 404
    r = await client.put(
        f"/api/attempts/{attempt_id}/draft",
        headers=bearer(student),
        json={"card": {**GAS_PIPE_CARD, "bogus": 1}},
    )
    assert r.status_code == 422


async def test_sweep_leaves_calls_alone_and_finish_scores_the_draft(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await call_session(client, teacher, GAS_PIPE, norm_seconds=5)
    student = await login(client, "student1")
    attempt_id = (await current_call(client, student, created["id"]))["attempt_id"]
    r = await client.put(
        f"/api/attempts/{attempt_id}/draft", headers=bearer(student), json={"card": GAS_PIPE_CARD}
    )
    assert r.status_code == 200
    async with SessionLocal() as session:
        events = await sweep_not_notified(session, now=utcnow() + timedelta(seconds=600))
        await session.commit()
        attempt = await session.get(Attempt, uuid.UUID(attempt_id))
        assert attempt.card_status == "registered"
    assert all(e.payload.get("attempt_id") != uuid.UUID(attempt_id) for e in events)

    r = await client.post(f"/api/sessions/{created['id']}/finish", headers=bearer(teacher))
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/attempts/{attempt_id}", headers=bearer(student))
    body = r.json()
    assert body["state"] == "evaluated"
    # The draft is what got scored: the full address gives the full address score.
    address = body["evaluation"]["components"]["address"]
    assert address["score"] == address["max"]
