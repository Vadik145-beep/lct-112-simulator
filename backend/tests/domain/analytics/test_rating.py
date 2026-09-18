"""Skill rating rule (PRD 9.7): Elo-like moves that depend on the scenario difficulty."""

from app.domain.analytics.rating import (
    START_RATING,
    RatingState,
    expected_score,
    incident_group,
    nearest_difficulty,
    updated_rating,
)


def test_beating_a_hard_scenario_moves_more_than_an_easy_one() -> None:
    hard = updated_rating(START_RATING, 3, 100) - START_RATING
    easy = updated_rating(START_RATING, 1, 100) - START_RATING
    assert hard > easy > 0


def test_failing_an_easy_scenario_costs_more_than_a_hard_one() -> None:
    easy = updated_rating(START_RATING, 1, 0) - START_RATING
    hard = updated_rating(START_RATING, 3, 0) - START_RATING
    assert easy < hard < 0


def test_expected_score_is_half_against_an_equal_scenario() -> None:
    assert expected_score(1400, 1400) == 0.5
    assert expected_score(1600, 1400) > 0.5


def test_total_above_100_is_clamped() -> None:
    assert updated_rating(START_RATING, 2, 250) == updated_rating(START_RATING, 2, 100)


def test_state_tracks_groups_and_counts() -> None:
    state = RatingState()
    state.apply("13", "card_response", 2, 40)
    state.apply("13", "card_response", 2, 40)
    state.apply("1", "card_response", 2, 95)
    assert state.counts[("13", "card_response")] == 2
    assert state.get("13", "card_response") < START_RATING < state.get("1", "card_response")
    # An unseen group counts as the start rating, so it comes before the strong one.
    assert state.weakest_groups("card_response", ["1", "13", "22"]) == ["13", "22", "1"]
    assert state.minimum() == state.get("13", "card_response")


def test_incident_group_and_nearest_difficulty() -> None:
    assert incident_group("13.2.4.0") == "13"
    assert incident_group(None) == "0"
    assert nearest_difficulty(1250) == 1
    assert nearest_difficulty(1450) == 2
    assert nearest_difficulty(1700) == 3
