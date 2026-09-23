"""The squad's timeline without a database: which report is due when, and when an ignored
report call is closed (``app.training.reports``)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.training import reports
from app.training.service import BY_DISPATCHER, BY_SYSTEM
from tests.domain.evaluation.helpers import card_scenario

T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
NAME = "card_gkh_1_tech_v_podezde"


def iso(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def now(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def attempt(status: str = "accepted", *, log=None, calls=None) -> SimpleNamespace:
    return SimpleNamespace(
        response_status=status,
        status_log=log
        if log is not None
        else [
            {"status": "added", "at": iso(0), "by": BY_SYSTEM},
            {"status": "received", "at": iso(3), "by": BY_SYSTEM},
            {"status": "accepted", "at": iso(10), "by": BY_DISPATCHER},
        ],
        service_calls=calls or [],
    )


def report_call(status: str, start: float, end: float | None, *, spoken: bool = True) -> dict:
    dialog = [{"role": "caller", "text": "доклад", "topics": ["report"], "at": iso(start)}]
    if spoken:
        dialog.append({"role": "operator", "text": "принял", "topics": [], "at": iso(start + 5)})
    return {
        "id": f"r-{status}",
        "service": "gkh",
        "kind": "report",
        "report_status": status,
        "started_at": iso(start),
        "answered": True,
        "ended_at": iso(end) if end is not None else None,
        "dialog": dialog,
    }


# What the dispatcher says to the officer of «card_gkh_1_tech_v_podezde»: the squad only
# leaves once it has been told where to go.
ADDRESS_PASSED = "улица Свободы, дом 42, течёт стояк, пострадавших нет, код подъезда 5В"


def officer_call(start: float, end: float, said: str = ADDRESS_PASSED) -> dict:
    dialog = [
        {"role": "caller", "text": "Дежурный слушает.", "topics": ["greeting"], "at": iso(start)}
    ]
    if said:
        dialog.append({"role": "operator", "text": said, "topics": [], "at": iso(start + 1)})
    return {
        "id": "o-1",
        "service": "gkh",
        "kind": "outgoing",
        "started_at": iso(start),
        "answered": True,
        "ended_at": iso(end),
        "dialog": dialog,
    }


SCENARIO = card_scenario(NAME)
FIRST = SCENARIO.reference.reports[0]  # response_started, 30 s after «Принята»
SECOND = SCENARIO.reference.reports[1]  # arrived, 45 s after the first report ended


def test_a_squad_that_was_never_called_does_not_leave() -> None:
    """«Принята» alone dispatches nobody when the card requires a call to the officer: the
    squad has no address to go to (issue #127)."""
    assert reports.next_report(SCENARIO, attempt(), now(10 + FIRST.after_seconds)) is None
    assert reports.next_report(SCENARIO, attempt(), now(5000)) is None


def test_first_report_comes_after_the_delay_from_the_call_to_the_officer() -> None:
    a = attempt(calls=[officer_call(20, 80)])
    assert reports.next_report(SCENARIO, a, now(80 + FIRST.after_seconds - 1)) is None
    due = reports.next_report(SCENARIO, a, now(80 + FIRST.after_seconds))
    assert due is not None and due.status == "response_started"


def test_nothing_before_accepted_or_after_a_rejection() -> None:
    not_yet = attempt("received", log=[{"status": "added", "at": iso(0), "by": BY_SYSTEM}])
    assert reports.next_report(SCENARIO, not_yet, now(500)) is None
    assert reports.next_report(SCENARIO, attempt("rejected"), now(500)) is None
    assert reports.next_report(SCENARIO, attempt("works_done"), now(500)) is None


def test_a_silent_call_to_the_officer_dispatches_nobody() -> None:
    """Reaching the officer and hanging up without a word leaves the squad without a task."""
    silent = attempt(calls=[officer_call(20, 22, said="")])
    assert reports.next_report(SCENARIO, silent, now(5000)) is None
    greeted = attempt(calls=[officer_call(20, 24, said="Алло, добрый день.")])
    assert reports.next_report(SCENARIO, greeted, now(5000)) is None


def test_next_report_counts_from_the_end_of_the_previous_one() -> None:
    a = attempt("response_started", calls=[report_call("response_started", 40, 60)])
    assert reports.next_report(SCENARIO, a, now(60 + SECOND.after_seconds - 1)) is None
    due = reports.next_report(SCENARIO, a, now(60 + SECOND.after_seconds))
    assert due is not None and due.status == "arrived"


def test_no_report_while_a_call_is_open() -> None:
    a = attempt(calls=[{**officer_call(20, 80), "ended_at": None}])
    assert reports.next_report(SCENARIO, a, now(1000)) is None
    b = attempt(calls=[report_call("response_started", 40, None)])
    assert reports.next_report(SCENARIO, b, now(1000)) is None


def test_reports_are_delivered_once_and_in_order() -> None:
    a = attempt(
        "accepted",  # the trainee did not set the status, the squad still goes on
        calls=[report_call("response_started", 40, 60), report_call("arrived", 110, 130)],
    )
    due = reports.next_report(SCENARIO, a, now(1000))
    assert due is not None and due.status == "works_started"
    done = attempt(
        calls=[
            report_call(r.status, 40 + i * 100, 60 + i * 100)
            for i, r in enumerate(SCENARIO.reference.reports)
        ]
    )
    assert reports.next_report(SCENARIO, done, now(10_000)) is None


def test_ignored_report_is_stale_after_the_timeout_and_the_timeline_goes_on() -> None:
    ignored = report_call("response_started", 40, None, spoken=False)
    a = attempt(calls=[ignored])
    before = now(40 + reports.REPORT_CALL_TIMEOUT_SECONDS - 1)
    after = now(40 + reports.REPORT_CALL_TIMEOUT_SECONDS)
    assert reports.stale_report(a, before) is None
    assert reports.stale_report(a, after) is ignored
    # A report the dispatcher is talking to is never stale.
    spoken = attempt(calls=[report_call("response_started", 40, None)])
    assert reports.stale_report(spoken, now(10_000)) is None
    # Once the stale call is closed (ended_at set), the next delay counts from its end.
    closed = attempt(calls=[{**ignored, "ended_at": iso(130)}])
    assert reports.next_report(SCENARIO, closed, now(130 + SECOND.after_seconds - 1)) is None
    assert reports.next_report(SCENARIO, closed, now(130 + SECOND.after_seconds)) is not None


def test_card_without_reports_never_rings() -> None:
    plain = card_scenario("card_2-1_zadymlenie_musoroprovoda")
    assert plain.reference.reports == []
    assert reports.next_report(plain, attempt(), now(10_000)) is None
