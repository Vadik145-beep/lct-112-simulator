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

import hashlib
import re
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

# How the duty officer names his own service out loud. The reference titles come from the
# classifier and carry the paperwork («Территориальные ОИВ (управы, префектуры)»), which a
# voice reads aloud brackets and all (as the caller's types did before #104).
SPOKEN_SERVICE_TITLES: dict[str, str] = {
    "gkh": "ЕДЦ ЖКХ",
    "moek": "МОЭК",
    "mosvodokanal": "Мосводоканала",
    "mosgaz": "Мосгаза",
    "gormost": "Гормоста",
    "moslift": "Мослифта",
    "territorial_oiv": "управы",
    "101": "пожарной охраны",
    "102": "полиции",
    "103": "скорой помощи",
    "104": "газовой службы",
}

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
    ("order_number", "Принял, направляю наряд {order}."),
    ("order_number", "Наряд {order}, он и выезжает."),
    ("access", "Понял, доступ есть."),
    ("access", "Доступ на объект есть? Кто встретит бригаду?"),
    ("confirm", "Информацию принял, направляю наряд {order}, бригада выезжает."),
    ("confirm", "Принято. Как будем на месте, доложу."),
    ("repeat", "Повторите, пожалуйста, плохо слышно."),
    ("unknown", "Это не ко мне, давайте по происшествию."),
]
# The officer's requests for a fact itself. Said after the dispatcher has passed that fact they
# sound deaf, so the dialog engine answers with the confirmation instead. The clarifying
# questions of a service («Запах газа где: в квартире, в подъезде, на улице?») are not here:
# they ask more than the dispatcher said.
FACT_REQUESTS: frozenset[str] = frozenset(
    {
        "Назовите адрес: улица, дом, корпус.",
        "Что именно произошло? Какой тип происшествия по карточке?",
        "Пострадавшие есть?",
        "Доступ на объект есть? Кто встретит бригаду?",
    }
)
assert FACT_REQUESTS <= {text for _, text in GENERIC_REPLIES}

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
# Состояния, в которых бригаду уже видно на месте: это знает и заявитель, если ему позвонить
# (app.domain.scenarios.caller_back).
SQUAD_STATES_ON_SCENE = frozenset({"arrived", "works_started", "works_done"})
# Replies of the squad leader during a report call. After the report the dispatcher asks the
# usual things: how long it will take, who is on site, whether help is needed, what about the
# people. The set is universal — no address, no service, so it fits any card.
REPORT_REPLIES: list[tuple[str, str]] = [
    ("confirm", "Принято, работаем дальше."),
    ("confirm", "Принято, остаёмся на связи."),
    ("confirm", "Понял вас, диспетчер."),
    ("confirm", "Так точно, продолжаем."),
    ("progress", "{report}"),
    ("progress", "Работаем, пока по плану. Как будет результат — доложу."),
    ("progress", "Ещё в работе, думаю час-полтора. Точнее скажу позже."),
    ("progress", "Пока без изменений, продолжаем."),
    ("progress", "На месте бригада и дежурная машина, сил хватает."),
    ("progress", "Дополнительных служб пока не требуется, справляемся."),
    ("progress", "Работаем вдвоём, людей хватает."),
    ("progress", "Участок огородили, проезд ограничили."),
    ("progress", "Опасности для жильцов нет."),
    ("address", "Мы на адресе по карточке, всё верно."),
    ("incident_type", "По происшествию всё как в карточке, работаем."),
    ("injured", "Пострадавших нет, людей вывели."),
    ("injured", "Медпомощь никому не требуется."),
    ("order_number", "Наряд {order}, по нему и работаем."),
    ("access", "Доступ есть, заявитель встретил."),
    ("access", "В подъезд попали, открыл консьерж."),
    ("repeat", "Повторяю: {report}"),
    ("unknown", "Диспетчер, это старший группы, докладываю по происшествию."),
]
SQUAD_LEADER_PERSONA = "squad_leader"
SQUAD_LEADER_BEHAVIOUR = (
    "старший группы реагирования на месте: докладывает коротко, по делу, отвечает "
    "на уточняющие вопросы "
    "диспетчера, повторяет доклад по просьбе"
)
# Default timeline of the squad (seconds after the previous milestone) and how the leader
# words each report; ``{comment}`` is the reference comment of the step, ``{address}`` the
# street and the house of the card.
DEFAULT_REPORTS: list[tuple[str, int, str]] = [
    (
        "response_started",
        30,
        "Алло, диспетчер? Это старший группы реагирования. Выехали по адресу: {address}.",
    ),
    ("arrived", 45, "Алло, это снова старший группы. Прибыли по адресу: {address}, приступаем."),
    ("works_started", 30, "Алло, диспетчер, старший группы. Докладываю: {comment}"),
    ("works_done", 60, "Алло, диспетчер? Старший группы. Работы завершены: {comment}"),
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


def order_number(scenario: CardResponseScenario, service: str) -> str:
    """Номер наряда службы: свой у каждой карточки и службы, но всегда один и тот же, чтобы
    диспетчер мог записать его в статус, а проверка — сверить. Наряд принадлежит службе,
    диспетчер ДДС его не назначает и не передаёт (решение пользователя 23.09.2026)."""
    key = f"{scenario.ticket_ref or scenario.title}|{service}".encode()
    digest = hashlib.sha1(key).hexdigest()  # noqa: S324 - номер, а не защита
    return str(1000 + int(digest[:4], 16) % 9000)


def squad_state(delivered: list[str]) -> str:
    """The squad's state from the reports already delivered (progress status codes)."""
    for status in ("works_done", "works_started", "arrived", "response_started"):
        if status in delivered:
            return status
    return SQUAD_STATE_PENDING


def spoken_service(service: str, service_title: str) -> str:
    """What the officer calls his service out loud: the spoken name when there is one, else
    the reference title without its bracketed paperwork."""
    spoken = SPOKEN_SERVICE_TITLES.get(service)
    if spoken:
        return spoken
    without = re.sub(r"\s*\([^()]*\)", "", service_title)
    return re.sub(r"\s{2,}", " ", without).strip(" ,;«»") or service_title


def builtin_replies(
    service: str, service_title: str, state: str = SQUAD_STATE_PENDING, order: str = ""
) -> list[Reply]:
    """The built-in replies of a service's officer, ids from ``BUILTIN_REPLY_ID_BASE``. The
    «progress» reply follows the squad's state, so its id changes with the state."""
    rows = [*GENERIC_REPLIES, *SERVICE_REPLIES.get(service, [])]
    spoken = spoken_service(service, service_title)
    replies = [
        Reply(
            id=BUILTIN_REPLY_ID_BASE + i,
            topic=topic,
            text=text.format(service=spoken, order=order or "наряд"),
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
    ref = next((c for c in scenario.reference.service_calls if c.service == service), None)
    order = (ref.order_number if ref else None) or order_number(scenario, service)
    return [*own, *builtin_replies(service, service_title, state, order)]


def greeting(service: str, service_title: str) -> str:
    return GENERIC_REPLIES[0][1].format(service=spoken_service(service, service_title))


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
        "твой наряд": ref.order_number or order_number(scenario, ref.service),
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


def report_replies(report: BrigadeReport, order: str = "") -> list[Reply]:
    """Replies of the squad leader in a report call: confirmations, the report again. The report
    said again («{report}», «Повторяю: {report}») plays the report's own recording when the
    card has one, instead of a synthesis of the same words."""
    return [
        Reply(
            id=BUILTIN_REPLY_ID_BASE + i,
            topic=topic,
            text=text.format(report=report.text, order=order or "наш"),
            audio=report.audio if "{report}" in text else None,
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
            facts={
                "служба": service_title,
                "наряд": order_number(scenario, scenario.service),
                "доклад": report.text,
                "статус": report.status,
            },
            behaviour=SQUAD_LEADER_BEHAVIOUR,
        ),
        replies=report_replies(report, order_number(scenario, scenario.service)),
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
        if status in ("works_done", "works_started") and comment[:1].isupper():
            if not comment[:2].isupper():  # ЦТП, ИТП stay as they are
                comment = comment[0].lower() + comment[1:]  # after «завершены:» / «докладываю:»
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
    # Наряд принадлежит службе: диспетчер его не передаёт, а записывает со слов дежурного.
    facts = ["address", "incident_type", "injured"]
    if address.get("code") or flags.get("no_access"):
        facts.append("access")
    own = scenario_body.get("service")
    if not own:
        return []
    return [{"service": own, "required_facts": facts}]
