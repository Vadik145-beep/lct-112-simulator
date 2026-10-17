"""People of the dispatcher's own organization reachable by the phone (issue #263, customer's
recommendations of 16.10.2026, item 8: «руководитель организации, начальник отдела
реагирования, начальник аварийной бригады, специалист аварийной бригады»).

The squad chief is the officer of the own service's tab (decision of 17.10.2026): he sends the
squad. The head of the organization and the chief of the response department take reports and
send no one; the head is the one to tell when there are no vehicles.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.evaluation.schemas import (
    CallerProfile,
    CallIntakeScenario,
    CardResponseScenario,
    Reply,
)
from app.domain.scenarios.officers import reference_card_of

ROLE_HEAD = "head"
ROLE_RESPONSE_CHIEF = "response_chief"
ROLE_SQUAD_CHIEF = "squad_chief"
ROLE_SQUAD_SPECIALIST = "squad_specialist"
ROLE_TITLES: dict[str, str] = {
    ROLE_HEAD: "Руководитель организации",
    ROLE_RESPONSE_CHIEF: "Начальник отдела реагирования",
    ROLE_SQUAD_CHIEF: "Начальник аварийной бригады",
    ROLE_SQUAD_SPECIALIST: "Специалист аварийной бригады",
}
ROLES = tuple(ROLE_TITLES)
# The names a clean install starts with; the teacher changes them in «Справочник телефонов».
DEFAULT_NAMES: dict[str, str] = {
    ROLE_HEAD: "Соколов Андрей Петрович",
    ROLE_RESPONSE_CHIEF: "Лебедев Игорь Николаевич",
    ROLE_SQUAD_CHIEF: "Кузнецов Николай Иванович",
    ROLE_SQUAD_SPECIALIST: "Морозов Денис Сергеевич",
}


@dataclass(frozen=True)
class Person:
    """How a person of the organization talks on the phone: voice, manner and replies.
    ``{org}`` in a reply is the organization as said after the title («Мосводоканала»),
    ``{progress}`` where the squad is now."""

    persona: str
    voice: str
    behaviour: str
    replies: tuple[tuple[str, str], ...]


PERSONS: dict[str, Person] = {
    ROLE_HEAD: Person(
        "organization_head",
        "ru_male_3",
        "руководитель организации: занят, слушает краткий доклад (что, где, масштаб, что "
        "сделано), принимает решения по силам и средствам",
        (
            ("greeting", "Руководитель {org}, слушаю."),
            ("confirm", "Понял, принял. Держите на контроле."),
            ("progress", "Докладывайте по ходу работ."),
            ("repeat", "Повторите, не расслышал."),
            ("unknown", "Докладывайте коротко: что, где, что сделано."),
        ),
    ),
    ROLE_RESPONSE_CHIEF: Person(
        "response_chief",
        "ru_male_1",
        "начальник отдела реагирования: принимает доклад о происшествии, ставит на контроль, "
        "уточняет, какие силы направлены",
        (
            ("greeting", "Начальник отдела реагирования {org}, слушаю."),
            ("confirm", "Принял, ставлю на контроль. О ходе работ докладывайте."),
            ("progress", "{progress}"),
            ("repeat", "Не расслышал, повторите."),
            ("unknown", "Докладывайте: что случилось, где, какие силы направлены."),
        ),
    ),
    ROLE_SQUAD_SPECIALIST: Person(
        "squad_specialist",
        "ru_male_1",
        "специалист аварийной бригады: говорит о том, что видит сам, распоряжения на выезд "
        "принимает только начальник бригады",
        (
            ("greeting", "Специалист аварийной бригады, слушаю. {progress}"),
            ("confirm", "Понял, передам начальнику."),
            ("progress", "{progress}"),
            ("repeat", "Плохо слышно, повторите."),
            ("unknown", "Распоряжения на выезд — начальнику бригады, я на месте работаю."),
        ),
    ),
}
# Where the squad is, as its specialist says it (the squad's states of ``officers``).
SPECIALIST_PROGRESS: dict[str, str] = {
    "pending": "Собираемся, ждём команды на выезд.",
    "no_vehicles": "Стоим, машин нет.",
    "response_started": "Едем на адрес.",
    "arrived": "На месте, осматриваемся.",
    "works_started": "Работаем, пока без замечаний.",
    "works_done": "Закончили, сворачиваемся.",
}
# The dispatcher sends the squad through the wrong person (#263, «позвонил не тому»).
TOPIC_WRONG_CONTACT = "wrong_contact"
WRONG_CONTACT = "Распоряжение на выезд — начальнику аварийной бригады, звоните ему."
# Ids apart from the officers' (1000) and the caller's (2000) banks, a hundred per role.
REPLY_ID_BASE = 3000


def person_scenario(
    role: str, scenario: CardResponseScenario, spoken_org: str, progress: str
) -> CallIntakeScenario:
    """A person of the organization as the dialog engine sees him. ``progress`` is where the
    squad is, in the words of the duty officer (``officers.PROGRESS_REPLIES``); the specialist
    says it his own way."""
    person = PERSONS[role]
    rows = person.replies
    base = REPLY_ID_BASE + 100 * ROLES.index(role)
    replies = [
        Reply(
            id=base + i,
            topic=topic,
            text=text.format(org=spoken_org, progress=progress).strip(),
            approved=True,
        )
        for i, (topic, text) in enumerate(rows)
    ]
    return CallIntakeScenario(
        kind="call_intake",
        title=f"Звонок: {ROLE_TITLES[role]}",
        ticket_ref=scenario.ticket_ref,
        difficulty=scenario.difficulty,
        norm_seconds=scenario.norm_seconds,
        caller=CallerProfile(
            persona=person.persona,
            voice=person.voice,
            noise="street" if role == ROLE_SQUAD_SPECIALIST else None,
            opening=replies[0].text,
            facts={"организация": spoken_org, "где бригада": progress},
            behaviour=person.behaviour,
        ),
        replies=replies,
        required_topics=[],
        reference_card=reference_card_of(scenario),
    )
