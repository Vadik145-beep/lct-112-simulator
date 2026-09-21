"""«Звонки в службы»: did the dispatcher call the service officers the card requires and pass
the facts (issue #36, customer: «в рамках телефонии фокус на взаимодействии Б и В»).

The reference lists the calls (``reference.service_calls``: service, required facts, norm);
the attempt logs the calls made (``service_calls``: dialog of each). Each required call is an
equal share of the component: 30 % for reaching the officer, 50 % for the facts passed (their
share), 20 % for staying within the norm. Facts are read from the dispatcher's turns by
keywords (``detect_service_facts``); the address counts only when the street and the house of
the card are actually named, not just the word «адрес».
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.domain.evaluation.result import Component, scale
from app.domain.evaluation.schemas import (
    Card,
    CardResponseAttempt,
    CardResponseScenario,
    DialogTurn,
    ServiceCallLog,
    ServiceCallRef,
)
from app.domain.evaluation.text import (
    detect_service_facts,
    normalize_house,
    normalize_text,
    street_core,
)
from app.domain.evaluation.timing import seconds_between
from app.domain.reference_data import SERVICE_CALL_FACTS

KEY = "service_call"
TITLE = "Звонки в службы"
REACHED_SHARE = 0.3
FACTS_SHARE = 0.5
NORM_SHARE = 0.2
STREET_THRESHOLD = 85.0
FACT_TITLES = {f["code"]: f["title"] for f in SERVICE_CALL_FACTS}
_NUMBER = re.compile(r"\d")


def dispatcher_turns(dialog: list[DialogTurn]) -> list[DialogTurn]:
    return [t for t in dialog if t.role == "operator"]


def address_named(card: Card, text: str) -> bool:
    """The street (by its core name) and the house of the card appear in the phrase."""
    normalized = normalize_text(text)
    if not normalized:
        return False
    street = street_core(card.address.street)
    house = normalize_house(card.address.house)
    if not street and not house:
        return False  # a card without a street and a house: nothing to name
    street_ok = not street or fuzz.partial_ratio(street, normalized) >= STREET_THRESHOLD
    house_ok = not house or any(normalize_house(tok) == house for tok in normalized.split())
    return bool(street_ok and house_ok)


def facts_from_dialog(card: Card, dialog: list[DialogTurn]) -> list[str]:
    """Facts the dispatcher passed in the call, in the order of ``SERVICE_CALL_FACTS``."""
    found: set[str] = set()
    for turn in dispatcher_turns(dialog):
        detected = set(detect_service_facts(turn.text)) | {
            t for t in turn.topics if t in FACT_TITLES
        }
        if "address" in detected and not address_named(card, turn.text):
            detected.discard("address")
        elif "address" not in detected and address_named(card, turn.text):
            detected.add("address")
        if "order_number" in detected and not _NUMBER.search(turn.text):
            detected.discard("order_number")  # «наряд» without a number is not a number
        found |= detected
    return [f["code"] for f in SERVICE_CALL_FACTS if f["code"] in found]


def call_seconds(log: ServiceCallLog) -> float | None:
    if log.ended_at is None:
        last = [t.at for t in log.dialog if t.at is not None]
        if not last:
            return None
        return seconds_between(log.started_at, max(last))
    return seconds_between(log.started_at, log.ended_at)


@dataclass
class CallOutcome:
    ref: ServiceCallRef
    log: ServiceCallLog | None
    facts_passed: list[str]
    facts_missing: list[str]
    seconds: float | None
    within_norm: bool
    fraction: float


def outcomes(scenario: CardResponseScenario, attempt: CardResponseAttempt) -> list[CallOutcome]:
    logs_by_service: dict[str, list[ServiceCallLog]] = {}
    for log in attempt.service_calls:
        logs_by_service.setdefault(log.service, []).append(log)
    result: list[CallOutcome] = []
    for ref in scenario.reference.service_calls:
        candidates = [c for c in logs_by_service.get(ref.service, []) if c.answered]
        if not candidates:
            result.append(CallOutcome(ref, None, [], list(ref.required_facts), None, False, 0.0))
            continue
        # The best of several calls to the same service counts (a second call may fix a miss).
        best: CallOutcome | None = None
        for log in candidates:
            passed = facts_from_dialog(scenario.card, log.dialog)
            required = list(ref.required_facts)
            missing = [f for f in required if f not in passed]
            seconds = call_seconds(log)
            within = seconds is not None and seconds <= ref.norm_seconds
            facts_fraction = (len(required) - len(missing)) / len(required) if required else 1.0
            fraction = REACHED_SHARE + FACTS_SHARE * facts_fraction + (NORM_SHARE if within else 0)
            outcome = CallOutcome(
                ref, log, [f for f in passed if f in required], missing, seconds, within, fraction
            )
            if best is None or outcome.fraction > best.fraction:
                best = outcome
        assert best is not None
        result.append(best)
    return result


def service_call_component(
    scenario: CardResponseScenario, attempt: CardResponseAttempt, max_points: int
) -> Component:
    calls = outcomes(scenario, attempt)
    if not calls:
        return Component(KEY, TITLE, float(max_points), max_points)
    fraction = sum(c.fraction for c in calls) / len(calls)
    items = [
        {
            "service": c.ref.service,
            "called": c.log is not None,
            "seconds": round(c.seconds, 1) if c.seconds is not None else None,
            "norm_seconds": c.ref.norm_seconds,
            "within_norm": c.within_norm,
            "required_facts": list(c.ref.required_facts),
            "facts_passed": c.facts_passed,
            "facts_missing": c.facts_missing,
            "fraction": round(c.fraction, 2),
        }
        for c in calls
    ]
    return Component(KEY, TITLE, scale(max_points, fraction), max_points, items=items)


def fact_title(code: str) -> str:
    return FACT_TITLES.get(code, code)
