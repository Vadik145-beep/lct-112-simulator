"""Card-response training API: journal, opening a card, statuses, closing, events."""

import uuid
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import SessionLocal
from app.models import (
    MODE_CARD_RESPONSE,
    SESSION_RUNNING,
    Attempt,
    Group,
    Scenario,
    SessionEvent,
    TrainingSession,
    User,
)
from app.training.service import sweep_not_notified, utcnow
from tests.api.conftest import DATA_DIR
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)


async def make_session(
    *, difficulty: int = 1, norm_seconds: int = 30, scenario_keys: list[str] | None = None
) -> uuid.UUID:
    """A running session for «Учебная-1» with the seed scenarios (or a subset) as queue."""
    async with SessionLocal() as session:
        teacher = await session.scalar(select(User).where(User.login == "teacher1"))
        group = await session.scalar(select(Group).where(Group.title == "Учебная-1"))
        query = select(Scenario).where(
            Scenario.kind == MODE_CARD_RESPONSE, Scenario.service_code == "territorial_oiv"
        )
        if scenario_keys:
            query = query.where(Scenario.seed_key.in_(scenario_keys))
        scenarios = sorted(
            await session.scalars(query), key=lambda s: (s.difficulty, s.seed_key or "")
        )
        ts = TrainingSession(
            title="Тест",
            teacher_id=teacher.id,
            group_id=group.id,
            mode=MODE_CARD_RESPONSE,
            scenario_ids=[s.id for s in scenarios],
            difficulty=difficulty,
            service_profile=["territorial_oiv"],
            norm_seconds=norm_seconds,
            pass_threshold=70,
            status=SESSION_RUNNING,
            started_at=utcnow(),
        )
        session.add(ts)
        await session.commit()
        return ts.id


async def journal(client: AsyncClient, token: dict, session_id: uuid.UUID, **params) -> dict:
    r = await client.get(
        f"/api/sessions/{session_id}/journal", headers=bearer(token), params=params
    )
    assert r.status_code == 200, r.text
    return r.json()


async def set_status(client: AsyncClient, token: dict, attempt_id: str, **body) -> dict:
    r = await client.post(f"/api/attempts/{attempt_id}/status", headers=bearer(token), json=body)
    return r


async def test_assignments_list_running_session_first(client: AsyncClient) -> None:
    token = await login(client, "student1")
    r = await client.get("/api/me/assignments", headers=bearer(token))
    assert r.status_code == 200, r.text
    items = r.json()
    assert items and items[0]["status"] == "running"
    assert items[0]["mode"] == "card_response"
    assert items[0]["service"]["code"] == "territorial_oiv"
    assert items[0]["norm_seconds"] == 30

    teacher = await login(client, "teacher1")
    r = await client.get("/api/me/assignments", headers=bearer(teacher))
    assert r.status_code == 403


async def test_journal_issues_cards_by_difficulty(client: AsyncClient) -> None:
    session_id = await make_session(difficulty=3)
    token = await login(client, "student1")
    data = await journal(client, token, session_id)
    assert data["total"] == 3
    assert data["session"]["service"]["short_title"] == "Управа"
    assert data["arm"]["dispatcher"].startswith("Кузнецов")
    first = data["items"][0]
    assert first["state"] == "issued"
    assert first["response_status_title"] == "Добавлена"
    assert first["card_status_title"] == "Зарегистрирована"
    assert first["card_status_alert"] is False
    assert first["card_number"].startswith("3826")
    assert {i["card_number"] for i in data["items"]} == {"38260311", "38260340", "38261102"}
    assert first["norm_seconds"] == 30
    titles = {i["incident_title"] for i in data["items"]}
    assert "задымление: мусоропровод" in titles
    own = [s for s in first["services"] if s["is_own"]]
    assert own and own[0]["code"] == "territorial_oiv" and own[0]["status"] == "added"
    others = {s["code"]: s for s in first["services"] if not s["is_own"]}
    assert "101" in others and others["101"]["short_title"] == "101"
    assert others["101"]["status"] in ("received", "accepted")
    # Opening the journal again does not issue more cards.
    again = await journal(client, token, session_id)
    assert again["total"] == 3
    assert again["last_seq"] == 3


