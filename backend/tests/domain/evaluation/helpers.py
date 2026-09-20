"""Builders shared by the evaluation tests: reference scenarios from data/seed/scenarios and
attempts that follow the reference exactly (a «perfect» attempt scores 100)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.evaluation.schemas import (
    CallIntakeAttempt,
    CallIntakeScenario,
    CardResponseAttempt,
    CardResponseScenario,
    parse_scenario,
)
from app.providers.grammar import GrammarMatch, GrammarResult

SCENARIOS_DIR = Path(__file__).resolve().parents[4] / "data" / "seed" / "scenarios"
T0 = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)

# Timeline of a good card response: statuses set at these seconds after «Добавлена».
STEP_SECONDS = {
    "received": 5,
    "accepted": 20,
    "rejected": 20,
    "response_started": 120,
    "arrived": 600,
    "works_started": 700,
    "works_done": 1500,
    "works_refused": 300,
}


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def load(name: str) -> dict:
    return json.loads((SCENARIOS_DIR / f"{name}.json").read_text(encoding="utf-8"))


def card_scenario(name: str = "card_2-1_zadymlenie_musoroprovoda") -> CardResponseScenario:
    scenario = parse_scenario(load(name))
    assert isinstance(scenario, CardResponseScenario)
    return scenario


def call_scenario(name: str = "call_2-1_zadymlenie_musoroprovoda") -> CallIntakeScenario:
    scenario = parse_scenario(load(name))
    assert isinstance(scenario, CallIntakeScenario)
    return scenario


def status_log(*steps: dict) -> list[dict]:
    """Entries with timestamps from STEP_SECONDS unless ``at`` is given."""
    log = []
    for step in steps:
        entry = dict(step)
        entry.setdefault("at", at(STEP_SECONDS[entry["status"]]))
        log.append(entry)
    return log


def perfect_card_attempt(scenario: CardResponseScenario) -> CardResponseAttempt:
    steps = [{"status": "received"}]
    for ref in scenario.reference.status_chain:
        step: dict = {"status": ref.status}
        if ref.comment_example:
            step["comment"] = ref.comment_example
        if ref.order_number:
            step["order_number"] = "14-217"
        if ref.status in {"rejected", "works_refused"}:
            step["reject_reason"] = scenario.reference.reject_reason
        steps.append(step)
    # A perfect dispatcher also finds every planted operator mistake (issue #35).
    flags = [
        {"field": e.field, "corrected_value": e.correct_value, "at": at(10 + i)}
        for i, e in enumerate(scenario.injected_errors)
    ]
    return CardResponseAttempt(
        issued_at=at(0), received_at=at(5), status_log=status_log(*steps), flagged_fields=flags
    )


def card_attempt(*steps: dict, issued_at: datetime | None = None) -> CardResponseAttempt:
    return CardResponseAttempt(
        issued_at=issued_at or at(0), received_at=at(5), status_log=status_log(*steps)
    )


def perfect_call_attempt(
    scenario: CallIntakeScenario, seconds: float | None = None
) -> CallIntakeAttempt:
    ref = scenario.reference_card
    if seconds is None:
        seconds = scenario.norm_seconds - 10
    dialog = [{"role": "caller", "text": scenario.caller.opening, "topics": ["what_happened"]}]
    for topic in scenario.required_topics:
        dialog.append({"role": "operator", "text": f"Вопрос по теме {topic}", "topics": [topic]})
        reply = next((r for r in scenario.replies if r.topic == topic), None)
        if reply:
            dialog.append({"role": "caller", "text": reply.text, "topics": [topic]})
    return CallIntakeAttempt(
        answered_at=at(0),
        submitted_at=at(seconds),
        card={
            "incident_type": ref.incident_type,
            "signs_path": list(ref.signs_path),
            "flags": dict(ref.flags),
            "services": list(ref.expected_services),
            "address": ref.address.model_dump(),
            "caller": ref.caller.model_dump(),
            "description": ref.description or " ".join(ref.description_keywords),
        },
        dialog=dialog,
        call_dropped_marked=scenario.caller.drops_call,
        no_contact_marked=scenario.caller.no_contact,
    )


def grammar_ok() -> GrammarResult:
    return GrammarResult([], "languagetool")


def grammar_errors(n: int) -> GrammarResult:
    matches = [
        GrammarMatch(offset=i, length=1, message="ошибка", rule_id="R", category="TYPOS")
        for i in range(n)
    ]
    return GrammarResult(matches, "languagetool")


def grammar_down() -> GrammarResult:
    return GrammarResult([], "unavailable")
