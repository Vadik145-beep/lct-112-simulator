"""Teacher API: groups, sessions from draft to report, access rules, monitoring events."""

import asyncio
import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import SessionLocal
from app.events import events_after
from app.models import Group, User
from app.scenarios import jobs
from app.training.service import sweep_not_notified, utcnow
from tests.api.conftest import DATA_DIR
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)

GROUP_1 = "Учебная-1"


async def group_id(title: str = GROUP_1) -> str:
    async with SessionLocal() as session:
        group = await session.scalar(select(Group).where(Group.title == title))
        assert group is not None
        return str(group.id)


async def create_session(client: AsyncClient, token: dict, **overrides) -> dict:
    body = {
        "title": "Карточки для управы",
        "group_id": await group_id(),
        "difficulty": 3,
        "service_profile": ["territorial_oiv"],
        "norm_seconds": 30,
        "pass_threshold": 70,
        **overrides,
    }
    r = await client.post("/api/sessions", headers=bearer(token), json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def start(client: AsyncClient, token: dict, session_id: str) -> dict:
    r = await client.post(f"/api/sessions/{session_id}/start", headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()


async def journal(client: AsyncClient, token: dict, session_id: str) -> dict:
    r = await client.get(f"/api/sessions/{session_id}/journal", headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()


async def set_status(client: AsyncClient, token: dict, attempt_id: str, **body):
    return await client.post(f"/api/attempts/{attempt_id}/status", headers=bearer(token), json=body)


async def events(session_id: str, after: int = 0) -> list:
    async with SessionLocal() as session:
        return await events_after(session, uuid.UUID(session_id), after, student_id=None)


async def wait_job(client: AsyncClient, token: dict, job_id: str) -> dict:
    for _ in range(200):
        r = await client.get(f"/api/jobs/{job_id}", headers=bearer(token))
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] in {"done", "failed"}:
            return job
        await asyncio.sleep(0.1)
    raise AssertionError(f"задача не завершилась: {job}")


@pytest.fixture(autouse=True)
async def finish_jobs():
    yield
    await jobs.wait_all(max_seconds=10)


# ---------------------------------------------------------------- groups


async def test_groups_list_create_patch(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    r = await client.get("/api/groups", headers=bearer(teacher))
    assert r.status_code == 200, r.text
    groups = r.json()
    assert [g["title"] for g in groups] == [GROUP_1]
    assert len(groups[0]["members"]) == 6
    assert groups[0]["members"][0]["full_name"] < groups[0]["members"][-1]["full_name"]

    r = await client.get("/api/students", headers=bearer(teacher))
    assert r.status_code == 200
    students = r.json()
    assert len(students) == 12
    picked = [s["id"] for s in students[:2]]

    r = await client.post(
        "/api/groups", headers=bearer(teacher), json={"title": "Вечерняя", "student_ids": picked}
    )
    assert r.status_code == 201, r.text
    group = r.json()
    assert group["title"] == "Вечерняя"
    assert {m["id"] for m in group["members"]} == set(picked)

    r = await client.patch(
        f"/api/groups/{group['id']}",
        headers=bearer(teacher),
        json={"title": "Вечерняя-2", "student_ids": picked[:1]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Вечерняя-2"
    assert [m["id"] for m in r.json()["members"]] == picked[:1]

    # A colleague cannot edit it, a teacher cannot be a member, a student sees nothing.
    other = await login(client, "teacher2")
    r = await client.patch(
        f"/api/groups/{group['id']}", headers=bearer(other), json={"title": "Чужая"}
    )
    assert r.status_code == 403
    teacher_id = (await client.get("/api/me", headers=bearer(teacher))).json()["id"]
    r = await client.post(
        "/api/groups", headers=bearer(teacher), json={"title": "X", "student_ids": [teacher_id]}
    )
    assert r.status_code == 422
    student = await login(client, "student1")
    assert (await client.get("/api/groups", headers=bearer(student))).status_code == 403


# ---------------------------------------------------------------- sessions


async def test_student_cannot_create_session(client: AsyncClient) -> None:
    student = await login(client, "student1")
    r = await client.post(
        "/api/sessions",
        headers=bearer(student),
        json={"title": "Своё занятие", "group_id": await group_id()},
    )
    assert r.status_code == 403
    assert (await client.get("/api/sessions", headers=bearer(student))).status_code == 403


async def test_create_validates_settings(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    base = {"title": "Проверка", "group_id": await group_id()}
    bad = [
        {"mode": "bogus"},
        {"mode": "call_intake", "dialog_mode": "telepathy"},
        {"difficulty": 4},
        {"norm_seconds": 1},
        {"pass_threshold": 101},
        {"incident_groups": ["99"]},
        {"service_profile": ["nope"]},
        {"weights": {"speed": 10}},  # unknown component; sums are normalized since #35
        {"card_source": "tickets"},
        {"cards_per_student": -1},
        {"group_id": await group_id("Учебная-2")},
    ]
    for override in bad:
        r = await client.post("/api/sessions", headers=bearer(teacher), json={**base, **override})
        assert r.status_code in (403, 422), (override, r.text)
        assert r.json()["error"]["message"]


async def test_cloud_dialog_mode_needs_the_cloud_voice(client: AsyncClient, monkeypatch) -> None:
    from app.config import get_settings

    teacher = await login(client, "teacher1")
    body = {
        "title": "Облако",
        "group_id": await group_id(),
        "mode": "call_intake",
        "dialog_mode": "cloud",
    }
    monkeypatch.setattr(get_settings(), "cloud_voice_enabled", False)
    r = await client.post("/api/sessions", headers=bearer(teacher), json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "cloud_voice_disabled"
    models = await client.get("/api/models", headers=bearer(teacher))
    assert models.json()["cloud"] is False
    monkeypatch.setattr(get_settings(), "cloud_voice_enabled", True)
    r = await client.post("/api/sessions", headers=bearer(teacher), json=body)
    assert r.status_code == 201, r.text
    assert r.json()["dialog_mode"] == "cloud"
    assert (await client.get("/api/models", headers=bearer(teacher))).json()["cloud"] is True


async def test_call_intake_queue_ignores_difficulty(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    body = {
        "mode": "call_intake",
        "card_source": "scenarios",
        "scenario_ids": [],
        "incident_groups": [],
        "service_profile": [],
    }
    totals, queues = {}, {}
    for level in (1, 3):
        r = await client.post(
            "/api/sessions/preview", headers=bearer(teacher), json={**body, "difficulty": level}
        )
        assert r.status_code == 200, r.text
        totals[level] = r.json()["total"]
        queues[level] = [s["difficulty"] for s in r.json()["queue"]]
    assert totals[1] == totals[3] > 0
    assert queues[1] == sorted(queues[1])  # easy calls first


async def test_create_shows_queue_preview_and_own_sessions(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await create_session(client, teacher)
    assert created["status"] == "draft"
    assert created["group_title"] == GROUP_1
    assert len(created["members"]) == 6
    # Difficulty 3 with the управа profile: three seed scenarios, easy ones first.
    assert [s["difficulty"] for s in created["queue"]] == [2, 2, 3]
    assert all(s["service_code"] == "territorial_oiv" for s in created["queue"])
    assert created["scenario_ids"] == []

    listed = (await client.get("/api/sessions", headers=bearer(teacher))).json()
    assert listed[0]["id"] == created["id"]
    assert listed[0]["students"] == 6 and listed[0]["evaluated"] == 0

    other = await login(client, "teacher2")
    assert created["id"] not in {
        s["id"] for s in (await client.get("/api/sessions", headers=bearer(other))).json()
    }
    for method, path in (
        ("GET", ""),
        ("PATCH", ""),
        ("POST", "/start"),
        ("POST", "/finish"),
        ("GET", "/monitor"),
        ("GET", "/report"),
    ):
        r = await client.request(
            method, f"/api/sessions/{created['id']}{path}", headers=bearer(other), json={}
        )
        assert r.status_code == 403, (method, path, r.text)


async def test_patch_only_while_draft(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await create_session(client, teacher, difficulty=1)
    r = await client.patch(
        f"/api/sessions/{created['id']}",
        headers=bearer(teacher),
        json={"difficulty": 3, "norm_seconds": 45, "cards_per_student": 2},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["difficulty"] == 3 and data["norm_seconds"] == 45 and data["cards_per_student"] == 2
    assert len(data["queue"]) == 3

    await start(client, teacher, created["id"])
    r = await client.patch(
        f"/api/sessions/{created['id']}", headers=bearer(teacher), json={"difficulty": 1}
    )
    assert r.status_code == 409
    r = await client.patch(
        f"/api/sessions/{created['id']}",
        headers=bearer(teacher),
        json={"title": "Переименовано", "hints_enabled": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Переименовано" and r.json()["hints_enabled"] is False


async def test_start_fixes_queue_and_notifies_group(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await create_session(client, teacher)
    started = await start(client, teacher, created["id"])
    assert started["status"] == "running" and started["started_at"]
    assert len(started["scenario_ids"]) == 3
    assert [e.type for e in await events(created["id"])] == ["session.started"]
    assert (await events(created["id"]))[0].student_id is None
    # Starting again changes nothing.
    again = await start(client, teacher, created["id"])
    assert again["scenario_ids"] == started["scenario_ids"]
    assert len(await events(created["id"])) == 1

    student = await login(client, "student1")
    assignments = (await client.get("/api/me/assignments", headers=bearer(student))).json()
    assert assignments[0]["id"] == created["id"] and assignments[0]["status"] == "running"
    data = await journal(client, student, created["id"])
    assert data["total"] == 3
    types = [e.type for e in await events(created["id"])]
    assert types == ["session.started"] + ["attempt.issued"] * 3

    # A trainee of another group does not see it.
    outsider = await login(client, "student7")
    r = await client.get(f"/api/sessions/{created['id']}/journal", headers=bearer(outsider))
    assert r.status_code == 404


async def test_start_refuses_empty_queue(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await create_session(client, teacher, service_profile=["moslift"])
    assert created["queue"] == []
    r = await client.post(f"/api/sessions/{created['id']}/start", headers=bearer(teacher))
    assert r.status_code == 422
    assert "сценариев" in r.json()["error"]["message"]


async def test_cards_per_student_limits_issue(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await create_session(client, teacher, difficulty=1, cards_per_student=1)
    await start(client, teacher, created["id"])
    student = await login(client, "student1")
    attempt_id = (await journal(client, student, created["id"]))["items"][0]["attempt_id"]
    r = await client.post(f"/api/attempts/{attempt_id}/finish", headers=bearer(student))
    assert r.status_code == 200, r.text
    assert r.json()["issued"] == []
    assert (await journal(client, student, created["id"]))["total"] == 1


async def test_monitor_progress_and_finish_with_report(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await create_session(client, teacher)
    session_id = created["id"]
    await start(client, teacher, session_id)

    student = await login(client, "student1")
    items = (await journal(client, student, session_id))["items"]
    first = next(i for i in items if i["card_number"] == "38260311")
    await client.post(f"/api/attempts/{first['attempt_id']}/open", headers=bearer(student))
    r = await client.post(
        f"/api/attempts/{first['attempt_id']}/progress",
        headers=bearer(student),
        json={"stage": "editing_status"},
    )
    assert r.status_code == 204
    r = await set_status(client, student, first["attempt_id"], status="accepted")
    assert r.status_code == 200, r.text

    r = await client.get(f"/api/sessions/{session_id}/monitor", headers=bearer(teacher))
    assert r.status_code == 200, r.text
    monitor = r.json()
    assert monitor["status"] == "running" and monitor["cards_total"] == 3
    tile = next(s for s in monitor["students"] if s["login"] == "student1")
    assert len(tile["active"]) == 3 and tile["finished"] == 0
    card = next(c for c in tile["active"] if c["card_number"] == "38260311")
    assert card["state"] == "in_progress" and card["response_status"] == "accepted"
    assert card["primary_status_at"] and card["received_at"]
    idle = next(s for s in monitor["students"] if s["login"] == "student2")
    assert idle["active"] == [] and idle["average"] is None
    types = [e.type for e in await events(session_id)]
    assert "attempt.progress" in types and types.index("attempt.progress") < types.index(
        "attempt.status_changed"
    )

    # Second card: wrong decision (the reference says «Не принята»: duplicate).
    dubl = next(i for i in items if i["card_number"] == "38260340")
    r = await set_status(client, student, dubl["attempt_id"], status="accepted")
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/attempts/{dubl['attempt_id']}/finish", headers=bearer(student))
    assert r.status_code == 200 and r.json()["attempt"]["state"] == "evaluated"

    r = await client.post(f"/api/sessions/{session_id}/finish", headers=bearer(teacher))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "finished" and r.json()["finished_at"]
    finished = [e for e in await events(session_id) if e.type == "session.finished"]
    assert len(finished) == 1
    # One card was never opened → withdrawn; the accepted one is closed and scored.
    assert finished[0].payload["withdrawn"] == 1 and finished[0].payload["closed"] == 1
    assert (await journal(client, student, session_id))["total"] == 2
    r = await client.post(f"/api/sessions/{session_id}/finish", headers=bearer(teacher))
    assert (
        r.status_code == 200
        and len([e for e in await events(session_id) if e.type == "session.finished"]) == 1
    )

    r = await client.get(f"/api/sessions/{session_id}/report", headers=bearer(teacher))
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["status"] == "finished" and report["norm_seconds"] == 30
    assert report["summary"]["students"] == 6 and report["summary"]["participated"] == 1
    assert report["summary"]["evaluated"] == 2
    row = next(s for s in report["students"] if s["login"] == "student1")
    assert row["attempts_total"] == 2 and row["evaluated"] == 2
    assert row["average"] is not None and 0 <= row["average"] <= 100
    assert row["average_seconds"] is not None and row["average_deviation"] is not None
    assert row["wrong_decisions"] == 1
    wrong = next(a for a in row["attempts"] if a["decision_correct"] is False)
    assert wrong["decision_expected"] == "reject" and wrong["decision_actual"] == "accept"
    # The report lists the trainee's own steps with the time from issue; system statuses
    # («Добавлена», «Получена службой») are not the trainee's actions.
    actions = wrong["actions"]
    assert [a["kind"] for a in actions] == ["status"] * len(actions) and actions
    assert actions[0]["title"] == "Принята" and actions[0]["seconds"] >= 0
    assert all(a["title"] not in ("Добавлена", "Получена службой") for a in actions)
    assert wrong["total"] is not None and wrong["seconds"] is not None
    assert isinstance(wrong["errors"], list)
    listed = (await client.get("/api/sessions", headers=bearer(teacher))).json()
    mine = next(s for s in listed if s["id"] == session_id)
    assert mine["evaluated"] == 2 and mine["average"] == row["average"]

    # Finished session: the trainee cannot act, no new cards.
    r = await set_status(client, student, first["attempt_id"], status="response_started")
    assert r.status_code == 409


async def test_unfinished_threshold_marks_card(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    teacher = await login(client, "teacher1")
    created = await create_session(client, teacher, difficulty=1, unfinished_seconds=60)
    await start(client, teacher, created["id"])
    student = await login(client, "student1")
    attempt_id = (await journal(client, student, created["id"]))["items"][0]["attempt_id"]
    r = await set_status(client, student, attempt_id, status="accepted")
    assert r.status_code == 200
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE attempts SET primary_status_at = :t WHERE id = :id"),
            {"t": utcnow() - timedelta(seconds=61), "id": uuid.UUID(attempt_id)},
        )
    async with SessionLocal() as session:
        swept = await sweep_not_notified(session)
        await session.commit()
    assert [e.payload["card_status"] for e in swept if e.payload["attempt_id"] == attempt_id] == [
        "not_finished"
    ]
    item = (await journal(client, student, created["id"]))["items"][0]
    assert item["card_status_title"] == "Не завершено" and item["card_status_alert"] is True
    # «Работы завершены» closes it normally.
    r = await set_status(client, student, attempt_id, status="response_started", order_number="1")
    assert r.status_code == 200, r.text
    r = await set_status(client, student, attempt_id, status="arrived")
    r = await set_status(client, student, attempt_id, status="works_started")
    r = await set_status(client, student, attempt_id, status="works_done", comment="Готово")
    assert r.status_code == 200, r.text
    assert r.json()["attempt"]["card_status"] == "finished"


async def test_session_norm_overrides_scenario_norm(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await create_session(client, teacher, difficulty=1, norm_seconds=120)
    await start(client, teacher, created["id"])
    student = await login(client, "student1")
    attempt_id = (await journal(client, student, created["id"]))["items"][0]["attempt_id"]
    await set_status(client, student, attempt_id, status="accepted")
    r = await client.post(f"/api/attempts/{attempt_id}/finish", headers=bearer(student))
    time_component = r.json()["attempt"]["evaluation"]["components"]["time"]
    assert time_component["items"][0]["norm_seconds"] == 120


async def test_seed_users_untouched(client: AsyncClient) -> None:
    async with SessionLocal() as session:
        logins = set(await session.scalars(select(User.login)))
    assert {"teacher1", "teacher2", "student1", "student12"} <= logins


async def test_queue_preview_counts_cards_before_the_lesson_exists(client: AsyncClient) -> None:
    """docs/BUGS.md 6/7: the lesson form asks how many cards match the filters."""
    teacher = await login(client, "teacher1")
    r = await client.post(
        "/api/sessions/preview",
        headers=bearer(teacher),
        json={"mode": "card_response", "difficulty": 1, "service_profile": ["territorial_oiv"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == len(body["queue"]) > 0
    assert all(s["service_code"] == "territorial_oiv" for s in body["queue"])
    # A service without approved cards: an honest zero, not a 500.
    r = await client.post(
        "/api/sessions/preview",
        headers=bearer(teacher),
        json={"mode": "card_response", "difficulty": 1, "service_profile": ["no-such-service"]},
    )
    assert r.status_code == 200
    assert r.json() == {"total": 0, "harder_only": False, "queue": []}
    # Call intake ignores the service profile and reports cards harder than asked.
    r = await client.post(
        "/api/sessions/preview",
        headers=bearer(teacher),
        json={"mode": "call_intake", "difficulty": 1, "service_profile": ["no-such-service"]},
    )
    assert r.status_code == 200
    assert r.json()["total"] > 0
    r = await client.post(
        "/api/sessions/preview", headers=bearer(teacher), json={"mode": "nonsense"}
    )
    assert r.status_code == 422
    student = await login(client, "student1")
    r = await client.post("/api/sessions/preview", headers=bearer(student), json={})
    assert r.status_code == 403


# ---------------------------------------------------------------- generation for a lesson


async def test_generate_for_session_drafts_scenarios_under_its_groups(client: AsyncClient) -> None:
    """ТЗ «Настройка учебной среды»: the teacher picks the categories, the system drafts the
    scenarios, the teacher approves — only then they enter the lesson queue."""
    teacher = await login(client, "teacher1")
    lesson = await create_session(
        client,
        teacher,
        mode="call_intake",
        incident_groups=["24"],
        difficulty=1,
        service_profile=[],
    )
    assert lesson["queue"] == []  # no seed scenario is about drones

    r = await client.post(
        f"/api/sessions/{lesson['id']}/generate", headers=bearer(teacher), json={"count": 2}
    )
    assert r.status_code == 202, r.text
    accepted = r.json()
    assert accepted["count"] == 2 and accepted["groups"] == ["24"]
    job = await wait_job(client, teacher, accepted["job_id"])
    assert job["status"] == "done", job
    drafted = job["result"]["scenarios"]
    assert len(drafted) == 2
    assert all(s["kind"] == "call_intake" and s["incident_group"] == "24" for s in drafted)
    for item in drafted:
        r = await client.get(f"/api/scenarios/{item['scenario_id']}", headers=bearer(teacher))
        assert r.status_code == 200, r.text
        scenario = r.json()
        assert scenario["status"] == "review" and scenario["source"] == "generated"
        assert scenario["incident_type_code"].startswith("24.")
        assert scenario["difficulty"] == 1
        assert scenario["generation"]["phrase"]

    # Drafts do not enter the queue; an approved one does, by the lesson's own filters.
    r = await client.get(f"/api/sessions/{lesson['id']}", headers=bearer(teacher))
    assert r.json()["queue"] == []
    first = drafted[0]["scenario_id"]
    r = await client.post(
        f"/api/scenarios/{first}/approve",
        headers=bearer(teacher),
        json={"reference": True, "replies": True, "confirm_grammar": True},
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/sessions/{lesson['id']}", headers=bearer(teacher))
    assert [q["id"] for q in r.json()["queue"]] == [first]

    # Default count: one per selected group; a colleague's lesson is not theirs to fill.
    r = await client.post(
        f"/api/sessions/{lesson['id']}/generate", headers=bearer(teacher), json={}
    )
    assert r.status_code == 202 and r.json()["count"] == 1
    await wait_job(client, teacher, r.json()["job_id"])
    other = await login(client, "teacher2")
    r = await client.post(f"/api/sessions/{lesson['id']}/generate", headers=bearer(other), json={})
    assert r.status_code == 403

    # Once started the queue is fixed, so generation is refused.
    await start(client, teacher, lesson["id"])
    r = await client.post(
        f"/api/sessions/{lesson['id']}/generate", headers=bearer(teacher), json={}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "session_started"


async def test_generate_for_session_without_groups_covers_several(client: AsyncClient) -> None:
    """No group selected: three drafts over the first groups of the classifier, in the
    lesson's mode and within its services."""
    teacher = await login(client, "teacher1")
    lesson = await create_session(client, teacher, difficulty=2)
    r = await client.post(
        f"/api/sessions/{lesson['id']}/generate", headers=bearer(teacher), json={}
    )
    assert r.status_code == 202, r.text
    assert r.json()["count"] == 3 and len(r.json()["groups"]) > 3
    job = await wait_job(client, teacher, r.json()["job_id"])
    assert job["status"] == "done", job
    kinds = {s["kind"] for s in job["result"]["scenarios"]}
    groups = [s["incident_group"] for s in job["result"]["scenarios"]]
    assert kinds == {"card_response"} and len(set(groups)) == 3

    # Issue #35: drafts of «Реагирование» may carry planted mistakes of the 112 operator
    # (their share follows the difficulty). Whatever was planted, the draft stays valid and
    # approvable: an inconsistent planted error would show up in «problems».
    for item in job["result"]["scenarios"]:
        r = await client.get(f"/api/scenarios/{item['scenario_id']}", headers=bearer(teacher))
        assert r.status_code == 200, r.text
        scenario = r.json()
        assert scenario["problems"] == [], scenario["problems"]
        for error in scenario["body"].get("injected_errors", []):
            assert error["field"] and error["wrong_value"] != error["correct_value"]