async def test_journal_pagination_and_address(client: AsyncClient) -> None:
    session_id = await make_session(difficulty=3)
    token = await login(client, "student1")
    page = await journal(client, token, session_id, per_page=10)
    item = next(i for i in page["items"] if i["card_number"] == "38260311")
    assert item["incident_title"] == "задымление: мусоропровод"
    assert item["address"].startswith(
        "Россия, Москва, (СЗАО, район Хорошёво-Мнёвники), улица Берзарина, 21"
    )
    assert item["caller"]["name"] == "Ким Олег Юрьевич"
    assert item["injured"] is False
    r = await client.get(
        f"/api/sessions/{session_id}/journal", headers=bearer(token), params={"per_page": 7}
    )
    assert r.status_code == 422


async def test_open_sets_received_once(client: AsyncClient) -> None:
    session_id = await make_session(scenario_keys=["card_2-1_zadymlenie_musoroprovoda"])
    token = await login(client, "student1")
    attempt_id = (await journal(client, token, session_id))["items"][0]["attempt_id"]
    r = await client.post(f"/api/attempts/{attempt_id}/open", headers=bearer(token))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["state"] == "received"
    assert data["response_status_title"] == "Получена службой"
    assert data["received_at"] is not None
    assert [t["code"] for t in data["transitions"]] == ["accepted", "rejected"]
    assert data["reference"] is None
    assert data["card"]["incident"]["group_title"] == "Пожары и задымления"
    assert data["card"]["address"]["street"] == "улица Берзарина"
    received_at = data["received_at"]
    r = await client.post(f"/api/attempts/{attempt_id}/open", headers=bearer(token))
    assert r.json()["received_at"] == received_at
    assert len([e for e in r.json()["status_log"] if e["status"] == "received"]) == 1


async def test_next_card_is_added_when_the_journal_is_opened(client: AsyncClient) -> None:
    """docs/BUGS.md 9: the 30 s of a card run from «Добавлена», so the next card must not be
    added while the trainee is still on the closed card's review; it is added by the
    journal request, with a fresh ``issued_at``."""
    session_id = await make_session(
        difficulty=1, scenario_keys=["card_2-1_zadymlenie_musoroprovoda", "card_2-1_dubl"]
    )
    token = await login(client, "student1")
    first = (await journal(client, token, session_id))["items"][0]["attempt_id"]
    await client.post(f"/api/attempts/{first}/open", headers=bearer(token))
    r = await client.post(f"/api/attempts/{first}/finish", headers=bearer(token))
    assert r.status_code == 200, r.text
    assert r.json()["issued"] == []
    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count()).select_from(Attempt).where(Attempt.session_id == session_id)
        )
    assert count == 1  # nothing was added behind the trainee's back
    before = utcnow()
    page = await journal(client, token, session_id)
    assert page["total"] == 2
    second = page["items"][0]
    assert second["attempt_id"] != first
    assert second["state"] == "issued"
    assert datetime.fromisoformat(second["issued_at"]) >= before - timedelta(seconds=1)


