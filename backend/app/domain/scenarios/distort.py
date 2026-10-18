"""Planted operator mistakes for generated cards (issues #35, #265).

The dispatcher of a city service receives the card ready from the 112 operator and checks it,
because the operator may have erred. A generated ``card_response`` scenario gets exactly as many
mistakes as its difficulty asks: none at 1, one at 2, two at 3. Every mistake is visible: the
description tells the true value in a plain sentence («Заявитель уточнил, что пострадавших
нет»), unless it already does, while the field shows the wrong one, so an attentive dispatcher
can always find it. Small mistakes are flagged and the work goes on with the true value; a card
sent to the wrong service (``wrong_service``) is refused with «Не принята» and the reason. A
card planned as sent to the wrong service (the trainee's own service for an incident it does not
serve, ``sent_to_wrong_service``) counts that as one of its mistakes. The card keeps the wrong
values, the truth goes to ``injected_errors``. The choice is deterministic per scenario (seeded
by the ticket and title), so regeneration gives the same card.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Mapping, Sequence

from rapidfuzz import fuzz

from app.domain.evaluation.data_check import (
    WRONG_SERVICE_ANSWER,
    WRONG_SERVICE_FIELD,
    WRONG_SERVICE_REASON,
)
from app.domain.evaluation.text import normalize_house, normalize_text
from app.domain.scenarios.template import ServiceInfo, card_reference

MISTAKES_BY_DIFFICULTY: dict[int, int] = {1: 0, 2: 1, 3: 2}
STREET_SIMILARITY = (60.0, 92.0)  # a look-alike street: close, but not the same
NEIGHBOUR_HOUSE_STEPS = (1, 2, -1, -2)
INJURED_ABSENT = ("пострадавших нет", "без пострадавших", "никто не пострадал")
# How the description tells the truth: different openers within one card, so two mistakes do
# not read as one pattern repeated.
TRUTH_SENTENCES = (
    "Заявитель уточнил, что {}.",
    "По словам заявителя, {}.",
    "Звонивший сообщил, что {}.",
)
TRUTH_KEY = "_truth"  # the clause a kind returns with its error; removed before storing
# Words of a street name that only tell its type; the rest is matched by stems.
STREET_TYPES = frozenset(
    {
        "улица",
        "проспект",
        "переулок",
        "бульвар",
        "шоссе",
        "проезд",
        "площадь",
        "набережная",
        "тупик",
        "аллея",
        "линия",
        "квартал",
        "микрорайон",
    }
)
STEM_LENGTH = 5  # «Маршала Тимошенко» in «улице Маршала Тимошенко», «Свободы» in «Свободе»
# Kinds that change the same part of the card: one of a group per card (the address sentence
# of one would otherwise repeat the wrong value of the other).
SAME_PART = {"house": "address", "street": "address"}
# Kind of an already planted mistake by its field.
KIND_BY_FIELD = {
    "address.house": "house",
    "address.street": "street",
    "incident_type": "incident_type",
    "flags.injured": "injured",
    WRONG_SERVICE_FIELD: "wrong_service",
}
REFUSED_LABEL = "Не принята: не в компетенции службы"
_HOUSE_NUMBER = re.compile(r"^(\d+)(.*)$")

Distortion = Callable[[dict, random.Random, "Context"], dict | None]


class Context:
    def __init__(
        self,
        type_rows: Sequence[Mapping],
        catalogue: Mapping[str, ServiceInfo],
        streets: Sequence[str] = (),
        services: Sequence[str] = (),
    ) -> None:
        self.type_rows = type_rows
        self.catalogue = catalogue
        self.streets = streets
        # Services a wrong card may be sent to (the lesson's ones); empty = any on АРМ-112.
        self.services = services


def inject_by_difficulty(
    body: dict,
    *,
    type_rows: Sequence[Mapping],
    catalogue: Mapping[str, ServiceInfo],
    streets: Sequence[str] = (),
    services: Sequence[str] = (),
    prefer: Sequence[str] = (),
) -> dict:
    """Returns the body with exactly as many mistakes as its difficulty asks (none at 1),
    counting those already planted. ``prefer`` lists kinds to try first (the trainee's weak
    spots), in order."""
    if body.get("kind") != "card_response":
        return body
    count = MISTAKES_BY_DIFFICULTY.get(int(body.get("difficulty") or 1), 0)
    count -= len(body.get("injected_errors") or [])
    if count <= 0:
        return body
    rng = random.Random(  # noqa: S311 - a stable choice per scenario, not security
        f"{body.get('ticket_ref')}|{body.get('title')}|{body.get('card', {}).get('number')}"
    )
    ctx = Context(type_rows, catalogue, streets, services)
    return inject(body, count=count, rng=rng, ctx=ctx, prefer=prefer)


def inject(
    body: dict,
    *,
    count: int,
    rng: random.Random,
    ctx: Context,
    prefer: Sequence[str] = (),
) -> dict:
    """Plants up to ``count`` more mistakes of kinds the card does not have yet; kinds that do
    not fit the card are skipped (no house → no neighbour house)."""
    result = _copy_body(body)
    result["injected_errors"] = list(body.get("injected_errors") or [])
    others = [k for k in DISTORTIONS if k not in prefer]
    rng.shuffle(others)
    kinds = [k for k in prefer if k in DISTORTIONS] + others
    planted = 0
    used_parts = {
        SAME_PART.get(kind, kind)
        for kind in (KIND_BY_FIELD.get(str(e.get("field"))) for e in result["injected_errors"])
        if kind
    }
    truths: list[str] = []
    for kind in kinds:
        if planted >= count:
            break
        part = SAME_PART.get(kind, kind)
        if part in used_parts:
            continue
        trial = _copy_body(result)
        error = DISTORTIONS[kind](trial, rng, ctx)
        if error is None:
            continue
        used_parts.add(part)
        truth = error.pop(TRUTH_KEY, None)
        if truth:
            truths.append(truth)
        trial["injected_errors"] = [*result["injected_errors"], error]
        result = trial
        planted += 1
    if not planted:
        return body
    _tell_truth(result["card"], truths, rng)
    return result


def _copy_body(body: Mapping) -> dict:
    copy = dict(body)
    card = body.get("card") or {}
    copy["card"] = {
        **card,
        "address": dict(card.get("address") or {}),
        "flags": dict(card.get("flags") or {}),
        "notified": [dict(n) for n in card.get("notified") or []],
    }
    return copy


# --- kinds -----------------------------------------------------------------------------------


def neighbour_house(body: dict, rng: random.Random, ctx: Context) -> dict | None:
    """«д. 44» instead of «д. 42»; the description names the right house."""
    card = body["card"]
    house = str(card["address"].get("house") or "")
    street = card["address"].get("street")
    match = _HOUSE_NUMBER.match(house.strip())
    if not match or not street:
        return None
    number, suffix = int(match.group(1)), match.group(2)
    neighbour = number + rng.choice(NEIGHBOUR_HOUSE_STEPS)
    if neighbour <= 0:
        neighbour = number + 1
    wrong = f"{neighbour}{suffix}"
    if normalize_house(wrong) == normalize_house(house):
        return None
    card["address"]["house"] = wrong
    return {
        "field": "address.house",
        "wrong_value": wrong,
        "correct_value": house,
        "hint_level": 2,
        TRUTH_KEY: _address_truth(card, str(street), house, about="house"),
    }


def lookalike_street(body: dict, rng: random.Random, ctx: Context) -> dict | None:
    """A street with a similar name (from the street list); the description names the right
    one. Skipped when the card has no street or no street list is available."""
    card = body["card"]
    street = str(card["address"].get("street") or "")
    if not street or not ctx.streets:
        return None
    lo, hi = STREET_SIMILARITY
    candidates = [
        s
        for s in ctx.streets
        if s != street and lo <= fuzz.ratio(normalize_text(s), normalize_text(street)) <= hi
    ]
    if not candidates:
        return None
    wrong = rng.choice(sorted(candidates))
    card["address"]["street"] = wrong
    house = card["address"].get("house")
    return {
        "field": "address.street",
        "wrong_value": wrong,
        "correct_value": street,
        "hint_level": 3,
        TRUTH_KEY: _address_truth(card, street, str(house) if house else None, about="street"),
    }


def _branch(code: str) -> str:
    """The classifier branch of a type: group, first and second sign («3.2.2» for «Взрыв МЦК»
    and its kinds «… (поезд)», «… (станция, тоннель)»)."""
    return ".".join(code.split(".")[:3])


def sibling_incident_type(body: dict, rng: random.Random, ctx: Context) -> dict | None:
    """A neighbouring type of the classifier (same group, other final title); the description
    tells what really happened. Never a type of the same branch: a kind for its parent or the
    other way round tells nothing a person can catch (stand 18.10.2026: «Взрыв МЦК (станция,
    тоннель)» for «Взрыв МЦК»)."""
    card = body["card"]
    code = str(card.get("incident_type") or "")
    rows = {str(r["code"]): r for r in ctx.type_rows}
    row = rows.get(code)
    if row is None or not row.get("final_title"):
        return None
    group = str(row.get("group_code") or code.split(".")[0])
    siblings = [
        r
        for c, r in rows.items()
        if _branch(c) != _branch(code)
        and str(r.get("group_code")) == group
        and r.get("final_title")
        and r.get("final_title") != row.get("final_title")
    ]
    if not siblings:
        return None
    wrong_row = rng.choice(sorted(siblings, key=lambda r: str(r["code"])))
    card["incident_type"] = str(wrong_row["code"])
    card["signs"] = [wrong_row[k] for k in ("sign1", "sign2", "sign3") if wrong_row.get(k)]
    return {
        "field": "incident_type",
        "wrong_value": str(wrong_row["code"]),
        "correct_value": code,
        "hint_level": 2,
        "wrong_label": wrong_row.get("final_title"),
        "correct_label": row.get("final_title"),
        TRUTH_KEY: f"это {_spoken_title(str(row['final_title']))}",
    }


def flipped_injured(body: dict, rng: random.Random, ctx: Context) -> dict | None:
    """«Пострадавшие: да» while the caller says there are none (or the other way round).
    Skipped when the description already contradicts the true flag."""
    card = body["card"]
    injured = bool(card["flags"].get("injured"))
    description = normalize_text(card.get("description") or "")
    says_absent = any(p in description for p in INJURED_ABSENT)
    says_present = "пострадавш" in description and not says_absent
    if (injured and says_absent) or (not injured and says_present):
        return None
    card["flags"]["injured"] = not injured
    already_told = says_present if injured else says_absent
    truth = "есть пострадавшие" if injured else "пострадавших нет"
    return {
        "field": "flags.injured",
        "wrong_value": "false" if injured else "true",
        "correct_value": "true" if injured else "false",
        "hint_level": 1,
        "wrong_label": "нет" if injured else "да",
        "correct_label": "да" if injured else "нет",
        TRUTH_KEY: None if already_told else truth,
    }


def wrong_service(body: dict, rng: random.Random, ctx: Context) -> dict | None:
    """The card is sent to a service the incident does not concern: the right answer is
    «Не принята» with «не в компетенции службы». The incident in the description is the
    evidence; the reference becomes the refusal."""
    if (body.get("reference") or {}).get("decision") != "accept":
        return None
    card = body["card"]
    own = str(body.get("service") or "")
    expected = [str(n.get("service")) for n in card["notified"]]
    pool = ctx.services or list(ctx.catalogue)
    candidates = sorted(
        code
        for code in pool
        if code in ctx.catalogue
        and ctx.catalogue[code].via_arm112
        and code != own
        and code not in expected
    )
    if not candidates:
        return None
    code = rng.choice(candidates)
    body["service"] = code
    for entry in card["notified"]:
        if entry.get("service") == own:
            entry["status"] = "Получена службой"
    card["notified"].append({"service": code, "status": "Добавлена"})
    body["reference"] = card_reference(region=None, service=code, expected=expected)
    return {
        "field": WRONG_SERVICE_FIELD,
        "wrong_value": code,
        "correct_value": WRONG_SERVICE_ANSWER,
        "hint_level": 1,
        "wrong_label": ctx.catalogue[code].title or code,
        "correct_label": REFUSED_LABEL,
    }


def sent_to_wrong_service(body: dict, catalogue: Mapping[str, ServiceInfo]) -> dict:
    """Marks a card whose own service does not serve the incident (planned so) as the wrong
    service mistake: the reference is already the refusal «не в компетенции службы»."""
    reference = body.get("reference") or {}
    if reference.get("decision") != "reject" or reference.get("reject_reason") != (
        WRONG_SERVICE_REASON
    ):
        return body
    service = str(body.get("service") or "")
    info = catalogue.get(service)
    error = {
        "field": WRONG_SERVICE_FIELD,
        "wrong_value": service,
        "correct_value": WRONG_SERVICE_ANSWER,
        "hint_level": 1,
        "wrong_label": (info.title if info else None) or service,
        "correct_label": REFUSED_LABEL,
    }
    return {**body, "injected_errors": [*(body.get("injected_errors") or []), error]}


DISTORTIONS: dict[str, Distortion] = {
    "house": neighbour_house,
    "street": lookalike_street,
    "incident_type": sibling_incident_type,
    "injured": flipped_injured,
    "wrong_service": wrong_service,
}


def _address_truth(card: Mapping, street: str, house: str | None, *, about: str) -> str | None:
    """The true address as a clause, unless the description already names the part that is
    wrong in the card (the street there may be declined, the house number is not)."""
    description = normalize_text(card.get("description") or "")
    if about == "house" and house and f"дом {normalize_house(house)}" in description:
        return None
    if about == "street" and _street_named(street, description):
        return None
    return f"точный адрес — {street}{', дом ' + house if house else ''}"


def _street_named(street: str, description: str) -> bool:
    """Whether the description names the street, in any case form («улице Маршала
    Тимошенко» for «улица Маршала Тимошенко»): every word but the street type, by its stem."""
    words = [w for w in normalize_text(street).split() if w not in STREET_TYPES]
    return bool(words) and all(w[:STEM_LENGTH] in description for w in words)


def _spoken_title(title: str) -> str:
    """«пожар: опора контактной сети» → «пожар (опора контактной сети)»."""
    head, sep, tail = title.partition(":")
    spoken = f"{head.strip()} ({tail.strip()})" if sep and tail.strip() else title.strip()
    return spoken[:1].lower() + spoken[1:]


def _tell_truth(card: dict, truths: Sequence[str], rng: random.Random) -> None:
    """Adds the true values to the description, one plain sentence each, openers not repeated."""
    if not truths:
        return
    openers = list(TRUTH_SENTENCES)
    rng.shuffle(openers)
    text = (card.get("description") or "").rstrip()
    if text and not text.endswith((".", "!", "?")):
        text += "."
    sentences = [openers[i % len(openers)].format(t) for i, t in enumerate(truths)]
    card["description"] = " ".join([text, *sentences]).strip()
