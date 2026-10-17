"""People of the dispatcher's own organization reachable by the phone (issue #263, customer's
recommendations of 16.10.2026, item 8: «руководитель организации, начальник отдела
реагирования, начальник аварийной бригады, специалист аварийной бригады»).

The squad chief is the officer of the own service's tab (decision of 17.10.2026): he sends the
squad. The head of the organization and the chief of the response department take reports and
send no one; the head is the one to tell when there are no vehicles.
"""

from __future__ import annotations

from collections.abc import Sequence

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

HEAD_PERSONA = "organization_head"
HEAD_VOICE = "ru_male_3"
HEAD_BEHAVIOUR = (
    "руководитель организации: занят, слушает краткий доклад (что, где, масштаб, что "
    "сделано), принимает решения по силам и средствам"
)
# Ids apart from the officers' (1000) and the caller's (2000) banks.
HEAD_REPLY_ID_BASE = 3000
HEAD_REPLIES: list[tuple[str, str]] = [
    ("greeting", "Руководитель {org}, слушаю."),
    ("confirm", "Понял, принял. Держите на контроле."),
    ("progress", "Докладывайте по ходу работ."),
    ("repeat", "Повторите, не расслышал."),
    ("unknown", "Докладывайте коротко: что, где, что сделано."),
]


def head_scenario(
    scenario: CardResponseScenario,
    spoken_org: str,
    *,
    extra: Sequence[tuple[str, str]] = (),
) -> CallIntakeScenario:
    """The head of the organization as the dialog engine sees him; ``spoken_org`` is the
    organization said after «Руководитель» («Мосводоканала»)."""
    rows = [*HEAD_REPLIES, *extra]
    replies = [
        Reply(
            id=HEAD_REPLY_ID_BASE + i,
            topic=topic,
            text=text.format(org=spoken_org),
            approved=True,
        )
        for i, (topic, text) in enumerate(rows)
    ]
    return CallIntakeScenario(
        kind="call_intake",
        title=f"Звонок: {ROLE_TITLES[ROLE_HEAD]}",
        ticket_ref=scenario.ticket_ref,
        difficulty=scenario.difficulty,
        norm_seconds=scenario.norm_seconds,
        caller=CallerProfile(
            persona=HEAD_PERSONA,
            voice=HEAD_VOICE,
            noise=None,
            opening=replies[0].text,
            facts={"организация": spoken_org},
            behaviour=HEAD_BEHAVIOUR,
        ),
        replies=replies,
        required_topics=[],
        reference_card=reference_card_of(scenario),
    )
