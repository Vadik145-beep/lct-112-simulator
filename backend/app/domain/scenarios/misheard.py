"""The squad chief mishears the address (issue #263, decision of 17.10.2026: «сложность 2 —
один раз путают данные: „Записал: дом 15?“ при 13, не поправил — ошибка»).

On a card of difficulty 2 and up, the first time the dispatcher names the address to the
officer of their own service, the officer repeats it back with a wrong house. The dispatcher
is expected to correct it in the same call. Left uncorrected, the squad goes to the wrong
house: its reports name it, and the review counts «не исправил неверно понятое». A correction
made later (on a report, on a repeat call) turns the squad round, but the error stays.

Everything is read from the stored turns, so the runtime and the evaluation agree on one
truth: the officer's question carries the topic ``misheard`` and the house he said
(``misheard_house``), the dispatcher's correcting phrase the topic ``misheard_fixed``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import pairwise

from app.domain.evaluation.schemas import Card
from app.domain.evaluation.text import normalize_house, normalize_text, words_to_numbers

MIN_DIFFICULTY = 2
TOPIC_MISHEARD = "misheard"
TOPIC_FIXED = "misheard_fixed"
HOUSE_KEY = "misheard_house"
# How far off the officer is: 13 → 15, as in the example of the decision.
HOUSE_SHIFT = 2

# The dispatcher says «no» to the repeated address without naming the house.
_DENIAL = re.compile(r"^(нет|не так|не тот|неверно|неправильно|ошиб\w*)\b")
# The dispatcher agrees with the repeated (wrong) address.
_AGREEMENT = re.compile(r"^(да|верно|так точно|правильно|все верно|подтверждаю)\b")

ASK_HOUSE = "Какой дом тогда? Повторите."
AGREED = "Понял, записал."


@dataclass(frozen=True)
class Misheard:
    """The officer's mistake on this card: which house he said and whether it was corrected
    in the call it was made in (``fixed``) or later (``fixed_late``)."""

    call_id: str
    house_said: str
    fixed: bool
    fixed_late: bool

    @property
    def squad_has_wrong_house(self) -> bool:
        return not self.fixed and not self.fixed_late


def wrong_house(house: str | None) -> str | None:
    """The house the officer «hears»: the number of the card plus ``HOUSE_SHIFT``, the letter
    kept. ``None`` when the card's house has no number to get wrong."""
    match = re.match(r"\s*(\d+)(.*)$", house or "")
    if match is None:
        return None
    return f"{int(match.group(1)) + HOUSE_SHIFT}{match.group(2)}".strip()


def applies(difficulty: int, card: Card) -> bool:
    return difficulty >= MIN_DIFFICULTY and wrong_house(card.address.house) is not None


def spoken_address(address: Mapping, house: str | None = None) -> str:
    """The street, the house and the building as said aloud; ``house`` replaces the card's."""
    parts = [address.get("street") or ""]
    house = house or address.get("house")
    if house:
        parts.append(f"дом {house}")
    if address.get("building"):
        parts.append(f"корпус {address['building']}")
    text = ", ".join(p for p in parts if p)
    return text or (address.get("descriptive") or "адрес по карточке")


def question(card: Card) -> str:
    """«Записал: улица Свободы, дом 44, корпус 2?» for a card at house 42."""
    said = wrong_house(card.address.house)
    return f"Записал: {spoken_address(card.address.model_dump(), said)}?"


def fixed_reply(card: Card, *, squad_out: bool) -> str:
    house = card.address.house
    if squad_out:
        return f"Понял, дом {house}. Ошиблись адресом, переезжаем."
    return f"Понял, дом {house}. Исправил, записал."


def names_house(card: Card, text: str) -> bool:
    """The phrase names the house of the card (digits or words: «тринадцать»)."""
    house = normalize_house(card.address.house)
    if not house:
        return False
    spoken = words_to_numbers(normalize_text(text))
    return any(normalize_house(token) == house for token in spoken.split())


def house_said(card: Card, text: str) -> bool:
    """The phrase names the card's house as a house: «дом 10», «д. десять» — not any number."""
    house = normalize_house(card.address.house)
    if not house:
        return False
    spoken = words_to_numbers(normalize_text(text)).split()
    return any(
        word in ("дом", "доме", "дома", "д") and normalize_house(nxt) == house
        for word, nxt in pairwise(spoken)
    )


def denies(text: str) -> bool:
    return bool(_DENIAL.match(normalize_text(text)))


def agrees(text: str) -> bool:
    return bool(_AGREEMENT.match(normalize_text(text)))


def state(calls: Iterable[Mapping]) -> Misheard | None:
    """The officer's mistake on the card, read from the stored calls in their order."""
    found: Misheard | None = None
    for call in calls:
        call_id = str(call.get("id") or "")
        for turn in call.get("dialog") or []:
            topics = turn.get("topics") or []
            if found is None:
                if turn.get("role") == "caller" and TOPIC_MISHEARD in topics:
                    found = Misheard(call_id, str(turn.get(HOUSE_KEY) or ""), False, False)
                continue
            if turn.get("role") != "operator" or TOPIC_FIXED not in topics:
                continue
            if found.squad_has_wrong_house:
                same_call = call_id == found.call_id
                found = Misheard(found.call_id, found.house_said, same_call, not same_call)
    return found


def report_text(text: str, card: Card, misheard: Misheard | None) -> str:
    """A report of a squad sent to the wrong house names that house."""
    if misheard is None or not misheard.squad_has_wrong_house:
        return text
    address = card.address.model_dump()
    right = spoken_address(address)
    wrong = spoken_address(address, misheard.house_said)
    return text.replace(right, wrong, 1)