async def test_full_chain_closes_card_and_issues_next(client: AsyncClient) -> None:
    session_id = await make_session(
        difficulty=1, scenario_keys=["card_2-1_zadymlenie_musoroprovoda", "card_2-1_dubl"]
    )
    started = utcnow()
    token = await login(client, "student1")
    attempt_id = (await journal(client, token, session_id))["items"][0]["attempt_id"]
    await client.post(f"/api/attempts/{attempt_id}/open", headers=bearer(token))

    r = await set_status(client, token, attempt_id, status="accepted")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["applied"] is True
    assert data["attempt"]["primary_status_at"] is not None
    assert data["attempt"]["state"] == "in_progress"

    r = await set_status(
        client,
        token,
        attempt_id,
        status="response_started",
        order_number="14-217",
        comment="Направлен дежурный слесарь",
    )
    assert r.status_code == 200, r.text
    r = await set_status(client, token, attempt_id, status="arrived")
    assert r.status_code == 200, r.text
    r = await set_status(
        client, token, attempt_id, status="works_started", comment="Мусоропровод вскрыт"
    )
    assert r.status_code == 200, r.text
    r = await set_status(
        client,
        token,
        attempt_id,
        status="works_done",
        comment="Задымление устранено, ствол промыт, пострадавших нет",
    )
    assert r.status_code == 200, r.text
    assert (utcnow() - started).total_seconds() < 10
    data = r.json()
    attempt = data["attempt"]
    assert attempt["state"] == "evaluated"
    assert attempt["card_status"] == "finished"
    assert attempt["card_status_title"] == "Завершена"
    assert attempt["submitted_at"] is not None
    assert attempt["transitions"] == []
    assert attempt["reference"]["decision"] == "accept"
    # Evaluated at once (PRD 9.2): full chain in time → decision, time and chain at maximum.
    evaluation = attempt["evaluation"]
    assert evaluation["passed"] is True
    assert evaluation["total"] >= 90
    assert evaluation["components"]["decision"]["score"] == 30
    assert evaluation["components"]["time"]["score"] == 20
    assert evaluation["components"]["status_chain"]["score"] == 20
    assert evaluation["errors"] == []
    assert "Задымление устранено" in evaluation["checked_text"]
    assert [e["status"] for e in attempt["status_log"]] == [
        "added",
        "received",
        "accepted",
        "response_started",
        "arrived",
        "works_started",
        "works_done",
    ]
    assert attempt["status_log"][3]["order_number"] == "14-217"
    # Queue mode: the next card is not issued by closing this one (its 30 s run from
    # «Добавлена», BUGS 9) — it appears when the trainee asks for the journal.
    assert data["issued"] == []
    page = await journal(client, token, session_id)
    assert page["total"] == 2
    assert page["items"][0]["state"] == "issued"

    r = await set_status(client, token, attempt_id, status="accepted")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "card_closed"


async def test_reject_requires_comment_and_marks_card(client: AsyncClient) -> None:
    session_id = await make_session(scenario_keys=["card_2-1_dubl"])
    token = await login(client, "student1")
    attempt_id = (await journal(client, token, session_id))["items"][0]["attempt_id"]

    r = await set_status(client, token, attempt_id, status="rejected")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "comment_required"
    assert "обязателен комментарий" in r.json()["error"]["message"].lower()

    r = await set_status(
        client,
        token,
        attempt_id,
        status="rejected",
        reject_reason="duplicate",
        comment="Дубль: реагирование по КП 38260311, информация передана слесарю",
    )
    assert r.status_code == 200, r.text
    attempt = r.json()["attempt"]
    assert attempt["state"] == "in_progress"
    assert attempt["card_status"] == "refused"
    assert attempt["card_status_alert"] is True
    assert [t["code"] for t in attempt["transitions"]] == ["accepted"]
    entry = attempt["status_log"][-1]
    assert entry["reject_reason_title"] == "Дубль"
    # A status without opening the card first still records «Получена службой».
    assert [e["status"] for e in attempt["status_log"]] == ["added", "received", "rejected"]

    r = await set_status(
        client, token, attempt_id, status="rejected", reject_reason="bogus", comment="x"
    )
    assert r.status_code == 422


