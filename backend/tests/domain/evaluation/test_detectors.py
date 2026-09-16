"""Typical error detectors, each on a memo example (pages 28-32): fires and does not fire."""

from __future__ import annotations

import pytest

from app.domain.evaluation.call_intake import evaluate_call_intake
from app.domain.evaluation.card_response import evaluate_card_response
from app.domain.evaluation.detectors import CALL_DETECTORS, CARD_DETECTORS
from app.domain.evaluation.schemas import (
    CallIntakeAttempt,
    CardResponseAttempt,
    CardResponseScenario,
)
from app.domain.reference_data import TYPICAL_ERRORS
from tests.domain.evaluation.helpers import (
    at,
    call_scenario,
    card_attempt,
    card_scenario,
    perfect_call_attempt,
    perfect_card_attempt,
)


def card_errors(scenario: CardResponseScenario, attempt: CardResponseAttempt) -> dict[str, str]:
    result = evaluate_card_response(scenario, attempt)
    return {e.code: e.explanation for e in result.errors}


def test_every_typical_error_has_a_detector() -> None:
    codes = {e["code"] for e in TYPICAL_ERRORS}
    assert codes == set(CARD_DETECTORS) | set(CALL_DETECTORS)


def test_perfect_attempts_trigger_nothing() -> None:
    for name in (
        "card_2-1_zadymlenie_musoroprovoda",
        "card_2-1_dubl",
        "card_17-1_pozharnaya_signalizaciya",
        "card_31-3_svist_gazovoy_truby",
        "card_5-2_otravlenie_lekarstvami",
    ):
        scenario = card_scenario(name)
        assert card_errors(scenario, perfect_card_attempt(scenario)) == {}, name


# --- no_status (memo p. 28, example 1) --------------------------------------------------------


def test_no_status_fires_without_a_primary_status() -> None:
    errors = card_errors(card_scenario(), card_attempt({"status": "received"}))
    assert "no_status" in errors
    assert "Не оповещено" in errors["no_status"]
    assert "late_primary" not in errors


def test_no_status_silent_when_rejected() -> None:
    errors = card_errors(
        card_scenario("card_2-1_dubl"),
        card_attempt(
            {"status": "received"}, {"status": "rejected", "comment": "дубль, КП 38260311"}
        ),
    )
    assert "no_status" not in errors


# --- late_primary (memo p. 21, 26) ------------------------------------------------------------


def test_late_primary_fires_after_the_norm() -> None:
    errors = card_errors(
        card_scenario(), card_attempt({"status": "received"}, {"status": "accepted", "at": at(31)})
    )
    assert "late_primary" in errors
    assert "31 с" in errors["late_primary"]


def test_late_primary_silent_within_the_norm() -> None:
    errors = card_errors(
        card_scenario(), card_attempt({"status": "received"}, {"status": "accepted", "at": at(30)})
    )
    assert "late_primary" not in errors


# --- status_mismatch (memo p. 28, example 2) -------------------------------------------------


def test_status_mismatch_fires_on_accepted_with_a_refusal_comment() -> None:
    # «Принята: не обслуживаем территорию» → should have been «Не принята».
    errors = card_errors(
        card_scenario(),
        card_attempt(
            {"status": "received"},
            {"status": "accepted", "comment": "Принята: не обслуживаем территорию"},
        ),
    )
    assert "status_mismatch" in errors
    assert "Не принята" in errors["status_mismatch"]


def test_status_mismatch_fires_on_rejected_with_a_squad() -> None:
    errors = card_errors(
        card_scenario(),
        card_attempt(
            {"status": "received"},
            {
                "status": "rejected",
                "comment": "Наряд выехал, информация передана",
                "order_number": "1",
            },
        ),
    )
    assert "status_mismatch" in errors


def test_status_mismatch_silent_on_a_plain_accept() -> None:
    errors = card_errors(
        card_scenario(),
        card_attempt(
            {"status": "received"}, {"status": "accepted", "comment": "Информация принята"}
        ),
    )
    assert "status_mismatch" not in errors


# --- competence_refusal (memo p. 29, example 3: «Не принята: в компетенции 102») --------------


def test_competence_refusal_fires_when_another_service_is_the_excuse() -> None:
    errors = card_errors(
        card_scenario(),
        card_attempt(
            {"status": "received"},
            {"status": "rejected", "comment": "Не принята: в компетенции 102, информация передана"},
        ),
    )
    assert "competence_refusal" in errors
    assert "profile_refusal" not in errors


def test_competence_refusal_silent_when_the_reference_also_rejects() -> None:
    scenario = card_scenario("card_17-1_pozharnaya_signalizaciya")
    errors = card_errors(
        scenario,
        card_attempt(
            {"status": "received"},
            {"status": "rejected", "comment": "Дом обслуживает УК «ПИК», информация передана в УК"},
        ),
    )
    assert "competence_refusal" not in errors


# --- profile_refusal (memo p. 29, example 3: «Не принята» без комментариев) ------------------


