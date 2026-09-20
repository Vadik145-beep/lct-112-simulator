"""«Реагирование на карточку»: components, time, chain, comments, renormalization, weights."""

from __future__ import annotations

import pytest

from app.domain.evaluation import evaluate_sync
from app.domain.evaluation.card_response import evaluate_card_response
from app.providers.embeddings import TfidfEmbedding
from tests.domain.evaluation.helpers import (
    at,
    card_attempt,
    card_scenario,
    grammar_down,
    grammar_errors,
    grammar_ok,
    load,
    perfect_card_attempt,
)


def test_perfect_attempt_scores_100() -> None:
    scenario = card_scenario()
    result = evaluate_card_response(scenario, perfect_card_attempt(scenario), grammar=grammar_ok())
    assert result.total == 100
    assert result.passed
    assert result.errors == []
    assert {k: c.score for k, c in result.components.items()} == {
        "decision": 30,
        "time": 20,
        "status_chain": 20,
        "comments": 15,
        "typical_errors": 10,
        "grammar": 5,
    }
    assert result.methods == {"grammar": "languagetool", "similarity": "tfidf"}


def test_prd_example_components() -> None:
    """The PRD 9.2 example: reference chain of five statuses with a squad number and comments.
    A dispatcher who accepts in 20 s and sets the chain with paraphrased comments."""
    scenario = card_scenario()
    attempt = card_attempt(
        {"status": "received"},
        {"status": "accepted", "at": at(20)},
        {
            "status": "response_started",
            "order_number": "14-217",
            "comment": "Дежурный слесарь направлен на место, наряд 14-217",
        },
        {"status": "arrived"},
        {"status": "works_started", "comment": "Вскрыли мусоропровод, убрали тлеющий мусор"},
        {"status": "works_done", "comment": "Задымления нет, ствол мусоропровода промыт"},
    )
    result = evaluate_card_response(scenario, attempt, grammar=grammar_ok())
    components = result.components
    assert components["decision"].score == 30
    assert components["time"].score == 20
    assert components["status_chain"].score == 20
    assert components["status_chain"].items[0]["missing"] == []
    assert 15 * 0.6 <= components["comments"].score <= 15  # paraphrases, not verbatim
    assert components["typical_errors"].score == 10
    assert components["grammar"].score == 5
    assert result.errors == []
    assert result.total >= 90
    assert result.passed


def test_rejected_without_comment() -> None:
    scenario = card_scenario("card_2-1_dubl")
    attempt = card_attempt(
        {"status": "received"}, {"status": "rejected", "reject_reason": "duplicate"}
    )
    result = evaluate_card_response(scenario, attempt, grammar=grammar_ok())
    codes = {e.code for e in result.errors}
    assert "empty_reject_comment" in codes
    assert result.components["decision"].score == 30  # decision and reason are right
    assert result.components["comments"].score == 0  # the mandatory comment is missing
    assert result.components["typical_errors"].score == 10 - 6
    assert result.total < 100


def test_reject_with_wrong_reason_gives_half_of_the_decision() -> None:
    scenario = card_scenario("card_2-1_dubl")
    attempt = card_attempt(
        {"status": "received"},
        {
            "status": "rejected",
            "reject_reason": "not_our_territory",
            "comment": "Не наша территория, передано в 101",
        },
    )
    result = evaluate_card_response(scenario, attempt)
    assert result.components["decision"].score == 15
    assert (
        result.components["decision"].items[0]["note"] == "решение верно, причина отказа не совпала"
    )


def test_reason_is_inferred_from_the_comment() -> None:
    scenario = card_scenario("card_2-1_dubl")
    attempt = card_attempt(
        {"status": "received"},
        {"status": "rejected", "comment": "Не принята: дубль, реагирование по КП 38260311"},
    )
    result = evaluate_card_response(scenario, attempt)
    assert result.components["decision"].score == 30
    assert result.components["decision"].items[1]["actual_reason"] == "duplicate"


def test_wrong_decision_scores_zero_and_fails_on_a_critical_error() -> None:
    scenario = card_scenario("card_2-1_dubl")  # critical: duplicate_accepted
    attempt = card_attempt({"status": "received"}, {"status": "accepted"})
    result = evaluate_card_response(scenario, attempt, grammar=grammar_ok())
    assert result.components["decision"].score == 0
    assert any(e.code == "duplicate_accepted" and e.critical for e in result.errors)
    assert not result.passed


def test_corrected_decision_earns_half() -> None:
    scenario = card_scenario()
    attempt = card_attempt(
        {"status": "received"},
        {"status": "rejected", "comment": "не наш дом", "at": at(20)},
        {"status": "accepted", "at": at(40)},
    )
    result = evaluate_card_response(scenario, attempt)
    assert result.components["decision"].score == 15
    assert "исправлено" in result.components["decision"].items[0]["note"]


