"""Adaptive selection (PRD 9.7): the weakest incident group first, unseen scenarios first,
difficulty near the rating."""

import uuid
from dataclasses import dataclass, field

from app.domain.analytics.adaptive import order_queue
from app.domain.analytics.rating import RatingState


@dataclass
class FakeScenario:
    incident_type_code: str | None
    difficulty: int
    title: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def test_failure_in_gas_brings_a_gas_card_next() -> None:
    fire = FakeScenario("1.5.6.2", 2, "fire")
    gas = FakeScenario("13.2.4.0", 2, "gas")
    medical = FakeScenario("22.34.0.0", 2, "medical")
    state = RatingState()
    state.apply("13", "card_response", 2, 20)  # failed the gas card
    state.apply("1", "card_response", 2, 90)
    state.apply("22", "card_response", 2, 85)
    ordered = order_queue([fire, medical, gas], mode="card_response", ratings=state)
    assert ordered[0].title == "gas"


def test_unseen_scenarios_come_before_taken_ones() -> None:
    gas1 = FakeScenario("13.2.4.0", 1, "gas-1")
    gas2 = FakeScenario("13.2.4.0", 2, "gas-2")
    state = RatingState()
    state.apply("13", "card_response", 2, 20)
    ordered = order_queue([gas1, gas2], mode="card_response", ratings=state, done=[gas1.id])
    assert [s.title for s in ordered] == ["gas-2", "gas-1"]


def test_difficulty_near_the_rating_is_preferred() -> None:
    easy = FakeScenario("13.2.4.0", 1, "easy")
    hard = FakeScenario("13.2.4.0", 3, "hard")
    weak = RatingState()
    weak.apply("13", "card_response", 1, 0)  # rating drops below 1400
    strong = RatingState()
    strong.apply("13", "card_response", 3, 100)  # rating rises above 1400
    assert order_queue([hard, easy], mode="card_response", ratings=weak)[0].title == "easy"
    assert order_queue([easy, hard], mode="card_response", ratings=strong)[0].title == "hard"


def test_equal_candidates_keep_the_teacher_order() -> None:
    a = FakeScenario("1.5.6.2", 2, "a")
    b = FakeScenario("1.5.6.2", 2, "b")
    ordered = order_queue([b, a], mode="card_response", ratings=RatingState())
    assert [s.title for s in ordered] == ["b", "a"]
