"""«Принята» closed by «Отказ от выполнения работ» (decision of 18.10.2026): on a card to refuse it
is the memo's correction (p. 32) — counted as the refusal, minus six for the wrong «Принята»; on a
card to accept the same chain stays a failure, so it cannot be used to game the score."""

from __future__ import annotations

from app.domain.evaluation.card_response import WRONG_ACCEPT_POINTS, evaluate_card_response
from app.domain.evaluation.schemas import (
    CardResponseAttempt,
    CardResponseScenario,
    ReferenceStep,
)
from tests.domain.evaluation.helpers import at, card_scenario, grammar_ok, status_log

COMMENT = "не наш профиль, информация передана в ЕДДС"


def to_refuse() -> CardResponseScenario:
    scenario = card_scenario()
    step = ReferenceStep(status="rejected", comment_example="Не в компетенции службы, передано")
    reference = scenario.reference.model_copy(
        update={
            "decision": "reject",
            "reject_reason": "not_in_competence",
            "status_chain": [step],
            "reports": [],
            "service_calls": [],
        }
    )
    return scenario.model_copy(update={"reference": reference, "injected_errors": []})


def attempt(*steps: dict) -> CardResponseAttempt:
    return CardResponseAttempt(
        issued_at=at(0),
        received_at=at(5),
        status_log=status_log({"status": "received"}, *steps),
        flagged_fields=[],
        service_calls=[],
    )


ACCEPT_THEN_REFUSE = (
    {"status": "accepted"},
    {"status": "works_refused", "comment": COMMENT},
)


def components(scenario: CardResponseScenario, att: CardResponseAttempt) -> tuple:
    result = evaluate_card_response(scenario, att, grammar=grammar_ok())
    return result.components, result


def test_on_a_card_to_refuse_the_corrected_refusal_counts() -> None:
    scenario = to_refuse()
    parts, fixed = components(scenario, attempt(*ACCEPT_THEN_REFUSE))
    assert parts["status_chain"].score == parts["status_chain"].max
    assert parts["comments"].score > 0
    decision = parts["decision"]
    assert decision.score == decision.max - WRONG_ACCEPT_POINTS
    _, right = components(
        scenario, attempt({"status": "rejected", "comment": COMMENT, "reject_reason": None})
    )
    assert fixed.total < right.total
    assert fixed.total >= 55


def test_on_a_card_to_accept_the_same_chain_still_fails() -> None:
    scenario = card_scenario()
    parts, result = components(scenario, attempt(*ACCEPT_THEN_REFUSE))
    assert parts["decision"].score == 0
    assert not result.passed