def test_missing_status_lowers_the_chain() -> None:
    scenario = card_scenario()
    attempt = card_attempt(
        {"status": "received"},
        {"status": "accepted"},
        {"status": "response_started", "order_number": "14-217", "comment": "Направлен слесарь"},
        {"status": "works_started", "comment": "Вскрыт мусоропровод"},  # «Прибытие» skipped
        {"status": "works_done", "comment": "Устранено"},
    )
    result = evaluate_card_response(scenario, attempt)
    chain = result.components["status_chain"]
    assert chain.score == 16  # 4 of 5 reference statuses
    assert chain.items[0]["missing"] == ["arrived"]
    assert chain.items[0]["extra"] == []


def test_extra_status_is_listed() -> None:
    scenario = card_scenario("card_2-1_dubl")
    attempt = card_attempt(
        {"status": "received"},
        {"status": "rejected", "comment": "дубль", "reject_reason": "duplicate", "at": at(20)},
        {"status": "accepted", "at": at(40)},
        {"status": "works_refused", "comment": "дубль КП 38260311", "at": at(60)},
    )
    result = evaluate_card_response(scenario, attempt)
    chain = result.components["status_chain"]
    assert chain.score == 20  # «Не принята» is present
    assert chain.items[0]["extra"] == ["accepted", "works_refused"]


def test_invalid_transition_is_skipped_and_reported() -> None:
    scenario = card_scenario()
    attempt = card_attempt(
        {"status": "received"},
        {"status": "arrived", "at": at(10)},  # impossible before «Принята»
        {"status": "accepted", "at": at(20)},
        {"status": "response_started", "order_number": "14-217", "comment": "Направлен слесарь"},
        {"status": "arrived"},
        {"status": "works_started", "comment": "Вскрыт мусоропровод"},
        {"status": "works_done", "comment": "Устранено"},
    )
    result = evaluate_card_response(scenario, attempt)
    chain = result.components["status_chain"]
    assert chain.score == 20
    assert chain.items[1]["invalid"] == "arrived"
    assert "нельзя проставить «Прибытие»" in chain.items[1]["message"]


@pytest.mark.parametrize(("seconds", "expected"), [(10, 20), (30, 20), (45, 10), (60, 0), (90, 0)])
def test_time_component(seconds: int, expected: int) -> None:
    scenario = card_scenario()  # norm 30 s
    attempt = card_attempt({"status": "received"}, {"status": "accepted", "at": at(seconds)})
    result = evaluate_card_response(scenario, attempt)
    assert result.components["time"].score == expected
    assert result.components["time"].items[0]["seconds"] == seconds


def test_time_is_zero_without_a_primary_status() -> None:
    result = evaluate_card_response(card_scenario(), card_attempt({"status": "received"}))
    assert result.components["time"].score == 0
    assert result.components["decision"].score == 0


def test_comments_and_order_number() -> None:
    scenario = card_scenario()
    attempt = card_attempt(
        {"status": "received"},
        {"status": "accepted"},
        {
            "status": "response_started",
            "comment": "Направлен дежурный слесарь, наряд 14-217",
        },  # no squad
        {"status": "arrived"},
        {"status": "works_started"},  # no comment
        {"status": "works_done", "comment": "Задымление устранено, ствол промыт, пострадавших нет"},
    )
    result = evaluate_card_response(scenario, attempt)
    comments = result.components["comments"]
    by_status = {i["status"]: i for i in comments.items}
    assert by_status["response_started"]["order_number"] == "нет"
    assert by_status["response_started"]["fraction"] == 0.5
    assert by_status["works_started"]["comment"] == "нет"
    assert by_status["works_started"]["fraction"] == 0
    assert by_status["works_done"]["fraction"] == 1
    assert comments.score == 7.5


def test_grammar_errors_reduce_the_component() -> None:
    scenario = card_scenario()
    attempt = perfect_card_attempt(scenario)
    assert (
        evaluate_card_response(scenario, attempt, grammar=grammar_errors(2))
        .components["grammar"]
        .score
        == 3
    )
    assert (
        evaluate_card_response(scenario, attempt, grammar=grammar_errors(9))
        .components["grammar"]
        .score
        == 0
    )


def test_languagetool_down_renormalizes_the_total() -> None:
    scenario = card_scenario()
    attempt = card_attempt(
        {"status": "received"},
        {"status": "accepted", "at": at(45)},  # time 10 of 20
        {
            "status": "response_started",
            "order_number": "14-217",
            "comment": "Направлен дежурный слесарь, наряд 14-217",
        },
        {"status": "arrived"},
        {"status": "works_started", "comment": "Мусоропровод вскрыт, тлеющий мусор удалён"},
        {"status": "works_done", "comment": "Задымление устранено, ствол промыт, пострадавших нет"},
    )
    with_grammar = evaluate_card_response(scenario, attempt, grammar=grammar_ok())
    without = evaluate_card_response(scenario, attempt, grammar=grammar_down())
    assert without.components["grammar"].status == "not_checked"
    assert without.methods["grammar"] == "not_checked"
    # 30 + 10 + 20 + 15 + 6 (late_primary −4) = 81 of 95 → 85; with grammar 86 of 100.
    assert with_grammar.total == 86
    assert without.total == round(81 / 95 * 100)
    assert evaluate_card_response(scenario, attempt, grammar=None).total == without.total