async def test_invalid_transition_explains_options(client: AsyncClient) -> None:
    session_id = await make_session()
    token = await login(client, "student1")
    attempt_id = (await journal(client, token, session_id))["items"][0]["attempt_id"]
    r = await set_status(client, token, attempt_id, status="arrived")
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "not_allowed"
    assert "Принята" in error["message"] and "Не принята" in error["message"]
    r = await set_status(client, token, attempt_id, status="received")
    assert r.json()["error"]["code"] == "not_allowed"
    await set_status(client, token, attempt_id, status="accepted")
    r = await set_status(client, token, attempt_id, status="response_started")
    assert r.json()["error"]["code"] == "order_number_required"


async def test_status_is_idempotent_by_action_id(client: AsyncClient) -> None:
    session_id = await make_session()
    token = await login(client, "student1")
    attempt_id = (await journal(client, token, session_id))["items"][0]["attempt_id"]
    action = uuid.uuid4().hex
    first = await set_status(client, token, attempt_id, status="accepted", action_id=action)
    assert first.status_code == 200 and first.json()["applied"] is True
    second = await set_status(client, token, attempt_id, status="accepted", action_id=action)
    assert second.status_code == 200 and second.json()["applied"] is False
    log = second.json()["attempt"]["status_log"]
    assert [e["status"] for e in log].count("accepted") == 1


async def test_late_primary_status_marks_not_notified(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    session_id = await make_session(norm_seconds=30)
    token = await login(client, "student1")
    attempt_id = (await journal(client, token, session_id))["items"][0]["attempt_id"]
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE attempts SET issued_at = :t WHERE id = :id"),
            {"t": utcnow() - timedelta(seconds=31), "id": uuid.UUID(attempt_id)},
        )
    async with SessionLocal() as session:
        events = await sweep_not_notified(session)
        await session.commit()
        assert [e.type for e in events if e.payload["attempt_id"] == attempt_id] == [
            "card.status_changed"
        ]
        # A second sweep changes nothing.
        assert not [
            e for e in await sweep_not_notified(session) if e.payload["attempt_id"] == attempt_id
        ]
    item = (await journal(client, token, session_id))["items"][0]
    assert item["card_status"] == "not_notified"
    assert item["card_status_title"] == "Не оповещено"
    assert item["card_status_alert"] is True
    # A late «Принята» still counts: the card returns to the normal status, the timestamp
    # keeps the delay for the evaluation.
    r = await set_status(client, token, attempt_id, status="accepted")
    assert r.status_code == 200
    assert r.json()["attempt"]["card_status"] == "registered"
    assert r.json()["attempt"]["primary_status_at"] is not None


async def test_finish_without_final_status(client: AsyncClient) -> None:
    session_id = await make_session(difficulty=1)
    token = await login(client, "student1")
    attempt_id = (await journal(client, token, session_id))["items"][0]["attempt_id"]
    r = await client.post(f"/api/attempts/{attempt_id}/finish", headers=bearer(token))
    assert r.status_code == 200, r.text
    attempt = r.json()["attempt"]
    assert attempt["state"] == "evaluated"
    assert attempt["reference"] is not None
    # Closed without any status: the evaluation says so.
    assert attempt["evaluation"]["passed"] is False
    assert "no_status" in {e["code"] for e in attempt["evaluation"]["errors"]}
    assert r.json()["issued"] == []
    r = await client.post(f"/api/attempts/{attempt_id}/finish", headers=bearer(token))
    assert r.json()["applied"] is False


async def test_access_rules(client: AsyncClient) -> None:
    session_id = await make_session()
    token = await login(client, "student1")
    attempt_id = (await journal(client, token, session_id))["items"][0]["attempt_id"]

    other = await login(client, "student2")
    r = await client.get(f"/api/attempts/{attempt_id}", headers=bearer(other))
    assert r.status_code == 404
    r = await set_status(client, other, attempt_id, status="accepted")
    assert r.status_code == 404
    stranger = await login(client, "student7")  # other group
    r = await client.get(f"/api/sessions/{session_id}/journal", headers=bearer(stranger))
    assert r.status_code == 404

    teacher = await login(client, "teacher1")
    r = await client.get(f"/api/attempts/{attempt_id}", headers=bearer(teacher))
    assert r.status_code == 200
    r = await set_status(client, teacher, attempt_id, status="accepted")
    assert r.status_code == 403
    student_id = (await client.get("/api/me", headers=bearer(token))).json()["id"]
    r = await client.get(
        f"/api/sessions/{session_id}/journal",
        headers=bearer(teacher),
        params={"student_id": student_id},
    )
    assert r.status_code == 200 and r.json()["total"] == 1
    other_teacher = await login(client, "teacher2")
    r = await client.get(f"/api/attempts/{attempt_id}", headers=bearer(other_teacher))
    assert r.status_code == 404


