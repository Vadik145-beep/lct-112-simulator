"""Duty officers of the city services (issue #36, customer: «звено Б → В») and the reports of
their squads (customer, 21.09.2026: «старший группы звонит в ДДС, докладывает об обстановке и
ходе работ, или диспетчер сам набирает старшего и уточняет ход работ — работают оба варианта»).

The dispatcher of a service receives the card and calls the officer of the responding unit to
pass the facts. The officer is played by the dialog engine with the profile below: calm,
short, asks for the address, the incident, the injured, the squad number, confirms receipt.
Every service has a built-in set of replies (topics = the facts of a service call plus
``greeting``, ``progress``, ``confirm``, ``repeat``, ``unknown``); a scenario may add its own
(``service_replies``), and generated ones wait for the teacher like the caller's.

After «Принята» the squad lives on a timeline (``reference.reports``): it departs, arrives,
works, finishes. At each milestone the squad leader calls the dispatcher with a report
(``report_scenario``), and when the dispatcher calls first, the officer answers about the
progress from the same state (``squad_state``, topic ``progress``).
"""

from __future__ import annotations

from collections.abc import Mapping

from app.domain.evaluation.schemas import (
    BrigadeReport,
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

# What the officer says about the response when the dispatcher asks before the squad
# reported itself (topic «progress»), by the squad's state. The state is the last report
# delivered: none yet → the squad is being sent; the report of «Начало реагирования» → on the
# way; «Прибытие» → on site; «Проведение работ» → working; «Работы завершены» → done.
SQUAD_STATE_PENDING = "pending"
PROGRESS_REPLIES: dict[str, str] = {
    SQUAD_STATE_PENDING: "Бригаду собираем, выезжаем. Как выедем — доложу.",
    "response_started": "Бригада в пути, едем на адрес. По прибытии доложим.",
    "arrived": "Бригада на месте, осматриваемся. Как начнём работы — доложу.",
    "works_started": "Работы ведутся, пока без замечаний. По окончании доложу.",
    "works_done": "Работы завершены, докладывал. Всё по карточке отражено.",
}
# Replies of the squad leader during a report call: the dispatcher confirms or asks again.
REPORT_REPLIES: list[tuple[str, str]] = [
    ("confirm", "Принято, работаем дальше."),
    ("confirm", "Так точно, продолжаем."),
    ("address", "Мы на адресе по карточке, всё верно."),
    ("incident_type", "По происшествию всё как в карточке, работаем."),
    ("injured", "Пострадавших нет, докладываю как есть."),
    ("order_number", "Наряд тот же, что вы передавали."),
    ("access", "Доступ есть, встретили."),
    ("repeat", "Повторяю: {report}"),
    ("progress", "{report}"),
    ("unknown", "Диспетчер, это старший наряда, докладываю по происшествию."),
]
SQUAD_LEADER_PERSONA = "squad_leader"
SQUAD_LEADER_BEHAVIOUR = (
    "старший наряда на месте: докладывает коротко, по делу, отвечает на уточняющие вопросы "
    "диспетчера, повторяет доклад по просьбе"
)
# Default timeline of the squad (seconds after the previous milestone) and how the leader
# words each report; ``{comment}`` is the reference comment of the step, ``{address}`` the
# street and the house of the card.
DEFAULT_REPORTS: list[tuple[str, int, str]] = [
    ("response_started", 30, "Диспетчер, старший наряда. Выехали по адресу: {address}."),
    ("arrived", 45, "Старший наряда. Прибыли по адресу: {address}, приступаем."),
    ("works_started", 30, "Старший наряда. {comment}"),
    ("works_done", 60, "Старший наряда. Работы завершены: {comment}"),
]
DEFAULT_WORK_COMMENTS: dict[str, str] = {
    "works_started": "Приступили к работам на месте.",
    "works_done": "Работы выполнены, пострадавших нет.",
}

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


def squad_state(delivered: list[str]) -> str:
    """The squad's state from the reports already delivered (progress status codes)."""
    for status in ("works_done", "works_started", "arrived", "response_started"):
        if status in delivered:
            return status
    return SQUAD_STATE_PENDING


def builtin_replies(
    service: str, service_title: str, state: str = SQUAD_STATE_PENDING
) -> list[Reply]:
    """The built-in replies of a service's officer, ids from ``BUILTIN_REPLY_ID_BASE``. The
    «progress» reply follows the squad's state, so its id changes with the state."""
    rows = [*GENERIC_REPLIES, *SERVICE_REPLIES.get(service, [])]
    replies = [
        Reply(
            id=BUILTIN_REPLY_ID_BASE + i,
            topic=topic,
            text=text.format(service=service_title),
            approved=True,
        )
        for i, (topic, text) in enumerate(rows)
    ]
    states = list(PROGRESS_REPLIES)
    replies.append(
        Reply(
            id=BUILTIN_REPLY_ID_BASE + len(rows) + states.index(state),
            topic="progress",
            text=PROGRESS_REPLIES[state],
            approved=True,
        )
    )
    return replies


def officer_replies(
    scenario: CardResponseScenario,
    service: str,
    service_title: str,
    state: str = SQUAD_STATE_PENDING,
) -> list[Reply]:
    """Built-in replies plus the scenario's own for the service (any-service ones too)."""
    own = [
        Reply(id=r.id, topic=r.topic, text=r.text, audio=r.audio, approved=r.approved)
        for r in scenario.service_replies
        if r.service in (None, "", service)
    ]
    return [*own, *builtin_replies(service, service_title, state)]


def greeting(service: str, service_title: str) -> str:
    return GENERIC_REPLIES[0][1].format(service=service_title)


def _reference_card(scenario: CardResponseScenario) -> ReferenceCard:
    card = scenario.card
    return ReferenceCard(
        incident_type=card.incident_type,
        signs_path=list(card.signs),
        flags=dict(card.flags),
        address=card.address,
        caller=card.caller,
        description=card.description,
        expected_services=[n.service for n in card.notified],
    )


def officer_scenario(
    scenario: CardResponseScenario,
    ref: ServiceCallRef,
    service_title: str,
    state: str = SQUAD_STATE_PENDING,
) -> CallIntakeScenario:
    """The officer as the dialog engine sees him: the same shape as a caller scenario, so the
    select / hybrid / generate providers and the voice pipeline work unchanged. The «caller»
    side is the officer, the «operator» side the dispatcher. ``state`` is the squad's state
    (``squad_state``): the officer answers about the progress from it."""
    facts = {
        "служба": service_title,
        "что знает": "о происшествии — только то, что сообщает диспетчер",
        "где бригада": PROGRESS_REPLIES[state],
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
        replies=officer_replies(scenario, ref.service, service_title, state),
        required_topics=list(ref.required_facts),
        reference_card=_reference_card(scenario),
    )


def report_replies(report: BrigadeReport) -> list[Reply]:
    """Replies of the squad leader in a report call: confirmations, the report again."""
    return [
        Reply(
            id=BUILTIN_REPLY_ID_BASE + i,
            topic=topic,
            text=text.format(report=report.text),
            approved=True,
        )
        for i, (topic, text) in enumerate(REPORT_REPLIES)
    ]


def report_scenario(
    scenario: CardResponseScenario, report: BrigadeReport, service_title: str
) -> CallIntakeScenario:
    """The squad leader's report as the dialog engine sees it: the leader «calls» with the
    report as the opening, then confirms or repeats. Same shape as the officer's call."""
    return CallIntakeScenario(
        kind="call_intake",
        title=f"Доклад бригады: {service_title}",
        ticket_ref=scenario.ticket_ref,
        difficulty=scenario.difficulty,
        norm_seconds=scenario.norm_seconds,
        caller=CallerProfile(
            persona=SQUAD_LEADER_PERSONA,
            voice=OFFICER_VOICE,
            noise="street",
            opening=report.text,
            facts={"служба": service_title, "доклад": report.text, "статус": report.status},
            behaviour=SQUAD_LEADER_BEHAVIOUR,
        ),
        replies=report_replies(report),
        required_topics=[],
        reference_card=_reference_card(scenario),
    )


def _spoken_address(card: Mapping) -> str:
    address = card.get("address") or {}
    parts = [address.get("street") or ""]
    if address.get("house"):
        parts.append(f"дом {address['house']}")
    if address.get("building"):
        parts.append(f"корпус {address['building']}")
    text = ", ".join(p for p in parts if p)
    return text or (address.get("descriptive") or "адрес по карточке")


def default_reports(scenario_body: Mapping) -> list[dict]:
    """The squad's reports of a generated or seeded card: one per progress status of the
    reference chain, in the chain's order, worded from the reference comments. A rejected
    card has no squad and no reports."""
    reference = scenario_body.get("reference") or {}
    if reference.get("decision") != "accept":
        return []
    chain = {s.get("status"): s for s in reference.get("status_chain") or [] if s.get("status")}
    card = scenario_body.get("card") or {}
    address = _spoken_address(card)
    reports: list[dict] = []
    for status, after, template in DEFAULT_REPORTS:
        if status not in chain:
            continue
        comment = (chain[status].get("comment_example") or "").strip()
        comment = comment or DEFAULT_WORK_COMMENTS.get(status, "")
        if comment and not comment.endswith((".", "!", "?")):
            comment += "."
        if status == "works_done" and comment[:1].isupper() and not comment[:2].isupper():
            comment = comment[0].lower() + comment[1:]  # after «Работы завершены:»
        text = template.format(address=address, comment=comment).strip()
        reports.append({"status": status, "after_seconds": after, "text": text})
    return reports


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
