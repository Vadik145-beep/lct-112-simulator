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
ANNOUNCE = f"Адрес принял. {SHORTAGE}"
STILL_NONE = "Машин так и нет, все на выездах."
RESOLVED = (
    "Понял. Снимаю машину с планового выезда, бригада выедет через десять минут. "
    "Держите на контроле."
)
# The dispatcher tells the head about the shortage: vehicles, squads, people, equipment.
_SHORTAGE = re.compile(r"(машин|свободн|бригад|техник|экипаж|\bсил\b|\bнекого\b|\bнечем\b)")


def applies(difficulty: int) -> bool:
    return difficulty >= MIN_DIFFICULTY


def next_plan(previous: str | None) -> str:
    """The turn of a new card of difficulty 3 after the trainee's previous one."""
    return PLAN_COMPLICATION if previous == PLAN_NO_VEHICLES else PLAN_NO_VEHICLES


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


def blocked(calls: Iterable[Mapping]) -> bool:
    """No vehicles said and no vehicle found yet: the squad stays where it is."""
    rows = list(calls)
    return announced(rows) and resolving_call(rows) is None


def released_at(calls: Iterable[Mapping]) -> datetime | None:
    """When the squad got its vehicle: the end of the head's call (``None`` while it lasts)."""
    call = resolving_call(calls)
    ended = call.get("ended_at") if call else None
    return datetime.fromisoformat(ended) if isinstance(ended, str) else ended
