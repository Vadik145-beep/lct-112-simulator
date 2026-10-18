"""No vehicles (issue #263, decision of 17.10.2026: «сложность 3 — „машин нет“, диспетчер
должен доложить руководителю организации»).

On difficulty 3 a trainee's cards take turns: one gets «машин нет», the next a complication on
site (``complication``), and so on, the first card of difficulty 3 always «машин нет» — every
trainee meets it at least once in a lesson (decision of 17.10.2026, for the video). The turn is
fixed on the first call to the squad chief of the card (``PLAN_KEY`` on that call).

On a «машин нет» card the squad chief takes the address and says there are no vehicles. The
squad stays where it is — no departure, no reports — until the dispatcher reports the shortage
to the head of the organization, who takes a vehicle off a planned job. One truth for the card:
the squad is not sent, has no vehicles, or is sent; every answer follows it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime

from app.domain.evaluation.text import normalize_text

MIN_DIFFICULTY = 3
PLAN_KEY = "squad_plan"
PLAN_NO_VEHICLES = "no_vehicles"
PLAN_COMPLICATION = "complication"
TOPIC_NO_VEHICLES = "no_vehicles"
TOPIC_VEHICLES_FOUND = "vehicles_found"
ROLE_KEY = "role"

SHORTAGE = "Только машин нет, все на выездах. Ближайшая освободится часа через полтора."
ANNOUNCE = f"Принял. {SHORTAGE}"
STILL_NONE = "Машин так и нет, все на выездах."
# The chief's fact sheet on a «машин нет» card, until the head finds a vehicle.
FACT_KEY = "машины"
# Asked straight what to do, the chief sends the dispatcher to the head; he never hints it
# unasked (decision of 18.10.2026: he said «ожидаю освобождения машины»).
TO_HEAD = "Это к руководителю, только он может снять машину."
ASKS_WHAT_TO_DO = re.compile(
    r"что\s+(же\s+|нам\s+|мне\s+|теперь\s+|тогда\s+)?(делать|делаем|будем\s+делать|предлагаете)"
    r"|как\s+(же\s+)?(быть|поступить)"
)
# One short line of the chief's prompt (decision of 18.10.2026: no notes mid-call).
FACT = (
    "свободных машин нет, все на выездах, наряд не называешь; только если диспетчер прямо "
    f"спросит, что делать, — «{TO_HEAD}»"
)
# The chief called again before the head found a vehicle: he has said it already and does not
# take the card anew (stand 18.10.2026: the second call asked the address all over again).
FACT_AGAIN = (
    "ты уже говорил диспетчеру, что машин нет; машин так и нет, все на выездах, наряд не "
    f"называешь; только если диспетчер прямо спросит, что делать, — «{TO_HEAD}»"
)
RESOLVED = (
    "Понял. Снимаю машину с планового выезда, бригада выедет через десять минут. "
    "Держите на контроле."
)
# Told of the shortage without the address, the head asks it first (decision of 18.10.2026).
ASK_ADDRESS = "По какому адресу?"
TOPIC_ASK_ADDRESS = "ask_address"
# The dispatcher tells the head about the shortage: vehicles, squads, people, equipment.
_SHORTAGE = re.compile(r"(машин|свободн|бригад|техник|экипаж|\bсил\b|\bнекого\b|\bнечем\b)")


def applies(difficulty: int) -> bool:
    return difficulty >= MIN_DIFFICULTY


def plan_after(plans: Iterable[str]) -> str:
    """The turn of the next card after the turns already given in the lesson: «машин нет» while
    it is not ahead of the complications, so the cards alternate whatever order they are
    worked in."""
    rows = list(plans)
    ahead = rows.count(PLAN_NO_VEHICLES) > rows.count(PLAN_COMPLICATION)
    return PLAN_COMPLICATION if ahead else PLAN_NO_VEHICLES


def plan_of(calls: Iterable[Mapping]) -> str | None:
    return next((str(c[PLAN_KEY]) for c in calls if c.get(PLAN_KEY)), None)


def mentions_shortage(text: str) -> bool:
    return bool(_SHORTAGE.search(normalize_text(text)))


def _said(call: Mapping, topic: str) -> bool:
    return any(
        t.get("role") == "caller" and topic in (t.get("topics") or [])
        for t in call.get("dialog") or []
    )


def announced(calls: Iterable[Mapping]) -> bool:
    """The squad chief has said there are no vehicles."""
    return any(_said(c, TOPIC_NO_VEHICLES) for c in calls)


def resolving_call(calls: Iterable[Mapping]) -> Mapping | None:
    """The call in which the head of the organization found a vehicle."""
    return next((c for c in calls if _said(c, TOPIC_VEHICLES_FOUND)), None)


def held(calls: Iterable[Mapping]) -> bool:
    """The card is a «машин нет» one and the head has not found a vehicle yet: the squad
    cannot leave, whatever was said (one truth from the start of the card)."""
    rows = list(calls)
    return plan_of(rows) == PLAN_NO_VEHICLES and resolving_call(rows) is None


# The head's own words when he finds a vehicle (his prompt tells him to say so).
FOUND_WORDS = re.compile(r"планов")
HEAD_FACT_KEY = "машины"
HEAD_FACT = "если адрес не назван — спроси адрес, потом снимаешь машину с планового выезда"
# The chief's own words on a «машин нет» card, read from the record of a cloud call (stand
# 18.10.2026: «Свободных нет, все на выездах, выехать не могу» was not heard as «машин нет»).
# Only what is said of vehicles and squads: «пострадавших нет», «доступа нет» are not here.
_SQUADS = r"(машин\w*|бригад\w*|экипаж\w*)"
_STILL = r"(так\s+и\s+|пока\s+|сейчас\s+)?"
NONE_WORDS = re.compile(
    rf"машин\w*\s+{_STILL}нет"
    r"|нет\s+(свободн\w+\s+)?машин"
    rf"|свободн\w*\s+({_SQUADS}\s+)?{_STILL}нет"
    rf"|нет\s+свободн\w*(\s+{_SQUADS}|\s+(все|сейчас|пока)\b|$)"
    rf"|все\s+({_SQUADS}\s+)?(на\s+выезд|в\s+разъезд)"
    r"|машин\w*\s+(все\s+)?занят"
    r"|некого\s+(отправ|направ|посла)"
    r"|не\s+на\s+чем\s+(ехать|выех)"
)
# «Выехать не могу» is «машин нет» unless he says what he is missing («без адреса …»).
CANNOT_GO = re.compile(r"выех\w*\s+(\w+\s+)?не\s+с?мо[гж]\w*|не\s+с?мо[гж]\w*\s+(\w+\s+)?выех")
MISSING_SAID = re.compile(r"\bбез\b|адрес|распоряжен|команд")


def says_none(text: str) -> bool:
    """The squad chief says there are no vehicles."""
    said = normalize_text(text)
    if NONE_WORDS.search(said):
        return True
    return bool(CANNOT_GO.search(said)) and not MISSING_SAID.search(said)


def asks_what_to_do(text: str) -> bool:
    return bool(ASKS_WHAT_TO_DO.search(normalize_text(text)))


def finds_vehicle(text: str) -> bool:
    """The head says he takes a vehicle off a planned job."""
    return bool(FOUND_WORDS.search(normalize_text(text)))


def blocked(calls: Iterable[Mapping]) -> bool:
    """No vehicles said and no vehicle found yet: the squad stays where it is."""
    rows = list(calls)
    return announced(rows) and resolving_call(rows) is None


def released_at(calls: Iterable[Mapping]) -> datetime | None:
    """When the squad got its vehicle: the end of the head's call (``None`` while it lasts)."""
    call = resolving_call(calls)
    ended = call.get("ended_at") if call else None
    return datetime.fromisoformat(ended) if isinstance(ended, str) else ended