def test_profile_refusal_fires_on_rejecting_a_profile_incident() -> None:
    errors = card_errors(
        card_scenario(), card_attempt({"status": "received"}, {"status": "rejected", "comment": ""})
    )
    assert "profile_refusal" in errors
    assert "empty_reject_comment" in errors


def test_profile_refusal_silent_after_the_decision_is_corrected() -> None:
    # Memo p. 32: mistaken «Не принята» → set «Принята».
    errors = card_errors(
        card_scenario(),
        card_attempt(
            {"status": "received"},
            {"status": "rejected", "comment": "не наш дом", "at": at(20)},
            {"status": "accepted", "at": at(40)},
        ),
    )
    assert "profile_refusal" not in errors


# --- empty_reject_comment (memo p. 30, example 4) ---------------------------------------------


def test_empty_reject_comment_fires_on_refusal_without_text() -> None:
    errors = card_errors(
        card_scenario("card_2-1_dubl"),
        card_attempt({"status": "received"}, {"status": "rejected", "reject_reason": "duplicate"}),
    )
    assert "empty_reject_comment" in errors


def test_empty_reject_comment_fires_on_works_refused_without_text() -> None:
    errors = card_errors(
        card_scenario(),
        card_attempt(
            {"status": "received"},
            {"status": "accepted"},
            {"status": "works_refused", "comment": "   "},
        ),
    )
    assert "empty_reject_comment" in errors


def test_empty_reject_comment_silent_with_text() -> None:
    errors = card_errors(
        card_scenario("card_2-1_dubl"),
        card_attempt(
            {"status": "received"},
            {"status": "rejected", "reject_reason": "duplicate", "comment": "дубль КП 38260311"},
        ),
    )
    assert "empty_reject_comment" not in errors


# --- incomplete_comment (memo p. 30, example 5: «Не принята: не обслуживаем») -----------------


def test_incomplete_comment_fires_without_hand_over_information() -> None:
    scenario = card_scenario("card_17-1_pozharnaya_signalizaciya")
    errors = card_errors(
        scenario,
        card_attempt({"status": "received"}, {"status": "rejected", "comment": "Не обслуживаем"}),
    )
    assert "incomplete_comment" in errors


def test_incomplete_comment_silent_when_hand_over_is_stated() -> None:
    scenario = card_scenario("card_17-1_pozharnaya_signalizaciya")
    errors = card_errors(
        scenario,
        card_attempt(
            {"status": "received"},
            {"status": "rejected", "comment": "Не обслуживаем, информация передана в УК «ПИК»"},
        ),
    )
    assert "incomplete_comment" not in errors


def test_incomplete_comment_silent_for_a_duplicate() -> None:
    errors = card_errors(
        card_scenario("card_2-1_dubl"),
        card_attempt(
            {"status": "received"}, {"status": "rejected", "comment": "Не принята: дубль"}
        ),
    )
    assert "incomplete_comment" not in errors


# --- progress_missing (memo p. 31, example 6) -------------------------------------------------


def test_progress_missing_fires_when_progress_statuses_are_skipped() -> None:
    errors = card_errors(
        card_scenario(),
        card_attempt(
            {"status": "received"},
            {"status": "accepted"},
            {"status": "works_done", "comment": "Задымление устранено"},
        ),
    )
    assert "progress_missing" in errors
    assert "«Начало реагирования»" in errors["progress_missing"]
    assert "«Прибытие»" in errors["progress_missing"]


def test_progress_missing_fires_when_a_progress_status_has_no_comment() -> None:
    errors = card_errors(
        card_scenario(),
        card_attempt(
            {"status": "received"},
            {"status": "accepted"},
            {"status": "response_started", "order_number": "14-217"},
            {"status": "arrived"},
            {"status": "works_started"},
            {"status": "works_done", "comment": "Задымление устранено"},
        ),
    )
    assert "progress_missing" in errors
    assert "без комментария" in errors["progress_missing"]
    assert "«Проведение работ»" in errors["progress_missing"]


def test_progress_missing_silent_on_a_full_chain_and_on_a_refusal() -> None:
    scenario = card_scenario()
    assert "progress_missing" not in card_errors(scenario, perfect_card_attempt(scenario))
    errors = card_errors(
        card_scenario("card_2-1_dubl"),
        card_attempt(
            {"status": "received"}, {"status": "rejected", "comment": "дубль КП 38260311"}
        ),
    )
    assert "progress_missing" not in errors


# --- duplicate_accepted (memo p. 30: карточка Службы 101 на 2 минуты раньше) ------------------


def test_duplicate_accepted_fires_on_accepting_a_duplicate() -> None:
    errors = card_errors(
        card_scenario("card_2-1_dubl"), card_attempt({"status": "received"}, {"status": "accepted"})
    )
    assert "duplicate_accepted" in errors
    assert "38260311" in errors["duplicate_accepted"]
    assert "wrong_accept_unfixed" in errors


def test_duplicate_accepted_silent_on_the_original_card() -> None:
    scenario = card_scenario()
    errors = card_errors(scenario, card_attempt({"status": "received"}, {"status": "accepted"}))
    assert "duplicate_accepted" not in errors


