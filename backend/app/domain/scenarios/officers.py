"""Duty officers of the city services (issue #36, customer: «звено Б → В»).

The dispatcher of a service receives the card and calls the officer of the responding unit to
pass the facts. The officer is played by the dialog engine with the profile below: calm,
short, asks for the address, the incident, the injured, the squad number, confirms receipt.
Every service has a built-in set of replies (topics = the facts of a service call plus
``greeting``, ``confirm``, ``repeat``, ``unknown``); a scenario may add its own
(``service_replies``), and generated ones wait for the teacher like the caller's.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.domain.evaluation.schemas import (
    CallerProfile,
    CallIntakeScenario,
    CardResponseScenario,
    ReferenceCard,
    Reply,
    ServiceCallRef,
)

OFFICER_PERSONA = "service_officer"
OFFICER_VOICE = "ru_male_2"
OFFICER_BEHAVIOUR = (
    "спокойный дежурный службы: говорит коротко и по делу, уточняет адрес, тип происшествия, "
    "есть ли пострадавшие, просит номер наряда, подтверждает приём информации"
)
# Replies scenarios may not replace: the built-in set is the floor of every service.
BUILTIN_REPLY_ID_BASE = 1000

# Generic replies of any duty officer; {service} is the short title of the service. Within a
# topic the confirmation comes first: without a model the first unused reply of the topic is
# chosen, and the dispatcher usually states the fact rather than being asked for it.
GENERIC_REPLIES: list[tuple[str, str]] = [
    ("greeting", "Дежурный {service}, слушаю."),
    ("address", "Адрес принял. Подъезд, этаж известны?"),
    ("address", "Назовите адрес: улица, дом, корпус."),
    ("incident_type", "Понял, происшествие принял."),
    ("incident_type", "Что именно произошло? Какой тип происшествия по карточке?"),
    ("injured", "Принято, по пострадавшим понял."),
    ("injured", "Пострадавшие есть?"),
    ("order_number", "Наряд записал."),
    ("order_number", "Номер наряда назовите."),
    ("access", "Понял, доступ есть."),
    ("access", "Доступ на объект есть? Кто встретит бригаду?"),
    ("confirm", "Информацию принял, бригаду направляю."),
    ("confirm", "Принято. Как будем на месте, доложу."),
    ("repeat", "Повторите, пожалуйста, плохо слышно."),
    ("unknown", "Это не ко мне, давайте по происшествию."),
]

# Service-specific flavour on top of the generic set.
SERVICE_REPLIES: dict[str, list[tuple[str, str]]] = {
    "moek": [
        ("incident_type", "Отопление или горячая вода? Сколько домов без тепла?"),
        ("confirm", "Принял, бригада тепловых сетей выезжает."),
    ],
    "mosvodokanal": [
        ("incident_type", "Холодная вода или канализация? Течёт на улице или в доме?"),
        ("confirm", "Принял, аварийная бригада выезжает, участок отключим."),
    ],
    "gkh": [
        ("incident_type", "Стояк, кровля или подвал? Квартиры заливает?"),
        ("confirm", "Принял, сантехник ОДС выезжает."),
    ],
    "mosgaz": [
        ("incident_type", "Запах газа где: в квартире, в подъезде, на улице?"),
        ("injured", "Люди из помещения выведены? Плохо кому-нибудь?"),
        ("confirm", "Принял, аварийная бригада Мосгаза выезжает, до прибытия не включать свет."),
    ],
    "gormost": [
        ("incident_type", "Переход, мост или набережная? Движение перекрыто?"),
        ("confirm", "Принял, дежурная бригада Гормоста выезжает."),
    ],
    "moslift": [
        ("incident_type", "Лифт стоит с людьми или пустой? Между этажами?"),
        ("confirm", "Принял, механик выезжает."),
    ],
    "territorial_oiv": [
        ("confirm", "Принял, информацию довожу до дежурного управы."),
    ],
}


def builtin_replies(service: str, service_title: str) -> list[Reply]:
    """The built-in replies of a service's officer, ids from ``BUILTIN_REPLY_ID_BASE``."""
    rows = [*GENERIC_REPLIES, *SERVICE_REPLIES.get(service, [])]
    return [
        Reply(
            id=BUILTIN_REPLY_ID_BASE + i,
            topic=topic,
            text=text.format(service=service_title),
            approved=True,
        )
        for i, (topic, text) in enumerate(rows)
    ]


def officer_replies(
    scenario: CardResponseScenario, service: str, service_title: str
) -> list[Reply]:
    """Built-in replies plus the scenario's own for the service (any-service ones too)."""
    own = [
        Reply(id=r.id, topic=r.topic, text=r.text, audio=r.audio, approved=r.approved)
        for r in scenario.service_replies
        if r.service in (None, "", service)
    ]
    return [*own, *builtin_replies(service, service_title)]


def greeting(service: str, service_title: str) -> str:
    return GENERIC_REPLIES[0][1].format(service=service_title)


def officer_scenario(
    scenario: CardResponseScenario, ref: ServiceCallRef, service_title: str
) -> CallIntakeScenario:
    """The officer as the dialog engine sees him: the same shape as a caller scenario, so the
    select / hybrid / generate providers and the voice pipeline work unchanged. The «caller»
    side is the officer, the «operator» side the dispatcher."""
    card = scenario.card
    facts = {
        "служба": service_title,
        "что знает": "ничего о происшествии: всё сообщает диспетчер",
    }
    return CallIntakeScenario(
        kind="call_intake",
        title=f"Звонок дежурному: {service_title}",
        ticket_ref=scenario.ticket_ref,
        difficulty=scenario.difficulty,
        norm_seconds=ref.norm_seconds,
        caller=CallerProfile(
            persona=OFFICER_PERSONA,
            voice=OFFICER_VOICE,
            noise=None,
            opening=greeting(ref.service, service_title),
            facts=facts,
            behaviour=OFFICER_BEHAVIOUR,
        ),
        replies=officer_replies(scenario, ref.service, service_title),
        required_topics=list(ref.required_facts),
        reference_card=ReferenceCard(
            incident_type=card.incident_type,
            signs_path=list(card.signs),
            flags=dict(card.flags),
            address=card.address,
            caller=card.caller,
            description=card.description,
            expected_services=[n.service for n in card.notified],
        ),
    )


def default_service_calls(scenario_body: Mapping) -> list[dict]:
    """The reference calls of a generated or seeded card: on an accepted card the dispatcher
    calls the officer of their own service with the four basic facts, plus ``access`` when
    the card names an entrance code or «нет доступа». A rejected card needs no call."""
    reference = scenario_body.get("reference") or {}
    if reference.get("decision") != "accept":
        return []
    card = scenario_body.get("card") or {}
    address = card.get("address") or {}
    flags = card.get("flags") or {}
    facts = ["address", "incident_type", "injured", "order_number"]
    if address.get("code") or flags.get("no_access"):
        facts.append("access")
    own = scenario_body.get("service")
    if not own:
        return []
    return [{"service": own, "required_facts": facts}]
