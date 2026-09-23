"""Remarks on a scenario that the reference checks do not catch.

``validate.check_body`` answers «is this a valid scenario» — codes exist, topics are known,
the schema holds. These checks answer «is it usable in a lesson»: the caller must speak the way
a person speaks, because every reply is read aloud, and the card must be fillable from what the
caller says. They never block anything; the teacher sees them next to the scenario and decides.

Written out of what the 96 delivered scenarios were checked against by hand (23.09.2026).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# Read aloud, «кв.» becomes «кэвэ» and «(при уточнении)» is read as part of the phrase.
SHORTHAND = {
    r"\bа/м\b": "а/м",
    r"\bд/р\b": "д/р",
    r"\bб/п\b": "б/п",
    r"\bб/р\b": "б/р",
    r"\bж/д\b": "ж/д",
    r"\bкв\.": "кв.",
    r"\bд\.\s*\d": "д. с номером",
    r"\bэт\.": "эт.",
    r"\bпод\.": "под.",
    r"\bул\.": "ул.",
    r"\bкорп\.": "корп.",
    r"\bстр\.": "стр.",
}
_DIGITS = re.compile(r"\d")
_BRACKETS = re.compile(r"[()]")


def _spoken(body: Mapping) -> list[tuple[str, str]]:
    """(where, text) of everything the caller says aloud."""
    caller = body.get("caller") or {}
    spoken = []
    if caller.get("opening"):
        spoken.append(("первая фраза", str(caller["opening"])))
    for reply in body.get("replies") or []:
        if reply.get("text"):
            spoken.append((f"реплика {reply.get('id')}", str(reply["text"])))
    return spoken


def _speech_remarks(body: Mapping) -> list[str]:
    remarks = []
    for where, text in _spoken(body):
        if _BRACKETS.search(text):
            remarks.append(f"{where}: скобки — их прочитают вслух.")
        found = [label for pattern, label in SHORTHAND.items() if re.search(pattern, text)]
        if found:
            remarks.append(f"{where}: сокращения ({', '.join(found)}) — заявитель их не произносит.")
        if _DIGITS.search(text):
            remarks.append(f"{where}: цифры — числа лучше писать словами.")
    return remarks


def _card_remarks(body: Mapping) -> list[str]:
    card = body.get("reference_card") or body.get("card") or {}
    remarks = []
    description = str(card.get("description") or "")
    missing = [
        word
        for word in card.get("description_keywords") or []
        if str(word).lower() not in description.lower()
    ]
    if missing:
        remarks.append(
            "Ключевые слова карточки не встречаются в описании: " + ", ".join(missing) + "."
        )
    address = card.get("address") or {}
    if not (address.get("street") or address.get("descriptive") or address.get("region")):
        remarks.append("В эталонной карточке нет адреса: диспетчеру нечего сверять.")
    caller = card.get("caller") or {}
    if body.get("kind") == "call_intake" and not caller.get("phone"):
        remarks.append("В эталонной карточке нет телефона заявителя.")
    return remarks


def _coverage_remarks(body: Mapping) -> list[str]:
    if body.get("kind") != "call_intake":
        return []
    topics = {r.get("topic") for r in body.get("replies") or []}
    missing = [t for t in body.get("required_topics") or [] if t not in topics]
    # «repeat» и «unknown» у сценария может не быть: на такой вопрос ответит банк общих фраз
    # голосом заявителя (app.domain.scenarios.fallback), это не изъян сценария.
    if not missing:
        return []
    return [
        "Обязательные темы без реплики — диспетчер не сможет их выяснить: "
        + ", ".join(missing)
        + "."
    ]


def review(body: Mapping) -> list[str]:
    """Remarks in the order a teacher reads them: speech first, then the card, then coverage."""
    return _speech_remarks(body) + _card_remarks(body) + _coverage_remarks(body)
