"""«Звонки в службы»: did the dispatcher call the service officers the card requires and pass
the facts (issue #36, customer: «в рамках телефонии фокус на взаимодействии Б и В»).

The reference lists the calls (``reference.service_calls``: service, required facts, norm);
the attempt logs the calls made (``service_calls``: dialog of each). Each required call is an
equal share of the component: 30 % for reaching the officer, 50 % for the facts passed (their
share), 20 % for staying within the norm — but a call that passed none of the required facts
scores nothing at all, because reaching the officer and saying nothing leaves the service
knowing nothing.

The call to the dispatcher's own service is the call to the chief of its emergency squad
(#263) and is scored by the weights of the issue: the right people reached 25 % (the squad
chief; the head of the organization too when there were no vehicles), the facts passed 30 %,
the misheard house corrected 15 %, the order to go given and the squad's number taken 15 %,
within the norm 15 %. A call to the duty officer of another service keeps the shares above.

Facts are read from the dispatcher's turns by keywords (``detect_service_facts``); the address
counts only when the street and the house of the card are actually named, not just the word
«адрес», and the incident when the phrase shares a word with the card itself
(``incident_named``), because the keyword table is too narrow for how a dispatcher really
speaks.
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
    content_words,
    detect_service_facts,
    has_number,
    normalize_house,
    normalize_text,
    stem,
    stems,
    street_core,
    words_to_numbers,
)
from app.domain.evaluation.timing import seconds_between
from app.domain.reference_data import SERVICE_CALL_FACTS
from app.domain.scenarios import misheard, no_vehicles, officers

KEY = "service_call"
TITLE = "Звонки в службы"
REACHED_SHARE = 0.3
FACTS_SHARE = 0.5
NORM_SHARE = 0.2
STREET_THRESHOLD = 85.0
# The call to the squad chief of the own service (#263).
CHIEF_ROLES_SHARE = 0.25
CHIEF_FACTS_SHARE = 0.3
CHIEF_MISHEARD_SHARE = 0.15
CHIEF_ORDER_SHARE = 0.15
CHIEF_NORM_SHARE = 0.15
# The dispatcher sends the squad: «выезжайте», «направляйте бригаду», «высылайте наряд».
_ORDER = re.compile(
    r"\b(выезж\w*|выезд\w*|выехать|высыл\w*|вышлите|направ\w*|отправ\w*|выдвига\w*|"
    r"езжайте|поезжайте)\b"
)
FACT_TITLES = {f["code"]: f["title"] for f in SERVICE_CALL_FACTS}


def dispatcher_turns(dialog: list[DialogTurn]) -> list[DialogTurn]:
    return [t for t in dialog if t.role == "operator"]


# The part of a street word that stays the same in every case: «зелён|ый», «зелён|ому»,
# «молостов|ых», «молостов|ой». The ending takes at most three letters; a short word is kept.
STREET_ENDING = 3
STREET_STEM_MIN = 4


def _street_stem(word: str) -> str:
    if len(word) <= STREET_STEM_MIN:
        return word
    return word[: max(STREET_STEM_MIN, len(word) - STREET_ENDING)]


def street_said(street: str, normalized: str) -> bool:
    """Every word of the street's name is in the phrase in some case and order: «дом 17 по
    Зелёному проспекту» names «Зелёный проспект» (#263, the dispatcher speaks, not reads)."""
    words = normalized.split()
    return all(any(w.startswith(_street_stem(part)) for w in words) for part in street.split())


def address_named(card: Card, text: str) -> bool:
    """The street (by its core name) and the house of the card appear in the phrase."""
    normalized = normalize_text(text)
    if not normalized:
        return False
    street = street_core(card.address.street)
    house = normalize_house(card.address.house)
    if not street and not house:
        return False  # a card without a street and a house: nothing to name
    # The whole name, or the word that carries it: «Героев Панфиловцев» is «Панфиловцев» on
    # the phone. The leading word alone is not enough — «Красного» names no street.
    street_ok = not street or any(
        fuzz.partial_ratio(variant, normalized) >= STREET_THRESHOLD
        or street_said(variant, normalized)
        for variant in (street, street.split()[-1])
    )
    # The numerals are turned into digits over the whole phrase, not token by token: a house
    # said aloud may take several words («сто тринадцать»).
    spoken = words_to_numbers(normalized)
    house_ok = not house or any(normalize_house(tok) == house for tok in spoken.split())
    return bool(street_ok and house_ok)


# Parts of the address an operator of 112 may have got wrong (issue #35, ``InjectedError``).
ADDRESS_PARTS = ("street", "house", "building", "entrance")


def true_card(scenario: CardResponseScenario) -> Card:
    """The card as it really is: the address parts the operator of 112 got wrong replaced by
    the truth (the caller's own words in the description). A dispatcher who passes the right
    house is right, whatever the field shows (#263)."""
    fixes = {
        e.field.split(".", 1)[1]: e.correct_value
        for e in scenario.injected_errors
        if e.field.split(".", 1)[-1] in ADDRESS_PARTS and e.field.startswith("address.")
    }
    if not fixes:
        return scenario.card
    address = scenario.card.address.model_copy(update=fixes)
    return scenario.card.model_copy(update={"address": address})


def wrong_address_told(scenario: CardResponseScenario, text: str) -> bool:
    """The phrase passes the operator's mistake in the address, not the truth."""
    truth = true_card(scenario)
    if truth is scenario.card:
        return False
    return address_named(scenario.card, text) and not address_named(truth, text)


def _evidence_of_another_fact(word: str) -> bool:
    """The word is a keyword of some other fact, so it says nothing about the incident."""
    facts = detect_service_facts(word)
    return bool(facts) and "incident_type" not in facts


def incident_words(card: Card) -> set[str]:
    """What this card's incident sounds like: the signs of the survey card and the caller's
    own description. The address of the card and the words that are evidence of another fact
    are dropped, so naming only the street or only «пострадавших нет» is not naming the
    incident."""
    said = [*card.signs, card.description]
    address = " ".join(
        part or "" for part in (card.address.street, card.address.city, card.address.district)
    )
    return {
        stem(word)
        for text in said
        for word in content_words(text)
        if not _evidence_of_another_fact(word)
    } - stems(address, min_length=4)


def incident_named(card: Card, text: str) -> bool:
    """The phrase names what happened: either by a keyword of the general table, or by a word
    the card itself uses. The keyword table alone is too narrow — it knows «течь» but not the
    «течёт стояк» a dispatcher says, and every new scenario brings its own wording."""
    if "incident_type" in detect_service_facts(text):
        return True
    said = {stem(w) for w in content_words(text) if not _evidence_of_another_fact(w)}
    return bool(said & incident_words(card))


# Speech recognition cuts a phrase at a pause: «Улица Лётчика Грицевца.» «83.» (stand
# 17.10.2026). The address may take this many phrases of the dispatcher in a row.
ADDRESS_PHRASES = 3


def address_in_phrases(card: Card, texts: list[str]) -> bool:
    """The address named across a few phrases said in a row, not only within one."""
    return any(
        address_named(card, " ".join(texts[start : start + size]))
        for size in range(1, ADDRESS_PHRASES + 1)
        for start in range(len(texts) - size + 1)
    )


# What the squad chief needs to go (decision of 17.10.2026): where and what happened. The
# rest he asks or the dispatcher adds; the evaluation counts it.
TO_GO = ("address", "incident_type")


def missing_to_go(scenario: CardResponseScenario, texts: list[str]) -> list[str]:
    """What the squad chief still lacks to go, from the dispatcher's phrases: the address (the
    true one, or the field's by mistake — the squad goes there all the same) and the
    incident."""
    truth = true_card(scenario)
    missing: list[str] = []
    if not (address_in_phrases(truth, texts) or address_in_phrases(scenario.card, texts)):
        missing.append("address")
    if not any(incident_named(truth, text) for text in texts):
        missing.append("incident_type")
    return missing


# The cloud's form after a call to the squad chief (#263, decision of 17.10.2026): the facts
# are read by meaning; the house named is compared with the card here.
ANALYSIS_FLAGS = {
    "incident_named": "incident_type",
    "injured_named": "injured",
    "access_named": "access",
}


def facts_from_analysis(scenario: CardResponseScenario, analysis: dict) -> list[str]:
    """Facts the form says the dispatcher passed; the address counts when the house named is
    the true one (a house of the operator's mistake is no address passed)."""
    truth = true_card(scenario).address
    found = {fact for key, fact in ANALYSIS_FLAGS.items() if analysis.get(key) is True}
    house = normalize_house(str(analysis.get("house_named") or ""))
    if analysis.get("address_named") is True and (
        not truth.house or house == normalize_house(truth.house)
    ):
        found.add("address")
    return [f["code"] for f in SERVICE_CALL_FACTS if f["code"] in found]


def call_facts(scenario: CardResponseScenario, log: ServiceCallLog) -> list[str]:
    """What the dispatcher passed in a call: by the cloud's form when there is one, else by
    the words of the transcript."""
    if log.analysis:
        return facts_from_analysis(scenario, log.analysis)
    return facts_from_dialog(true_card(scenario), log.dialog)


def form_says(logs: list[ServiceCallLog], key: str) -> bool:
    """Some form of these calls answers ``key`` with yes."""
    return any((log.analysis or {}).get(key) is True for log in logs)


def facts_from_dialog(card: Card, dialog: list[DialogTurn]) -> list[str]:
    """Facts the dispatcher passed in the call, in the order of ``SERVICE_CALL_FACTS``."""
    found: set[str] = set()
    said = [turn.text for turn in dispatcher_turns(dialog)]
    if address_in_phrases(card, said):
        found.add("address")
    for turn in dispatcher_turns(dialog):
        detected = set(detect_service_facts(turn.text)) | {
            t for t in turn.topics if t in FACT_TITLES
        }
        if "address" in detected and not address_named(card, turn.text):
            detected.discard("address")
        elif "address" not in detected and address_named(card, turn.text):
            detected.add("address")
        if "incident_type" not in detected and incident_named(card, turn.text):
            detected.add("incident_type")
        if "order_number" in detected and not has_number(turn.text):
            detected.discard("order_number")  # «наряд» without a number is not a number
        found |= detected
    return [f["code"] for f in SERVICE_CALL_FACTS if f["code"] in found]


def gives_order(text: str) -> bool:
    """The phrase sends the squad out (the dispatcher's order, #263)."""
    return bool(_ORDER.search(normalize_text(text)))


def _as_rows(attempt: CardResponseAttempt) -> list[dict]:
    """The calls as the dialog layer stores them, for the shared readers of the truth."""
    return [
        {
            "id": str(i),
            "kind": c.kind,
            "role": c.role,
            "dialog": [t.model_dump() for t in c.dialog],
            "ended_at": c.ended_at,
        }
        for i, c in enumerate(attempt.service_calls)
    ]


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
    # The call to the squad chief only (#263); ``None`` for another service's duty officer.
    order_given: bool | None = None
    order_number_taken: bool | None = None
    misheard_fixed: bool | None = None
    head_told: bool | None = None


def outgoing_calls(attempt: CardResponseAttempt) -> list[ServiceCallLog]:
    """The dispatcher's own calls; the squad's reports are scored elsewhere."""
    return [c for c in attempt.service_calls if c.kind == "outgoing"]


def outcomes(scenario: CardResponseScenario, attempt: CardResponseAttempt) -> list[CallOutcome]:
    logs_by_service: dict[str, list[ServiceCallLog]] = {}
    for log in outgoing_calls(attempt):
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
            passed = call_facts(scenario, log)
            required = list(ref.required_facts)
            passed_required = [f for f in passed if f in required]
            missing = [f for f in required if f not in passed]
            seconds = call_seconds(log)
            within = seconds is not None and seconds <= ref.norm_seconds
            facts_fraction = (len(required) - len(missing)) / len(required) if required else 1.0
            if required and not passed_required:
                # Reaching the officer and saying nothing is not a call: the service learns
                # nothing, and hanging up at once always fits the norm (as with an empty card
                # saved fast, issue #70).
                fraction = 0.0
            else:
                fraction = (
                    REACHED_SHARE + FACTS_SHARE * facts_fraction + (NORM_SHARE if within else 0)
                )
            outcome = CallOutcome(ref, log, passed_required, missing, seconds, within, fraction)
            if best is None or outcome.fraction > best.fraction:
                best = outcome
        assert best is not None
        if ref.service == scenario.service:
            best = _chief_outcome(scenario, attempt, best, candidates)
        result.append(best)
    return result


def _chief_outcome(
    scenario: CardResponseScenario,
    attempt: CardResponseAttempt,
    best: CallOutcome,
    candidates: list[ServiceCallLog],
) -> CallOutcome:
    """The call to the squad chief by the weights of #263. Said over several calls, the order
    and the number count wherever they were said."""
    rows = _as_rows(attempt)
    said = [t.text for log in candidates for t in log.dialog if t.role == "operator"]
    heard = [t.text for log in candidates for t in log.dialog if t.role == "caller"]
    order = best.ref.order_number or officers.order_number(scenario, best.ref.service)
    order_given = any(gives_order(text) for text in said) or form_says(candidates, "order_given")
    number_taken = any(order in text for text in heard) or any(
        (e.order_number or "").strip() for e in attempt.status_log
    )
    mistake = misheard.state(rows)
    fixed = mistake is None or mistake.fixed or form_says(candidates, "house_corrected")
    reached = 1
    required = 1
    head_told: bool | None = None
    if no_vehicles.announced(rows):
        required += 1
        head_told = no_vehicles.resolving_call(rows) is not None
        reached += int(head_told)
    required_facts = list(best.ref.required_facts)
    facts_fraction = (
        (len(required_facts) - len(best.facts_missing)) / len(required_facts)
        if required_facts
        else 1.0
    )
    if required_facts and not best.facts_passed:
        fraction = 0.0  # reaching him and saying nothing sends nobody anywhere (issue #127)
    else:
        fraction = (
            CHIEF_ROLES_SHARE * reached / required
            + CHIEF_FACTS_SHARE * facts_fraction
            + CHIEF_MISHEARD_SHARE * int(fixed)
            + CHIEF_ORDER_SHARE * (int(order_given) + int(number_taken)) / 2
            + (CHIEF_NORM_SHARE if best.within_norm else 0.0)
        )
    return CallOutcome(
        best.ref,
        best.log,
        best.facts_passed,
        best.facts_missing,
        best.seconds,
        best.within_norm,
        fraction,
        order_given=order_given,
        order_number_taken=number_taken,
        misheard_fixed=fixed,
        head_told=head_told,
    )


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
            "order_given": c.order_given,
            "order_number_taken": c.order_number_taken,
            "misheard_fixed": c.misheard_fixed,
            "head_told": c.head_told,
        }
        for c in calls
    ]
    return Component(KEY, TITLE, scale(max_points, fraction), max_points, items=items)


def fact_title(code: str) -> str:
    return FACT_TITLES.get(code, code)