async def test_events_are_dense_and_scoped(client: AsyncClient) -> None:
    session_id = await make_session()
    token = await login(client, "student1")
    attempt_id = (await journal(client, token, session_id))["items"][0]["attempt_id"]
    await client.post(f"/api/attempts/{attempt_id}/open", headers=bearer(token))
    await set_status(client, token, attempt_id, status="accepted")
    async with SessionLocal() as session:
        events = list(
            await session.scalars(
                select(SessionEvent)
                .where(SessionEvent.session_id == session_id)
                .order_by(SessionEvent.seq)
            )
        )
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert [e.type for e in events] == [
        "attempt.issued",
        "attempt.received",
        "attempt.status_changed",
    ]
    await set_status(client, token, attempt_id, status="works_done", comment="Готово")
    async with SessionLocal() as session:
        types = list(
            await session.scalars(
                select(SessionEvent.type)
                .where(SessionEvent.session_id == session_id)
                .order_by(SessionEvent.seq)
            )
        )
    assert types[3:] == [
        "attempt.status_changed",
        "card.status_changed",
        "attempt.submitted",
        "attempt.evaluated",
    ]
    # The next card is added by the journal request, not by closing (BUGS 9).
    await journal(client, token, session_id)
    async with SessionLocal() as session:
        last = await session.scalar(
            select(SessionEvent.type)
            .where(SessionEvent.session_id == session_id)
            .order_by(SessionEvent.seq.desc())
            .limit(1)
        )
    assert last == "attempt.issued"
    student_id = (await client.get("/api/me", headers=bearer(token))).json()["id"]
    assert all(str(e.student_id) == student_id for e in events)
    assert events[-1].payload["status"] == "accepted"


async def test_reference_search(client: AsyncClient) -> None:
    token = await login(client, "student1")
    r = await client.get("/api/reference/search", headers=bearer(token), params={"q": "не принята"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["memo"]
    assert all("принята" in h["text"].lower() and "не" in h["text"].lower() for h in data["memo"])
    assert all(h["page"] > 0 for h in data["memo"])
    r = await client.get(
        "/api/reference/search", headers=bearer(token), params={"q": "мусоропровод дым"}
    )
    hits = r.json()["types"]
    assert any(t["code"] == "1.5.6.2" for t in hits)
    assert hits[0]["group_title"] == "Пожары и задымления"
    r = await client.get("/api/reference/search", headers=bearer(token), params={"q": "я"})
    assert r.status_code == 422


async def test_restart_in_demo_mode_drops_own_cards(client: AsyncClient) -> None:
    session_id = await make_session(difficulty=3)
    token = await login(client, "student1")
    assert (await journal(client, token, session_id))["total"] == 3
    r = await client.post(f"/api/sessions/{session_id}/restart", headers=bearer(token))
    assert r.status_code == 204, r.text
    async with SessionLocal() as db:
        left = await db.scalar(
            select(func.count()).select_from(Attempt).where(Attempt.session_id == session_id)
        )
    assert left == 0
    # The journal starts the exercise again from the first card.
    assert (await journal(client, token, session_id))["total"] == 3
    teacher = await login(client, "teacher1")
    r = await client.post(f"/api/sessions/{session_id}/restart", headers=bearer(teacher))
    assert r.status_code == 404
