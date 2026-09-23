"""Time on an attempt without a database: the span differs by mode, and the trainee's pace
advice follows the mode they are slow in (``app.training.service.attempt_seconds``,
``app.training.progress``)."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.models import MODE_CALL_INTAKE, MODE_CARD_RESPONSE
from app.training import progress
from app.training.report import report_attempt
from app.training.service import attempt_seconds

T0 = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def card(*, primary: float | None = 24, submitted: float | None = 1500) -> SimpleNamespace:
    return SimpleNamespace(
        mode=MODE_CARD_RESPONSE,
        issued_at=T0,
        answered_at=None,
        primary_status_at=None if primary is None else at(primary),
        submitted_at=None if submitted is None else at(submitted),
    )


def call(*, answered: float | None = 3, submitted: float | None = 75) -> SimpleNamespace:
    return SimpleNamespace(
        mode=MODE_CALL_INTAKE,
        issued_at=T0,
        answered_at=None if answered is None else at(answered),
        primary_status_at=None,
        submitted_at=None if submitted is None else at(submitted),
    )


def test_card_is_timed_from_issuing_to_the_primary_status() -> None:
    assert attempt_seconds(card(primary=24)) == 24.0


def test_card_ignores_how_long_it_stayed_open_after_the_primary_status() -> None:
    assert attempt_seconds(card(primary=24, submitted=3000)) == 24.0


def test_card_without_a_primary_status_has_no_time() -> None:
    assert attempt_seconds(card(primary=None)) is None


def test_call_is_timed_from_answering_to_saving_the_card() -> None:
    # The span the evaluation scores (domain.evaluation.call_intake): 75 − 3.
    assert attempt_seconds(call(answered=3, submitted=75)) == 72.0


def test_call_without_a_saved_card_has_no_time() -> None:
    assert attempt_seconds(call(submitted=None)) is None


def test_call_that_was_never_answered_has_no_time() -> None:
    assert attempt_seconds(call(answered=None)) is None


def test_time_is_rounded_to_a_tenth_of_a_second() -> None:
    assert attempt_seconds(call(answered=3, submitted=75.44)) == 72.4


def row(attempt: SimpleNamespace, norm_seconds: int):
    """The attempt as the teacher's report shows it."""
    full = SimpleNamespace(
        id=uuid.uuid4(),
        card_number="38260001",
        state="evaluated",
        card_status="finished",
        result={"total": 80, "passed": True, "errors": []},
        status_log=[],
        flagged_fields=[],
        service_calls=[],
        dialog=[],
        **vars(attempt),
    )
    return report_attempt(
        full, SimpleNamespace(norm_seconds=norm_seconds), None, "Пожар: квартира", "Завершена"
    )


def test_report_times_a_call_against_the_norm() -> None:
    line = row(call(answered=3, submitted=75), norm_seconds=60)
    assert line.seconds == 72.0
    assert line.deviation == 12.0


def test_report_times_a_card_against_the_norm() -> None:
    line = row(card(primary=24), norm_seconds=30)
    assert line.seconds == 24.0
    assert line.deviation == -6.0


def test_report_leaves_the_time_empty_until_the_card_is_saved() -> None:
    line = row(call(submitted=None), norm_seconds=60)
    assert line.seconds is None
    assert line.deviation is None


def test_no_pace_advice_while_the_trainee_keeps_within_the_norm() -> None:
    late = Counter({MODE_CALL_INTAKE: 1})
    timed = Counter({MODE_CALL_INTAKE: 10})
    assert progress._overdue_mode(late, timed) is None


def test_pace_advice_names_the_mode_the_trainee_is_slow_in() -> None:
    late = Counter({MODE_CALL_INTAKE: 4})
    timed = Counter({MODE_CALL_INTAKE: 5, MODE_CARD_RESPONSE: 5})
    assert progress._overdue_mode(late, timed) == MODE_CALL_INTAKE


def test_pace_advice_picks_the_worse_mode_when_both_run_over() -> None:
    late = Counter({MODE_CALL_INTAKE: 2, MODE_CARD_RESPONSE: 4})
    timed = Counter({MODE_CALL_INTAKE: 5, MODE_CARD_RESPONSE: 5})
    assert progress._overdue_mode(late, timed) == MODE_CARD_RESPONSE


def test_a_slow_call_is_not_told_to_set_the_primary_status_sooner() -> None:
    tips = progress._recommendations(
        [], Counter({MODE_CALL_INTAKE: 4}), Counter({MODE_CALL_INTAKE: 5}), []
    )
    assert any("карточка часто сохраняется" in t.lower() for t in tips)
    assert not any("первичный статус" in t.lower() for t in tips)


def test_a_slow_card_still_gets_the_primary_status_advice() -> None:
    tips = progress._recommendations(
        [], Counter({MODE_CARD_RESPONSE: 4}), Counter({MODE_CARD_RESPONSE: 5}), []
    )
    assert any("первичный статус" in t.lower() for t in tips)
