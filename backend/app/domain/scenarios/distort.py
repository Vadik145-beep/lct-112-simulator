"""Planted operator mistakes for generated cards (issue #35).

The dispatcher of a city service receives the card ready from the 112 operator and checks it,
because the operator may have erred. A generated ``card_response`` scenario therefore gets a
mistake with a probability set by its difficulty: none at 1, 30 % at 2, 50 % at 3 (a difficulty 3
card sometimes carries two). The card keeps the wrong values, the truth goes to
``injected_errors``; the description «со слов заявителя» always lets an attentive dispatcher
notice the mistake. The choice is deterministic per scenario (seeded by the ticket and title), so
regeneration gives the same card.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Mapping, Sequence

from rapidfuzz import fuzz

from app.domain.evaluation.text import normalize_house, normalize_text
from app.domain.scenarios.template import ServiceInfo

SHARE_BY_DIFFICULTY: dict[int, float] = {1: 0.0, 2: 0.3, 3: 0.5}
SECOND_ERROR_CHANCE = 0.4  # at difficulty 3
STREET_SIMILARITY = (60.0, 92.0)  # a look-alike street: close, but not the same
NEIGHBOUR_HOUSE_STEPS = (1, 2, -1, -2)
INJURED_ABSENT = ("пострадавших нет", "без пострадавших", "никто не пострадал")
_HOUSE_NUMBER = re.compile(r"^(\d+)(.*)$")

Distortion = Callable[[dict, random.Random, "Context"], dict | None]


class Context:
    def __init__(
        self,
        type_rows: Sequence[Mapping],
        catalogue: Mapping[str, ServiceInfo],
        streets: Sequence[str] = (),
    ) -> None:
        self.type_rows = type_rows
        self.catalogue = catalogue
        self.streets = streets


def inject_by_difficulty(
    body: dict,
    *,
    type_rows: Sequence[Mapping],
    catalogue: Mapping[str, ServiceInfo],
    streets: Sequence[str] = (),
) -> dict:
    """Returns the body with planted mistakes according to its difficulty (or unchanged)."""
    if body.get("kind") != "card_response" or body.get("injected_errors"):
        return body
    difficulty = int(body.get("difficulty") or 1)
    share = SHARE_BY_DIFFICULTY.get(difficulty, 0.0)
    rng = random.Random(  # noqa: S311 - a stable choice per scenario, not security
        f"{body.get('ticket_ref')}|{body.get('title')}|{body.get('card', {}).get('number')}"
    )
    if rng.random() >= share:
        return body
    count = 2 if difficulty >= 3 and rng.random() < SECOND_ERROR_CHANCE else 1
    return inject(body, count=count, rng=rng, ctx=Context(type_rows, catalogue, streets))


def inject(body: dict, *, count: int, rng: random.Random, ctx: Context) -> dict:
    """Plants up to ``count`` mistakes of different kinds; kinds that do not fit the card are
    skipped (no house → no neighbour house)."""
    result = dict(body)
    result["card"] = _copy_card(body.get("card") or {})
    result["injected_errors"] = list(body.get("injected_errors") or [])
    kinds = list(DISTORTIONS)
    rng.shuffle(kinds)
    planted = 0
    used_fields: set[str] = set()
    for kind in kinds:
        if planted >= count:
            break
        error = DISTORTIONS[kind](result, rng, ctx)
        if error is None or error["field"] in used_fields:
            continue
        used_fields.add(error["field"])
        result["injected_errors"].append(error)
        planted += 1
    if not planted:
        result.pop("injected_errors", None)
        return body
    return result


def _copy_card(card: Mapping) -> dict:
    copy = dict(card)
    copy["address"] = dict(card.get("address") or {})
    copy["flags"] = dict(card.get("flags") or {})
    copy["notified"] = [dict(n) for n in card.get("notified") or []]
    return copy


# --- kinds -----------------------------------------------------------------------------------


def neighbour_house(body: dict, rng: random.Random, ctx: Context) -> dict | None:
    """«д. 44» instead of «д. 42»; the description names the right house."""
    card = body["card"]
    house = str(card["address"].get("house") or "")
    match = _HOUSE_NUMBER.match(house.strip())
    if not match or not card["address"].get("street"):
        return None
    number, suffix = int(match.group(1)), match.group(2)
    neighbour = number + rng.choice(NEIGHBOUR_HOUSE_STEPS)
    if neighbour <= 0:
        neighbour = number + 1
    wrong = f"{neighbour}{suffix}"
    if normalize_house(wrong) == normalize_house(house):
        return None
    card["address"]["house"] = wrong
    card["description"] = _mention(
        card.get("description") or "",
        normalize_house(house),
        f"Заявитель называет адрес: {card['address']['street']}, дом {house}.",
    )
    return {
        "field": "address.house",
        "wrong_value": wrong,
        "correct_value": house,
        "hint_level": 2,
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
    card["description"] = _mention(
        card.get("description") or "",
        normalize_text(street),
        f"Заявитель называет адрес: {street}{', дом ' + str(house) if house else ''}.",
    )
    return {
        "field": "address.street",
        "wrong_value": wrong,
        "correct_value": street,
        "hint_level": 3,
    }


def sibling_incident_type(body: dict, rng: random.Random, ctx: Context) -> dict | None:
    """A neighbouring type of the classifier (same group, other final title); the description
    still tells what really happened."""
    card = body["card"]
    code = str(card.get("incident_type") or "")
    rows = {str(r["code"]): r for r in ctx.type_rows}
    row = rows.get(code)
    if row is None:
        return None
    prefix = code.rsplit(".", 1)[0] + "."
    siblings = [
        r
        for c, r in rows.items()
        if c != code and c.startswith(prefix) and r.get("final_title") != row.get("final_title")
    ]
    if not siblings:
        group = str(row.get("group_code") or code.split(".")[0])
        siblings = [
            r
            for c, r in rows.items()
            if c != code and str(r.get("group_code")) == group and r.get("final_title")
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
    }


def flipped_injured(body: dict, rng: random.Random, ctx: Context) -> dict | None:
    """«Пострадавшие: да» while the description says there are none (or the other way round)."""
    card = body["card"]
    injured = bool(card["flags"].get("injured"))
    description = normalize_text(card.get("description") or "")
    says_absent = any(p in description for p in INJURED_ABSENT)
    says_present = "пострадавш" in description and not says_absent
    if injured and not says_present:
        return None
    if not injured and not says_absent:
        return None
    card["flags"]["injured"] = not injured
    return {
        "field": "flags.injured",
        "wrong_value": "false" if injured else "true",
        "correct_value": "true" if injured else "false",
        "hint_level": 1,
        "wrong_label": "нет" if injured else "да",
        "correct_label": "да" if injured else "нет",
    }


def extra_service(body: dict, rng: random.Random, ctx: Context) -> dict | None:
    """A service that has nothing to do with the incident is listed as notified."""
    card = body["card"]
    notified = {str(n.get("service")) for n in card["notified"]}
    candidates = sorted(
        code
        for code, info in ctx.catalogue.items()
        if info.via_arm112 and code not in notified and code != body.get("service")
    )
    if not candidates:
        return None
    code = rng.choice(candidates)
    card["notified"].insert(0, {"service": code, "status": "Получена службой"})
    return {
        "field": "services",
        "wrong_value": f"+{code}",
        "correct_value": f"-{code}",
        "hint_level": 1,
        "wrong_label": ctx.catalogue[code].title or code,
        "correct_label": ctx.catalogue[code].title or code,
    }


def missing_service(body: dict, rng: random.Random, ctx: Context) -> dict | None:
    """A service the incident needs is not notified."""
    card = body["card"]
    own = body.get("service")
    candidates = [n for n in card["notified"] if str(n.get("service")) != own]
    if len(candidates) < 2:
        return None  # a card with a single other service would lose all context
    removed = rng.choice(sorted(candidates, key=lambda n: str(n.get("service"))))
    card["notified"] = [n for n in card["notified"] if n is not removed]
    code = str(removed.get("service"))
    info = ctx.catalogue.get(code)
    return {
        "field": "services",
        "wrong_value": f"-{code}",
        "correct_value": f"+{code}",
        "hint_level": 2,
        "wrong_label": info.title if info and info.title else code,
        "correct_label": info.title if info and info.title else code,
    }


DISTORTIONS: dict[str, Distortion] = {
    "house": neighbour_house,
    "street": lookalike_street,
    "incident_type": sibling_incident_type,
    "injured": flipped_injured,
    "service_extra": extra_service,
    "service_missing": missing_service,
}


def _mention(description: str, needle: str, sentence: str) -> str:
    """Adds the sentence unless the description already carries the fact."""
    if needle and needle in normalize_text(description):
        return description
    text = description.rstrip()
    if text and not text.endswith((".", "!", "?")):
        text += "."
    return f"{text} {sentence}".strip()