def test_session_weights_scale_the_maximums() -> None:
    scenario = card_scenario()
    attempt = perfect_card_attempt(scenario)
    weights = {
        "decision": 35,
        "time": 20,
        "status_chain": 20,
        "comments": 15,
        "typical_errors": 10,
        "grammar": 0,
    }
    result = evaluate_card_response(scenario, attempt, weights=weights, grammar=grammar_errors(3))
    assert result.components["grammar"].status == "disabled"
    assert result.components["decision"].max == 35
    assert result.total == 100
    # The same attempt with a wrong decision: only the counted components matter.
    wrong = card_attempt(
        {"status": "received"}, {"status": "rejected", "comment": "не наш, передано в 101"}
    )
    result = evaluate_card_response(scenario, wrong, weights=weights, grammar=grammar_ok())
    assert result.components["grammar"].status == "disabled"
    assert result.total == round((0 + 20 + 0 + 0 + 4) / 100 * 100)


def test_weights_are_normalized_and_must_be_known() -> None:
    """Issue #35: the sum is no longer fixed at 100 — the applicable components are normalized
    to 100 proportionally; only an all-zero set and unknown keys are rejected."""
    scenario = card_scenario()
    attempt = perfect_card_attempt(scenario)
    result = evaluate_card_response(scenario, attempt, weights={"grammar": 0}, grammar=grammar_ok())
    assert result.components["grammar"].status == "disabled"
    assert sum(c.max for c in result.components.values()) == 100
    assert result.components["decision"].max == 33  # 30 of 95 → 31, the remainder 2 on top
    zeros = dict.fromkeys(
        ("decision", "time", "status_chain", "comments", "typical_errors", "grammar"), 0
    )
    with pytest.raises(ValueError, match="больше нуля"):
        evaluate_card_response(scenario, attempt, weights=zeros)
    with pytest.raises(ValueError, match="неизвестные составляющие"):
        evaluate_card_response(scenario, attempt, weights={"speed": 0})


def test_classic_six_weights_score_as_before_without_planted_errors() -> None:
    """Regression for issue #35: a lesson with the six classic weights and a card without
    ``injected_errors`` gets the same maxima and total as before «Проверка данных» existed."""
    scenario = card_scenario()
    assert scenario.injected_errors == []
    attempt = card_attempt(
        {"status": "received"},
        {"status": "accepted", "at": at(45)},
        {"status": "response_started", "order_number": "14-217", "comment": "Направлен слесарь"},
        {"status": "arrived"},
        {"status": "works_started", "comment": "Мусоропровод вскрыт, тлеющий мусор удалён"},
        {"status": "works_done", "comment": "Задымление устранено, ствол промыт, пострадавших нет"},
    )
    classic = {
        "decision": 30,
        "time": 20,
        "status_chain": 20,
        "comments": 15,
        "typical_errors": 10,
        "grammar": 5,
    }
    result = evaluate_card_response(scenario, attempt, weights=classic, grammar=grammar_ok())
    assert "data_check" not in result.components
    assert {k: c.max for k, c in result.components.items()} == classic
    assert result.total == 86  # as in test_languagetool_down_renormalizes_the_total
    assert evaluate_card_response(scenario, attempt, grammar=grammar_ok()).total == 86


def test_pass_threshold_is_applied() -> None:
    scenario = card_scenario("card_31-3_svist_gazovoy_truby")  # progress_missing is not critical
    attempt = card_attempt({"status": "received"}, {"status": "accepted", "at": at(20)})
    assert not evaluate_card_response(scenario, attempt).passed
    assert evaluate_card_response(scenario, attempt, pass_threshold=40).passed


def test_service_103_closes_without_a_squad() -> None:
    scenario = card_scenario("card_5-2_otravlenie_lekarstvami")
    attempt = card_attempt(
        {"status": "received"},
        {"status": "accepted"},
        {"status": "works_done", "comment": "Завершение работ без бригады: пациентка отказалась"},
    )
    result = evaluate_card_response(scenario, attempt)
    assert {e.code for e in result.errors} == {"progress_missing"}
    assert result.components["status_chain"].items[0]["matched"] == ["accepted", "works_done"]


def test_evaluate_sync_accepts_raw_dictionaries() -> None:
    body = load("card_2-1_zadymlenie_musoroprovoda")
    attempt = perfect_card_attempt(card_scenario()).model_dump(mode="json")
    result = evaluate_sync(body, attempt, embeddings=TfidfEmbedding())
    assert result.mode == "card_response"
    assert result.total == 100
    payload = result.to_dict()
    assert payload["components"]["decision"]["score"] == 30
    assert payload["methods"]["grammar"] == "not_checked"
