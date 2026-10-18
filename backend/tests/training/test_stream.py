"""«Вал карточек» (issue #264): the schedule (the first card at once, then one every 20–25
seconds, five in all) and the summary of how the trainee held the stream."""

import uuid
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from app.models import ATTEMPT_FINISHED, ATTEMPT_RECEIVED, MODE_CARD_RESPONSE, Attempt
from app.training.service import STREAM_CARDS, STREAM_GAP_SECONDS
from app.training.stream import due_count, schedule, stream_summary

T0 = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)
NORM = 30


def test_first_card_at_once_then_one_every_20_to_25_seconds_five_in_all() -> None:
    lo, hi = STREAM_GAP_SECONDS
    assert (STREAM_CARDS, lo, hi) == (5, 20.0, 25.0)
    for seed in ("a", "b", "c", "d"):
        times = schedule(seed)
        assert len(times) == 5
        assert times[0] == 0.0
        gaps = [b - a for a, b in pairwise(times)]
        assert all(lo <= g <= hi for g in gaps)
        assert due_count(times, 0.0) == 1
        assert due_count(times, lo - 0.1) == 1
        # Nothing comes after the fifth card, however long the lesson runs.
        assert due_count(times, 3600.0) == 5


def test_schedule_is_stable_per_seed_and_differs_between_trainees() -> None:
    assert schedule("lesson:student1") == schedule("lesson:student1")
    assert schedule("lesson:student1") != schedule("lesson:student2")


def test_nothing_when_no_cards() -> None:
    assert schedule("x", cards=0) == []
    assert due_count([], 100.0) == 0


def card(arrived: float, taken: float | None = None, closed: bool = False) -> Attempt:
    """A card of the stream: seconds from the start when it arrived and when it was taken."""
    return Attempt(
        id=uuid.uuid4(),
        mode=MODE_CARD_RESPONSE,
        issued_at=T0 + timedelta(seconds=arrived),
        primary_status_at=None if taken is None else T0 + timedelta(seconds=taken),
        state=ATTEMPT_FINISHED if closed else ATTEMPT_RECEIVED,
    )


def test_stream_held_in_norm_with_the_emergency_first() -> None:
    urgent = card(10, 15, closed=True)
    cards = [card(0, 20, closed=True), urgent, card(30, 40)]
    summary = stream_summary(cards, {urgent.id}, NORM)
    assert (summary.arrived, summary.closed, summary.late, summary.no_reaction) == (3, 2, 0, 0)
    assert (summary.emergency_cards, summary.emergency_first) == (1, True)
    assert summary.advice == ["Вал выдержан: все карточки приняты в норматив, ЧС — первой."]


def test_ordinary_card_taken_while_the_emergency_waited() -> None:
    urgent = card(10, 25)
    cards = [card(0, 20), urgent]
    summary = stream_summary(cards, {urgent.id}, NORM)
    assert summary.emergency_first is False
    assert summary.advice == [
        "Карточку с ЧС взяли позже обычных. При вале первой берите в работу карточку с отметкой ЧС."
    ]


def test_late_and_untouched_cards_are_counted_with_advice() -> None:
    urgent = card(50)
    cards = [card(0, 45), card(5, 50), card(20, 30), urgent, card(55)]
    summary = stream_summary(cards, {urgent.id}, NORM)
    assert (summary.late, summary.no_reaction, summary.emergency_first) == (2, 2, False)
    assert [tip.split(".")[0] for tip in summary.advice] == [
        "2 карточки остались без реакции",
        "Карточку с ЧС взяли позже обычных",
        "2 карточки приняты позже норматива (30 с)",
    ]


def test_no_emergency_in_the_stream() -> None:
    summary = stream_summary([card(0, 5)], set(), NORM)
    assert summary.emergency_first is None
    assert summary.advice == ["Вал выдержан: все карточки приняты в норматив."]