# --- wrong_accept_unfixed (memo p. 32) --------------------------------------------------------


def test_wrong_accept_unfixed_fires_when_the_mistake_stays() -> None:
    scenario = card_scenario("card_17-1_pozharnaya_signalizaciya")
    errors = card_errors(scenario, card_attempt({"status": "received"}, {"status": "accepted"}))
    assert "wrong_accept_unfixed" in errors


def test_wrong_accept_unfixed_silent_after_works_refused_with_a_comment() -> None:
    scenario = card_scenario("card_17-1_pozharnaya_signalizaciya")
    errors = card_errors(
        scenario,
        card_attempt(
            {"status": "received"},
            {"status": "accepted"},
            {"status": "works_refused", "comment": "Дом обслуживает УК «ПИК», информация передана"},
        ),
    )
    assert "wrong_accept_unfixed" not in errors


# --- wrong_final_status (memo p. 29: осиное гнездо, «Работы завершены: нет договора») ---------


def test_wrong_final_status_fires_on_works_done_with_a_refusal_comment() -> None:
    errors = card_errors(
        card_scenario(),
        card_attempt(
            {"status": "received"},
            {"status": "accepted"},
            {
                "status": "works_done",
                "comment": "Нет договора с организацией, которая удаляет гнёзда",
            },
        ),
    )
    assert "wrong_final_status" in errors
    assert "Отказ от выполнения работ" in errors["wrong_final_status"]


def test_wrong_final_status_silent_for_service_103_and_real_completion() -> None:
    scenario = card_scenario("card_5-2_otravlenie_lekarstvami")  # service 103
    errors = card_errors(
        scenario,
        card_attempt(
            {"status": "received"},
            {"status": "accepted"},
            {"status": "works_done", "comment": "Завершение работ без бригады: не обслуживаем"},
        ),
    )
    assert "wrong_final_status" not in errors
    scenario = card_scenario()
    assert "wrong_final_status" not in card_errors(scenario, perfect_card_attempt(scenario))


# --- call_intake detectors --------------------------------------------------------------------


def call_errors(name: str, attempt: CallIntakeAttempt) -> dict[str, str]:
    result = evaluate_call_intake(call_scenario(name), attempt)
    return {e.code: e.explanation for e in result.errors}


def test_address_not_asked_fires_when_the_address_is_never_touched() -> None:
    scenario = call_scenario()
    attempt = perfect_call_attempt(scenario)
    attempt.dialog = [t for t in attempt.dialog if "address" not in t.topics]
    attempt.card.address.street = None
    attempt.card.address.descriptive = None
    errors = call_errors("call_2-1_zadymlenie_musoroprovoda", attempt)
    assert "address_not_asked" in errors


def test_address_not_asked_silent_when_asked_or_in_the_card() -> None:
    scenario = call_scenario()
    assert "address_not_asked" not in call_errors(
        "call_2-1_zadymlenie_musoroprovoda", perfect_call_attempt(scenario)
    )
    attempt = perfect_call_attempt(scenario)
    attempt.dialog = []  # not labelled, but the card has the street
    assert "address_not_asked" not in call_errors("call_2-1_zadymlenie_musoroprovoda", attempt)


def test_no_call_dropped_mark_fires_only_when_the_caller_hung_up() -> None:
    scenario = call_scenario("call_2-2_skandal_sryv_zvonka")
    attempt = perfect_call_attempt(scenario)
    attempt.call_dropped_marked = False
    assert "no_call_dropped_mark" in call_errors("call_2-2_skandal_sryv_zvonka", attempt)
    attempt.call_dropped_marked = True
    assert "no_call_dropped_mark" not in call_errors("call_2-2_skandal_sryv_zvonka", attempt)
    # A caller who did not hang up never triggers it.
    other = call_scenario()
    assert "no_call_dropped_mark" not in call_errors(
        "call_2-1_zadymlenie_musoroprovoda", perfect_call_attempt(other)
    )


@pytest.mark.parametrize(
    ("region", "city", "fires"),
    [
        (None, None, True),
        ("Москва", None, True),
        ("Волгоградская область", "Волжский", False),
        ("Волгоградская обл.", None, False),
        (None, "Волжский, Волгоградская область", False),
    ],
)
def test_region_not_clarified(region: str | None, city: str | None, fires: bool) -> None:
    scenario = call_scenario("call_1-3_rebenok_velosiped_volzhskiy")
    attempt = perfect_call_attempt(scenario)
    attempt.card.address.region = region
    attempt.card.address.city = city
    errors = call_errors("call_1-3_rebenok_velosiped_volzhskiy", attempt)
    assert ("region_not_clarified" in errors) is fires


def test_region_not_clarified_silent_for_moscow_scenarios() -> None:
    scenario = call_scenario()
    attempt = perfect_call_attempt(scenario)
    attempt.card.address.region = None
    assert "region_not_clarified" not in call_errors("call_2-1_zadymlenie_musoroprovoda", attempt)
