"""«Задания формирует ИИ» (issue #265): each trainee gets cards written for him one at a time,
the next one after the previous is scored and by its mistakes; difficulty 3 gives an emergency
with two mistakes, still one card at a time; the cards stay out of the library and other
lessons. The tests run on the template provider (no model in the test environment)."""

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update

from app.db import SessionLocal
from app.models import (
    AI_CARD_GENERATING,
    AI_CARD_READY,
    AiCard,
    Attempt,
    Scenario,
    TrainingSession,
    User,
)
from app.training import ai_cards
from app.training.service import STREAM_CARDS, utcnow
from app.training.stream import issue_stream_cards, trainee_schedule
from tests.api.conftest import DATA_DIR
from tests.api.test_sessions import create_session, journal, set_status, start
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)


@pytest.fixture(autouse=True)
async def finish_writing():
    yield
    await ai_cards.wait_all(max_seconds=30)
    # The scenarios outlive the per-test reset: other tests take every scenario of a service.
    async with SessionLocal() as session:
        await session.execute(
            delete(TrainingSession).where(TrainingSession.card_source == ai_cards.CARD_SOURCE_AI)
        )
        await session.execute(
            delete(Scenario).where(Scenario.source == ai_cards.SCENARIO_SOURCE_AI)
        )
        await session.commit()


async def ai_lesson(client: AsyncClient, teacher: dict, difficulty: int) -> str:
    created = await create_session(
        client,
        teacher,
        card_source="ai",
        difficulty=difficulty,
        service_profile=[],
        cards_per_student=5,  # the form's own number is replaced by the difficulty's
    )
    assert created["cards_per_student"] == {1: 1, 2: 2, 3: 3}[difficulty]
    await start(client, teacher, created["id"])
    await ai_cards.wait_all()
    return created["id"]


async def service_of(login_name: str) -> str | None:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.login == login_name))
        assert user is not None
        return user.service_code


async def scenario_of(attempt_id: str) -> Scenario:
    async with SessionLocal() as session:
        attempt = await session.get(Attempt, uuid.UUID(attempt_id))
        assert attempt is not None
        scenario = await session.get(Scenario, attempt.scenario_id)
        assert scenario is not None
        return scenario


async def body_of(scenario: Scenario) -> dict:
    from app.training.service import load_scenario_card

    async with SessionLocal() as session:
        card = await load_scenario_card(session, scenario.id, scenario.current_version)
        return card.body


