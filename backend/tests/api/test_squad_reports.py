"""The squad's reports to the dispatcher without telephony (customer, 21.09.2026): the sweep
delivers them on the card's timeline as report calls, the dispatcher talks to the squad
leader and reflects the report with a status; an ignored report is closed and the timeline
goes on; the evaluation tells a status set after the report from one set before it.

Time is moved by rewinding the stored timestamps of the attempt (as the «Не оповещено» test
does), so the order of events stays as it would be in a real lesson.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.orm.attributes import flag_modified

from app.db import SessionLocal
from app.events import publish_events
from app.models import MODE_CARD_RESPONSE, Attempt
from app.training import reports
from tests.api.conftest import DATA_DIR
from tests.api.test_dialog import make_attempt
from tests.api.test_service_calls import end, say, start, status
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)

CARD = "card_moek_1_net_otopleniya"  # accepted card: a call to the officer and four reports


def _shift(value: str | None, delta: timedelta) -> str | None:
    return (datetime.fromisoformat(value) - delta).isoformat() if value else None


async def rewind(attempt_id: uuid.UUID, seconds: float) -> None:
    """Moves everything that happened on the card ``seconds`` into the past."""
    delta = timedelta(seconds=seconds)
    async with SessionLocal() as session:
        attempt = await session.get(Attempt, attempt_id)
        for name in ("issued_at", "received_at", "primary_status_at", "answered_at"):
            value = getattr(attempt, name, None)
            if value is not None:
                setattr(attempt, name, value - delta)
        attempt.status_log = [{**e, "at": _shift(e["at"], delta)} for e in attempt.status_log]
        flag_modified(attempt, "status_log")
        attempt.service_calls = [
            {
                **c,
                "started_at": _shift(c.get("started_at"), delta),
                "answered_at": _shift(c.get("answered_at"), delta),
                "ended_at": _shift(c.get("ended_at"), delta),
                "dialog": [{**t, "at": _shift(t.get("at"), delta)} for t in c.get("dialog") or []],
            }
            for c in attempt.service_calls or []
        ]
        flag_modified(attempt, "service_calls")
        await session.commit()


async def sweep() -> list[dict]:
    """Runs the report sweep now; returns the event payloads with their types."""
    async with SessionLocal() as session:
        events, to_dial = await reports.sweep_reports(session)
        await session.commit()
    assert to_dial == []  # no telephony in tests
    await publish_events(events)
    return [{"type": e.type, **e.payload} for e in events]


def started(events: list[dict]) -> list[str]:
    return [e["report_status"] for e in events if e["type"] == "service_call.started"]


async def report_calls(attempt_id: uuid.UUID) -> list[dict]:
    async with SessionLocal() as session:
        attempt = await session.get(Attempt, attempt_id)
        return [c for c in attempt.service_calls if c.get("kind") == "report"]


async def get_attempt(client: AsyncClient, token: dict, attempt_id: uuid.UUID) -> dict:
    r = await client.get(f"/api/attempts/{attempt_id}", headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()


async def test_reports_arrive_on_the_timeline_and_the_dispatcher_reflects_them(
    client: AsyncClient,
) -> None:
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    token = await login(client, "student1")

    # Nothing before «Принята», nothing right after it.
    assert await sweep() == []
    r = await status(client, token, attempt_id, status="accepted")
    assert r.status_code == 200, r.text
    assert await sweep() == []

    # The dispatcher calls the officer (issue #36) and asks about the squad: it is being
    # sent. The squad leaves after this call, so the first report counts from its end.
    r = await start(client, token, attempt_id)
    call_id = r.json()["call"]["id"]
    r = await say(client, token, attempt_id, call_id, "Как обстановка, бригада выехала?")
    assert r.json()["call"]["turns"][-1]["topics"] == ["progress"]
    assert "собираем" in r.json()["call"]["turns"][-1]["text"]
    await say(client, token, attempt_id, call_id, "Улица Молостовых, дом 10, корпус 1, наряд 4127")
    await end(client, token, attempt_id, call_id)
    await rewind(attempt_id, 20)
    assert await sweep() == []

    await rewind(attempt_id, 11)  # 31 s after the call ended, the delay is 30
    events = await sweep()
    assert started(events) == ["response_started"]
    first = next(e for e in events if e["type"] == "service_call.started")
    assert first["kind"] == "report"
    assert [e["type"] for e in events].count("service_call.answered") == 1  # spoke at once

    data = await get_attempt(client, token, attempt_id)
    report = next(c for c in data["service_calls"] if c["kind"] == "report")
    assert report["report_status"] == "response_started"
    assert report["report_status_title"] == "Начало реагирования"
    assert report["answered"] is True and report["ended_at"] is None
    assert report["turns"][0]["role"] == "caller"
    assert "Выехали" in report["turns"][0]["text"]
    assert report["facts_required"] == []

    # Delivered once: the same sweep again is silent while the report is open.
    assert await sweep() == []

    # The dispatcher talks to the squad leader: the report is repeated on request, facts are
    # not counted on a report; then hangs up and reflects the report.
    r = await say(client, token, attempt_id, report["id"], "Повторите, не расслышал")
    assert r.status_code == 200, r.text
    assert "Выехали" in r.json()["call"]["turns"][-1]["text"]
    assert r.json()["call"]["facts_passed"] == []
    r = await end(client, token, attempt_id, report["id"])
    assert r.json()["call"]["ended_at"] is not None
    r = await status(
        client, token, attempt_id, status="response_started", order_number="4127", comment="Выехали"
    )
    assert r.status_code == 200, r.text

    # The next report counts from the end of the previous one (45 s), not from «Принята».
    await rewind(attempt_id, 44)
    assert await sweep() == []
    await rewind(attempt_id, 2)
    assert started(await sweep()) == ["arrived"]
    arrived = (await report_calls(attempt_id))[-1]
    await end(client, token, attempt_id, arrived["id"])
    r = await status(client, token, attempt_id, status="arrived")
    assert r.status_code == 200, r.text

    # A report nobody talks to is closed after the timeout and the timeline goes on.
    await rewind(attempt_id, 31)
    assert started(await sweep()) == ["works_started"]
    await rewind(attempt_id, reports.REPORT_CALL_TIMEOUT_SECONDS - 1)
    assert await sweep() == []
    await rewind(attempt_id, 1)
    events = await sweep()
    assert [e["end_reason"] for e in events if e["type"] == "service_call.ended"] == ["not_taken"]
    ignored = (await report_calls(attempt_id))[-1]
    assert ignored["report_status"] == "works_started" and ignored["end_reason"] == "not_taken"
    await rewind(attempt_id, 61)
    assert started(await sweep()) == ["works_done"]
    done = (await report_calls(attempt_id))[-1]
    await end(client, token, attempt_id, done["id"])

    # The dispatcher never reflected «Проведение работ» and closes the card after the last
    # report: the ignored report is an error, the reflected ones are fine, and the reports
    # are not the dispatcher's calls (the officer's call scores on its own).
    r = await status(
        client, token, attempt_id, status="works_done", comment="Отопление восстановлено"
    )
    assert r.status_code == 200, r.text
    evaluation = r.json()["attempt"]["evaluation"]
    errors = {e["code"]: e["explanation"] for e in evaluation["errors"]}
    assert "status_before_report" not in errors
    assert "report_not_reflected" in errors
    assert "«Проведение работ»" in errors["report_not_reflected"]
    component = evaluation["components"]["service_call"]
    assert component["items"][0]["called"] is True
    assert len([c for c in r.json()["attempt"]["service_calls"] if c["kind"] == "report"]) == 4
    # A closed card gets no more reports.
    assert await sweep() == []


async def test_statuses_clicked_through_before_any_report_are_an_error(
    client: AsyncClient,
) -> None:
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    token = await login(client, "student1")
    for body in (
        {"status": "accepted"},
        {"status": "response_started", "order_number": "1", "comment": "Выехали"},
        {"status": "arrived"},
        {"status": "works_started", "comment": "Работы"},
        {"status": "works_done", "comment": "Готово"},
    ):
        r = await status(client, token, attempt_id, **body)
        assert r.status_code == 200, r.text
    errors = {e["code"]: e for e in r.json()["attempt"]["evaluation"]["errors"]}
    assert "status_before_report" in errors
    assert "«Начало реагирования»" in errors["status_before_report"]["explanation"]
    assert "report_not_reflected" not in errors
