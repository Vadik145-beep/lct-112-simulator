"""The squad chief is in a hurry (issue #263, decision of 17.10.2026: «сложность 3 — стресс:
торопит „Короче, что у вас?“; „Стоп, короче“ после длинного доклада — имитация перебивания,
настоящего перебивания нет»).

On a card of difficulty 3 the officer of the dispatcher's own service picks up with «Короче,
что у вас?», and once per call cuts a long phrase short. It is pressure, not a mistake of the
dispatcher: nothing is scored for it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.domain.evaluation.text import normalize_text

MIN_DIFFICULTY = 3
TOPIC_RUSH = "rush"
OPENING_TAIL = "Короче, что у вас?"
CUT_IN = "Стоп, короче. Адрес и что случилось — в двух словах."
# A phrase longer than this is «a long report»: the address, the incident, the injured and
# the access fit in about twenty words.
LONG_PHRASE_WORDS = 30


def applies(difficulty: int) -> bool:
    return difficulty >= MIN_DIFFICULTY


def opening(greeting: str) -> str:
    return f"{greeting} {OPENING_TAIL}"


def is_long(text: str) -> bool:
    return len(normalize_text(text).split()) > LONG_PHRASE_WORDS


def cut_in_already(turns: Iterable[Mapping]) -> bool:
    return any(t.get("role") == "caller" and TOPIC_RUSH in (t.get("topics") or []) for t in turns)
