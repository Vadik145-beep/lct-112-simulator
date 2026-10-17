"""A complication on site (issue #263, decision of 17.10.2026: «сложность 3 — осложнение на
месте от бригады: диспетчер должен оповестить ещё службу»).

On a card of difficulty 3 the squad, once on site, finds what it cannot handle alone: the
specialist of the squad calls the dispatcher and asks for another service. That service is one
of the card's own (a tab of the services strip with its handset), otherwise the dispatcher has
nowhere to call and the card goes without a complication (decision of 17.10.2026).

The call is a squad report on the timeline (``kind: "report"``, status ``complication``) put
right after the report of the arrival; it is not a status of the card, so the status checks of
the reports skip it. The dispatcher is expected to call the named service after it.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.domain.evaluation.schemas import BrigadeReport, CallIntakeScenario, CardResponseScenario
from app.domain.scenarios.officers import spoken_service

MIN_DIFFICULTY = 3
STATUS = "complication"
TITLE = "Осложнение на месте"
# After the report of the arrival: the squad has looked round.
AFTER_STATUS = "arrived"
AFTER_SECONDS = 25
SPECIALIST_PERSONA = "squad_specialist"
SPECIALIST_VOICE = "ru_male_1"  # not the squad chief's voice: another person on the line
SPECIALIST_BEHAVIOUR = (
    "специалист аварийной бригады на месте: докладывает об осложнении, просит вызвать "
    "другую службу, на вопросы отвечает коротко"
)
STUDIO_KEY = "_specialist"

# What the squad runs into, by the service it needs.
PROBLEMS: dict[str, str] = {
    "mosgaz": "Запах газа в подвале, похоже, задели трубу",
    "moek": "Задели теплотрассу, из-под земли парит",
    "mosvodokanal": "Вода уходит в колодец, похоже, порыв водопровода",
    "gkh": "Залило электрощиток в подъезде, нужен электрик",
    "moslift": "В лифте застрял жилец, двери не открываются",
    "gormost": "По пролёту пошла трещина, движение опасно",
    "territorial_oiv": "Нужно перекрыть двор, жильцы не дают работать",
    "101": "Задымление в подвале, похоже, загорелся кабель",
    "102": "На месте конфликт, нас не пускают к стояку",
    "103": "Жильцу стало плохо, нужна медицинская помощь",
    "104": "Запах газа в подъезде",
}
GENERIC_PROBLEM = "Сами не справимся"
OFF_THE_POINT = "Диспетчер, это специалист бригады, нам нужна помощь другой службы."


def service_needed(scenario: CardResponseScenario) -> str | None:
    """The other service of the card the squad asks for, or ``None`` when there is none."""
    others = [n.service for n in scenario.card.notified if n.service != scenario.service]
    known = [s for s in others if s in PROBLEMS]
    return (known or others or [None])[0]


def applies(scenario: CardResponseScenario) -> bool:
    if scenario.difficulty < MIN_DIFFICULTY or scenario.reference.decision != "accept":
        return False
    if AFTER_STATUS not in {r.status for r in scenario.reference.reports}:
        return False
    return service_needed(scenario) is not None


def report(
    scenario: CardResponseScenario, titles: Mapping[str, str] | None = None
) -> BrigadeReport:
    """The specialist's call. ``titles`` are the reference titles of the services, for a
    service without a spoken name; the spoken ones follow «помощь» («Мосгаза»)."""
    service = service_needed(scenario) or ""
    problem = PROBLEMS.get(service, GENERIC_PROBLEM)
    spoken = spoken_service(service, (titles or {}).get(service, service))
    text = (
        "Алло, диспетчер? Специалист аварийной бригады, мы на месте. "
        f"{problem}. Нужна помощь {spoken}, сообщите им."
    )
    return BrigadeReport(status=STATUS, after_seconds=AFTER_SECONDS, text=text)


def specialist(leader: CallIntakeScenario) -> CallIntakeScenario:
    """The specialist on the line instead of the squad chief: his voice, his manner, and his
    own words when the dispatcher talks off the point."""
    caller = leader.caller.model_copy(
        update={
            "persona": SPECIALIST_PERSONA,
            "voice": SPECIALIST_VOICE,
            "behaviour": SPECIALIST_BEHAVIOUR,
        }
    )
    replies = [
        r.model_copy(update={"text": OFF_THE_POINT, "audio": None}) if r.topic == "unknown" else r
        for r in leader.replies
    ]
    title = leader.title.replace("Доклад бригады", "Звонок специалиста бригады", 1)
    return leader.model_copy(update={"caller": caller, "replies": replies, "title": title})


def timeline(
    scenario: CardResponseScenario, titles: Mapping[str, str] | None = None
) -> list[BrigadeReport]:
    """The squad's reports with the complication after the arrival, when the card has one."""
    reports = list(scenario.reference.reports)
    if not applies(scenario):
        return reports
    at = next(i for i, r in enumerate(reports) if r.status == AFTER_STATUS) + 1
    return [*reports[:at], report(scenario, titles), *reports[at:]]