async def test_each_trainee_gets_own_card_written_at_start(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    lesson = await ai_lesson(client, teacher, difficulty=2)
    first = await login(client, "student1")
    second = await login(client, "student2")
    mine = (await journal(client, first, lesson))["items"]
    theirs = (await journal(client, second, lesson))["items"]
    assert len(mine) == 1 and len(theirs) == 1

    a, b = await scenario_of(mine[0]["attempt_id"]), await scenario_of(theirs[0]["attempt_id"])
    assert a.source == b.source == ai_cards.SCENARIO_SOURCE_AI
    assert a.incident_type_code != b.incident_type_code
    # Each card is for the trainee's own service: its own tab, and a type it is notified of.
    assert a.service_code == await service_of("student1")
    assert b.service_code == await service_of("student2")
    own = next(
        s
        for s in (
            await client.get(f"/api/attempts/{mine[0]['attempt_id']}", headers=bearer(first))
        ).json()["card"]["services"]
        if s["is_own"]
    )
    assert own["code"] == a.service_code
    body = await body_of(a)
    assert body["difficulty"] == 2
    assert len(body["injected_errors"]) == 1
    assert body["generation"]["reasons"]

    # Written for the lesson: not in the library and not in another lesson's queue.
    r = await client.get("/api/scenarios", headers=bearer(teacher))
    assert all(s["id"] not in {str(a.id), str(b.id)} for s in r.json()["items"])
    other = await create_session(client, teacher, difficulty=2, service_profile=[])
    started = await start(client, teacher, other["id"])
    assert not {str(a.id), str(b.id)} & set(started["scenario_ids"])


async def test_next_card_is_written_after_the_scored_one_by_its_mistakes(
    client: AsyncClient,
) -> None:
    teacher = await login(client, "teacher1")
    lesson = await ai_lesson(client, teacher, difficulty=2)
    student = await login(client, "student3")
    first = (await journal(client, student, lesson))["items"][0]["attempt_id"]
    first_body = await body_of(await scenario_of(first))
    missed_field = first_body["injected_errors"][0]["field"]

    # Accepted without flagging the operator's mistake and closed: not passed, mistake missed.
    await client.post(f"/api/attempts/{first}/open", headers=bearer(student))
    assert (await set_status(client, student, first, status="accepted")).status_code == 200
    r = await client.post(f"/api/attempts/{first}/finish", headers=bearer(student))
    assert r.status_code == 200, r.text
    # The review: advice from the mistakes, and why this card was written for the trainee.
    evaluation = r.json()["attempt"]["evaluation"]
    assert 1 <= len(evaluation["advice"]) <= 5
    assert evaluation["reasons"]
    assert evaluation["by_template"] is (first_body["generation"]["method"] == "template")
    await ai_cards.wait_all()  # written while the trainee reads the result

    items = (await journal(client, student, lesson))["items"]
    second = next(i for i in items if i["attempt_id"] != first)
    second_scenario = await scenario_of(second["attempt_id"])
    second_body = await body_of(second_scenario)
    assert second_body["injected_errors"][0]["field"] == missed_field
    first_group = (await scenario_of(first)).incident_type_code.split(".")[0]
    assert second_scenario.incident_type_code.split(".")[0] == first_group
    async with SessionLocal() as session:
        me = await session.scalar(select(User).where(User.login == "student3"))
        rows = list(
            await session.scalars(
                select(AiCard)
                .where(AiCard.session_id == uuid.UUID(lesson), AiCard.student_id == me.id)
                .order_by(AiCard.seq)
            )
        )
    assert [r.seq for r in rows] == [1, 2]
    assert all(r.status == AI_CARD_READY for r in rows)
    assert any("Пропущена ошибка" in reason for reason in rows[1].reasons)

    # After the lesson: advice per trainee for him and for the teacher.
    r = await client.get(f"/api/sessions/{lesson}/report", headers=bearer(teacher))
    row = next(s for s in r.json()["students"] if s["login"] == "student3")
    assert row["advice"]
    r = await client.get("/api/me/progress", headers=bearer(student))
    mine = next(s for s in r.json()["sessions"] if s["session_id"] == lesson)
    assert mine["advice"] == row["advice"]


async def test_difficulty_3_is_a_stream_of_written_cards_with_two_mistakes(
    client: AsyncClient,
) -> None:
    """At difficulty 3 the written cards come as the stream of an ordinary lesson: the first
    at once, then one every 40–50 s, three in all. They are written two ahead, so a card is
    there when its time comes; each is an emergency with two of the operator's mistakes."""
    teacher = await login(client, "teacher1")
    lesson = await ai_lesson(client, teacher, difficulty=3)
    async with SessionLocal() as session:
        ts = await session.get(TrainingSession, uuid.UUID(lesson))
        me = await session.scalar(select(User).where(User.login == "student4"))
        assert ts is not None and me is not None and ts.started_at is not None
        times = trainee_schedule(ts, me.id)
        begin = ts.started_at

    async def written() -> list[int]:
        async with SessionLocal() as session:
            rows = await session.scalars(
                select(AiCard.seq).where(
                    AiCard.session_id == uuid.UUID(lesson),
                    AiCard.student_id == me.id,
                    AiCard.status == AI_CARD_READY,
                )
            )
            return sorted(rows)

    async def sweep(seconds: float) -> None:
        # A hundredth over: a timedelta drops what is below a microsecond.
        async with SessionLocal() as session:
            await issue_stream_cards(session, begin + timedelta(seconds=seconds + 0.01))
            await session.commit()
        await ai_cards.wait_all()

    student = await login(client, "student4")
    # The journal adds nothing in a stream; two cards are written as the lesson starts.
    assert (await journal(client, student, lesson))["items"] == []
    assert await written() == [1, 2]
    await sweep(0)
    assert len((await journal(client, student, lesson))["items"]) == 1
    assert await written() == [1, 2, 3]  # card 3 started as card 1 came
    await sweep(times[1])
    assert len((await journal(client, student, lesson))["items"]) == 2
    assert await written() == [1, 2, 3]  # no fourth card is written: the stream has three
    await sweep(3600)
    items = (await journal(client, student, lesson))["items"]
    assert len(items) == STREAM_CARDS == 3
    assert await written() == [1, 2, 3]

    body = await body_of(await scenario_of(items[0]["attempt_id"]))
    assert len(body["injected_errors"]) == 2
    assert body["service"] == await service_of("student4")
    assert body["card"]["flags"]["emergency"] is True
    card = await client.get(f"/api/attempts/{items[0]['attempt_id']}", headers=bearer(student))
    assert card.json()["card"]["emergency"] is True  # «ЧС» in the card header


async def test_a_card_lost_in_writing_is_written_by_the_template(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    lesson = await ai_lesson(client, teacher, difficulty=1)
    async with SessionLocal() as session:
        me = await session.scalar(select(User).where(User.login == "student5"))
        # As if the server restarted while the first card was being written.
        await session.execute(
            update(AiCard)
            .where(AiCard.session_id == uuid.UUID(lesson), AiCard.student_id == me.id)
            .values(
                status=AI_CARD_GENERATING,
                scenario_id=None,
                created_at=utcnow() - timedelta(seconds=ai_cards.STALE_SECONDS + 5),
            )
        )
        await session.commit()
    student = await login(client, "student5")
    items = (await journal(client, student, lesson))["items"]
    assert len(items) == 1
    body = await body_of(await scenario_of(items[0]["attempt_id"]))
    assert body["generation"]["method"] == "template"
    assert not body.get("injected_errors")  # difficulty 1


async def test_the_journal_says_the_card_is_being_written(client: AsyncClient) -> None:
    """Stand 20.10.2026: the local model writes a card for about a minute, and the empty
    journal looked broken. While the trainee's card is being written and he has none to work
    on, the journal and the teacher's monitor say how long he waits; the local model gets two
    minutes before the template takes over."""
    assert not ai_cards.cloud_model()  # no cloud key in the test environment
    assert ai_cards.model_seconds() == ai_cards.LOCAL_MODEL_SECONDS == 120
    assert ai_cards.STALE_SECONDS > ai_cards.LOCAL_MODEL_SECONDS
    teacher = await login(client, "teacher1")
    lesson = await ai_lesson(client, teacher, difficulty=1)
    async with SessionLocal() as session:
        me = await session.scalar(select(User).where(User.login == "student6"))
        await session.execute(
            update(AiCard)
            .where(AiCard.session_id == uuid.UUID(lesson), AiCard.student_id == me.id)
            .values(status=AI_CARD_GENERATING)
        )
        await session.commit()
    student = await login(client, "student6")
    data = await journal(client, student, lesson)
    assert data["items"] == [] and data["ai_card_wait"] == ai_cards.WAIT_LOCAL == "до двух минут"
    r = await client.get(f"/api/sessions/{lesson}/monitor", headers=bearer(teacher))
    tile = next(s for s in r.json()["students"] if s["login"] == "student6")
    assert tile["ai_card_wait"] == ai_cards.WAIT_LOCAL

    async with SessionLocal() as session:
        await session.execute(
            update(AiCard)
            .where(AiCard.session_id == uuid.UUID(lesson), AiCard.student_id == me.id)
            .values(status=AI_CARD_READY)
        )
        await session.commit()
    data = await journal(client, student, lesson)
    assert len(data["items"]) == 1 and data["ai_card_wait"] is None


async def test_the_lesson_chooses_whose_dds_the_trainees_are(client: AsyncClient) -> None:
    """Decision of 17.10.2026: the teacher picks the service in the lesson form; two services
    go round the group, whatever service the admin gave the trainees; the journal's header
    names it."""
    teacher = await login(client, "teacher1")
    groups = (await client.get("/api/ai-card-groups", headers=bearer(teacher))).json()
    assert groups["by_service"]["gkh"]["1"] > 0  # ЖКХ takes part in fires
    assert "13" not in groups["by_service"]["gkh"]  # but never in «Запах газа»

    created = await create_session(
        client, teacher, card_source="ai", difficulty=1, service_profile=["gkh", "mosvodokanal"]
    )
    await start(client, teacher, created["id"])
    await ai_cards.wait_all()
    services = set()
    for name in ("student1", "student2"):
        trainee = await login(client, name)
        page = await journal(client, trainee, created["id"])
        card = await scenario_of(page["items"][0]["attempt_id"])
        assert card.service_code == page["session"]["service"]["code"]
        services.add(card.service_code)
    assert services == {"gkh", "mosvodokanal"}
