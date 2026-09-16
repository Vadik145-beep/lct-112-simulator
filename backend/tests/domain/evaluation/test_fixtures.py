"""The 10 reference scenarios in data/seed/scenarios: valid, consistent with the classifier
and the memo, and a perfect attempt against each scores 100."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.evaluation import evaluate_sync, parse_scenario
from app.domain.evaluation.schemas import (
    CallIntakeScenario,
    CardResponseAttempt,
    CardResponseScenario,
)
from app.domain.evaluation.status_machine import STATUSES
from app.domain.reference_data import CALLER_TOPICS, REJECT_REASONS, TYPICAL_ERRORS
from app.domain.services import resolve_services
from tests.domain.evaluation.helpers import (
    SCENARIOS_DIR,
    grammar_ok,
    perfect_call_attempt,
    perfect_card_attempt,
)

FILES = sorted(SCENARIOS_DIR.glob("*.json"))
CLASSIFIER = json.loads((SCENARIOS_DIR.parent / "classifier.json").read_text(encoding="utf-8"))
TYPES = {t["code"]: t for t in CLASSIFIER["types"]}
SERVICES = {s["code"] for s in CLASSIFIER["services"]}
FLAGS = {f["code"] for f in CLASSIFIER["flags"]}
TOPICS = {t["code"] for t in CALLER_TOPICS}
ERROR_CODES = {e["code"] for e in TYPICAL_ERRORS}
REASONS = {r["code"] for r in REJECT_REASONS}


def test_five_scenarios_of_each_kind() -> None:
    kinds = [parse_scenario(json.loads(f.read_text(encoding="utf-8"))).kind for f in FILES]
    assert kinds.count("card_response") == 5
    assert kinds.count("call_intake") == 5


@pytest.mark.parametrize("path", FILES, ids=[f.stem for f in FILES])
def test_scenario_is_consistent_with_the_reference_data(path: Path) -> None:
    scenario = parse_scenario(json.loads(path.read_text(encoding="utf-8")))
    assert scenario.ticket_ref, "каждый эталон построен из билета"
    if isinstance(scenario, CardResponseScenario):
        assert scenario.service in SERVICES
        assert scenario.card.incident_type in TYPES
        assert set(scenario.card.flags) <= FLAGS
        assert set(scenario.reference.critical_errors) <= ERROR_CODES
        chain = [s.status for s in scenario.reference.status_chain]
        assert chain and all(s in STATUSES for s in chain)
        if scenario.reference.decision == "accept":
            assert chain[0] == "accepted"
            assert scenario.reference.reject_reason is None
        else:
            assert chain[0] == "rejected"
            assert scenario.reference.reject_reason in REASONS
            assert scenario.reference.status_chain[0].comment_example
        for step in scenario.reference.status_chain:
            if STATUSES[step.status]["requires_comment"]:
                assert step.comment_example, step.status
            if STATUSES[step.status]["requires_order_number"]:
                assert step.order_number, step.status
    else:
        assert isinstance(scenario, CallIntakeScenario)
        ref = scenario.reference_card
        assert ref.incident_type in TYPES
        incident = TYPES[ref.incident_type]
        assert ref.signs_path == [
            s for s in (incident["sign1"], incident["sign2"], incident["sign3"]) if s
        ]
        assert set(ref.flags) <= FLAGS
        assert set(scenario.required_topics) <= TOPICS
        assert ref.expected_services == resolve_services(
            incident["service_rules"], [k for k, v in ref.flags.items() if v]
        )
        reply_topics = {r.topic for r in scenario.replies}
        assert set(scenario.required_topics) <= reply_topics, "на каждую тему есть реплика"
        assert scenario.caller.opening
        assert ref.description_keywords


@pytest.mark.parametrize("path", FILES, ids=[f.stem for f in FILES])
def test_perfect_attempt_scores_100(path: Path) -> None:
    body = json.loads(path.read_text(encoding="utf-8"))
    scenario = parse_scenario(body)
    if isinstance(scenario, CardResponseScenario):
        attempt = perfect_card_attempt(scenario)
    else:
        attempt = perfect_call_attempt(scenario)
    result = evaluate_sync(body, attempt.model_dump(mode="json"), grammar=grammar_ok())
    assert result.total == 100, result.to_dict()
    assert result.passed
    assert result.errors == []


def test_scenario_bodies_tolerate_editorial_keys_but_attempts_do_not() -> None:
    body = json.loads((SCENARIOS_DIR / "card_2-1_dubl.json").read_text(encoding="utf-8"))
    body["status"] = "review"
    body["note"] = "черновик"
    body["card"].pop("number", None)
    scenario = parse_scenario(body)
    assert scenario.kind == body["kind"]
    with pytest.raises(ValueError, match="Extra inputs"):
        CardResponseAttempt.model_validate({"issued_at": "2026-09-16T10:00:00Z", "typo": 1})
