"""What card the trainee gets next in a lesson where the cards are written for each trainee
(«Задания формирует ИИ», issue #265, recommendations 9–10 of the customer).

Pure: the caller passes the classifier rows, the trainee's ratings and the results of the
trainee's earlier cards of the lesson. The plan is a generation request (incident type, group,
difficulty), the mistake kinds to plant first and the reasons in plain words for the trainee
and the teacher.

* What the difficulty changes (решение 17.10): 1 — an everyday incident with few services, no
  mistakes; 2 — an everyday incident with several services, one mistake; 3 — an emergency
  with many services, two mistakes. The mistakes themselves are planted by ``distort``.
* Adaptive: after a card that was not passed or had a mistake missed, the next one stays in the
  same incident group (another type) and the missed kind is planted again; otherwise the
  weakest group by rating the trainee has not had yet. Unseen groups count as the start rating,
  so the first card of a new trainee is a stable random choice per trainee.
* Two trainees get different cards: a type already used in the lesson is taken only when its
  group has no other.
* The card is for the trainee's own service (``own_service``): only types it is notified of,
  and it is the card's own tab. The exception is the «не та служба» mistake, planned here: a type
  the service does not serve, sent to it all the same; it counts as one of the card's mistakes.
  It comes as the ``WRONG_SERVICE_SEQ``-th card from difficulty 2 and again after it was missed.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.domain.analytics.rating import START_RATING, incident_group
from app.domain.scenarios.template import ServiceInfo, choose_card_service
from app.domain.services import resolve_services
from app.providers.generation import GenerationRequest

CARDS_PER_TRAINEE = 5  # решение пользователя 17.10.2026: пять карточек на обучающегося
KIND = "card_response"

# Incident groups of the classifier by what they are like (numbers of the groups).
EVERYDAY_GROUPS: dict[int, tuple[str, ...]] = {
    # Household: utilities, social help, animals, other, problems on the road.
    1: ("14", "16", "20", "21", "23"),
    # Everyday, more involved: road accidents, ecology, gas smell, utilities, public order,
    # road problems, a person in danger, animals.
    2: ("2", "8", "13", "14", "15", "16", "17", "21"),
    # Emergencies (ЧС): fires, explosions, collapses and their threats, natural hazards,
    # accidents at hazardous plants, hazardous substances, transport accidents.
    3: ("1", "3", "5", "6", "7", "10", "11", "12"),
}
# Services the incident notifies without any flag, by difficulty (inclusive bounds).
SERVICES_BY_DIFFICULTY: dict[int, tuple[int, int]] = {1: (1, 4), 2: (4, 12), 3: (10, 99)}
# Field of a planted mistake → its kind in ``distort``.
KIND_BY_FIELD = {
    "address.house": "house",
    "address.street": "street",
    "incident_type": "incident_type",
    "flags.injured": "injured",
    "service": "wrong_service",
}
MISSED_VERDICTS = ("missed", "wrong_correction")
WRONG_SERVICE = "wrong_service"
WRONG_SERVICE_SEQ = 3  # the card of the lesson that is sent to the wrong service
WRONG_SERVICE_FROM = 2  # difficulty from which the card carries mistakes at all


@dataclass
class EarlierCard:
    """A card of the same trainee in this lesson, closed and scored."""

    incident_type: str
    total: float
    passed: bool
    data_check: Sequence[Mapping] = ()  # items of the «Проверка данных» component


@dataclass
class PlanInput:
    difficulty: int
    seq: int  # number of the card for this trainee, from 1
    trainee_key: str  # stable per trainee and lesson: the random choices follow it
    type_rows: Sequence[Mapping]
    catalogue: Mapping[str, ServiceInfo]
    group_titles: Mapping[str, str]
    ratings: Mapping[str, float]  # incident group → the trainee's rating in this mode
    earlier: Sequence[EarlierCard] = ()
    allowed_groups: Sequence[str] = ()  # the lesson's incident groups; empty = by difficulty
    services: Sequence[str] = ()  # the lesson's services; empty = any
    taken_types: Sequence[str] = ()  # types already given in the lesson (all trainees)
    own_service: str | None = None  # the trainee's service; its cards are for it


@dataclass
class Plan:
    request: GenerationRequest
    prefer: list[str]
    reasons: list[str] = field(default_factory=list)


def plan_next(spec: PlanInput) -> Plan:
    rng = random.Random(f"{spec.trainee_key}|{spec.seq}")  # noqa: S311 - variety, not security
    desk = spec.own_service if spec.own_service in spec.catalogue else None
    last = spec.earlier[-1] if spec.earlier else None
    missed = _missed_kinds(last) if last else []
    wrong = desk is not None and _wrong_service_now(spec, missed)
    rows = _rows_for(spec, desk, serves=not wrong)
    if wrong and not rows:  # the service is notified of everything that fits
        wrong, rows = False, _rows_for(spec, desk, serves=True)
    if not rows:  # a service the classifier never notifies: the card is not tied to it
        desk, rows = None, _rows_for(spec, None, serves=True)
    by_group: dict[str, list[Mapping]] = {}
    for row in rows:
        by_group.setdefault(str(row["group_code"]), []).append(row)
    group, reason = _choose_group(spec, by_group, last, missed, rng)
    row = _choose_type(by_group[group], spec.taken_types, rng)
    prefer = [k for k in (missed or _fresh_kinds(spec.earlier)) if k != WRONG_SERVICE]
    reasons = [reason]
    if missed:
        titles = ", ".join(f"«{t}»" for t in _missed_titles(last))
        reasons.append(f"Пропущена ошибка оператора 112 ({titles}) — подобная будет снова.")
    if wrong:
        reasons.append(
            "Подвох «не та служба»: такое происшествие ваша служба не обслуживает — карточку "
            "нужно не принять с причиной."
        )
    request = GenerationRequest(
        kind=KIND,
        phrase=str(row["final_title"])[:500],
        incident_type=str(row["code"]),
        incident_group=group,
        difficulty=spec.difficulty,
        service=desk,
        wrong_service=wrong,
    )
    return Plan(request=request, prefer=prefer, reasons=reasons)


def _wrong_service_now(spec: PlanInput, missed: Sequence[str]) -> bool:
    if spec.difficulty < WRONG_SERVICE_FROM:
        return False
    if WRONG_SERVICE in missed:
        return True
    had = any(item.get("field") == "service" for card in spec.earlier for item in card.data_check)
    return spec.seq == WRONG_SERVICE_SEQ and not had


# --- choice of the rows ----------------------------------------------------------------------


def _expected(row: Mapping) -> list[str]:
    return resolve_services(row.get("service_rules") or [], [])


def _mentions(row: Mapping, service: str) -> bool:
    """Whether the classifier notifies the service of this type under any condition."""
    return any(rule.get("service") == service for rule in row.get("service_rules") or [])


def _rows_for(spec: PlanInput, desk: str | None, *, serves: bool) -> list[Mapping]:
    """Rows fit for the trainee's service (``serves``: it is notified without conditions; not
    ``serves``: it is not notified at all), then the difficulty and the lesson's groups; the
    other conditions are dropped (last first) rather than leaving nothing, the service never.
    Without the trainee's service the lesson's services count instead."""
    groups = set(spec.allowed_groups) or set(EVERYDAY_GROUPS.get(spec.difficulty, ()))
    lo, hi = SERVICES_BY_DIFFICULTY.get(spec.difficulty, (1, 99))

    def own_service_fits(row: Mapping) -> bool:
        if desk is not None:
            return desk in _expected(row) if serves else not _mentions(row, desk)
        if not spec.services:
            return True
        service = choose_card_service(_expected(row), spec.catalogue, row.get("main_service"))
        return service in spec.services

    def services_fit(row: Mapping) -> bool:
        return lo <= len(_expected(row)) <= hi

    def in_groups(row: Mapping) -> bool:
        return str(row.get("group_code")) in groups

    usable = [r for r in spec.type_rows if r.get("final_title")]
    chains = (
        (in_groups, own_service_fits, services_fit),
        (in_groups, own_service_fits),
        (own_service_fits, services_fit),
        (own_service_fits,),
    )
    if desk is None:
        chains = (*chains, (in_groups,), ())
    for checks in chains:
        rows = [r for r in usable if all(check(r) for check in checks)]
        if rows:
            return rows
    return []


def _choose_group(
    spec: PlanInput,
    by_group: Mapping[str, list[Mapping]],
    last: EarlierCard | None,
    missed: Sequence[str],
    rng: random.Random,
) -> tuple[str, str]:
    groups = sorted(by_group)
    title = spec.group_titles
    if last is not None:
        last_group = incident_group(last.incident_type)
        if last_group in by_group and (missed or not last.passed):
            why = "не зачтена" if not last.passed else "есть пропущенные ошибки"
            return last_group, (
                f"Прошлая карточка группы «{title.get(last_group, last_group)}» {why} — "
                "ещё одна из этой группы."
            )
    done = {incident_group(c.incident_type) for c in spec.earlier}
    fresh = [g for g in groups if g not in done] or groups
    rng.shuffle(fresh)  # equal ratings: a stable random order per trainee
    fresh.sort(key=lambda g: spec.ratings.get(g, START_RATING))
    group = fresh[0]
    rating = spec.ratings.get(group)
    if rating is None:
        return group, f"Группа «{title.get(group, group)}»: карточек этой группы ещё не было."
    return group, (f"Группа «{title.get(group, group)}»: самый низкий рейтинг ({round(rating)}).")


def _choose_type(rows: Sequence[Mapping], taken: Sequence[str], rng: random.Random) -> Mapping:
    ordered = sorted(rows, key=lambda r: str(r["code"]))
    fresh = [r for r in ordered if str(r["code"]) not in set(taken)]
    return rng.choice(fresh or ordered)


# --- mistakes --------------------------------------------------------------------------------


def _missed_kinds(card: EarlierCard) -> list[str]:
    kinds = [
        KIND_BY_FIELD[str(item.get("field"))]
        for item in card.data_check
        if item.get("verdict") in MISSED_VERDICTS and item.get("field") in KIND_BY_FIELD
    ]
    return list(dict.fromkeys(kinds))


def _missed_titles(card: EarlierCard) -> list[str]:
    return [
        str(item.get("title") or item.get("field"))
        for item in card.data_check
        if item.get("verdict") in MISSED_VERDICTS
    ]


def _fresh_kinds(earlier: Sequence[EarlierCard]) -> list[str]:
    """Kinds the trainee has not met yet, so the lesson goes through different mistakes."""
    seen = {
        KIND_BY_FIELD[str(item.get("field"))]
        for card in earlier
        for item in card.data_check
        if item.get("field") in KIND_BY_FIELD
    }
    return [k for k in KIND_BY_FIELD.values() if k not in seen]
