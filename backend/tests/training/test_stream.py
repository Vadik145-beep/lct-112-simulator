"""«Вал карточек» (issue #264): the schedule (an opening burst, then the rate with spread) and
the summary of how the trainee held the stream."""

import uuid
from datetime import UTC, datetime, timedelta

from app.models import ATTEMPT_FINISHED, ATTEMPT_RECEIVED, MODE_CARD_RESPONSE, Attempt
from app.training.stream import JITTER, OPENING_BURST, due_count, schedule, stream_summary

T0 = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)
NORM = 30


def test_rate_and_length_hold_with_spread() -> None:
    for seed in ("a", "b", "c", "d"):
        times = schedule(seed, 4, 5)
        assert 0.8 * 20 <= len(times) <= 20
        assert times == sorted(times)
        assert times[-1] <= 5 * 60
        # The shift does not start empty: two or three cards in the first seconds.
        assert due_count(times, 5.0) in range(OPENING_BURST[0], OPENING_BURST[1] + 1)
        gaps = [
            b - a
            for a, b in zip(times[OPENING_BURST[1] :], times[OPENING_BURST[1] + 1 :], strict=False)
        ]
        assert all(15 * (1 - JITTER) - 1e-9 <= g <= 15 * (1 + JITTER) + 1e-9 for g in gaps)


def test_schedule_is_stable_per_seed_and_differs_between_trainees() -> None:
    assert schedule("lesson:student1", 6, 3) == schedule("lesson:student1", 6, 3)
    assert schedule("lesson:student1", 6, 3) != schedule("lesson:student2", 6, 3)


def test_nothing_when_the_stream_is_off() -> None:
    assert schedule("x", 0, 5) == []
    assert schedule("x", 4, 0) == []
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
