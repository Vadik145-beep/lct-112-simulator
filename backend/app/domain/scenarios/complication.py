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

from collections.abc import Iterable, Mapping

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
# The specialist's call not answered rings once more, then the squad tells it in its next
# calls (stand 18.10.2026: the call dropped, the chief and the reports forgot the complication).
MAX_RINGS = 2
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


def problem(scenario: CardResponseScenario) -> str:
    return PROBLEMS.get(service_needed(scenario) or "", GENERIC_PROBLEM)


def calls_about(calls: Iterable[Mapping]) -> list[Mapping]:
    """The specialist's calls about the complication, in order."""
    return [c for c in calls if c.get("kind") == "report" and c.get("report_status") == STATUS]


def happened(calls: Iterable[Mapping]) -> bool:
    """The squad has run into the complication: the specialist has called (answered or not)."""
    return bool(calls_about(calls))


def heard(calls: Iterable[Mapping]) -> bool:
    """The dispatcher answered the specialist's call."""
    return any(c.get("answered") for c in calls_about(calls))


def settled(calls: Iterable[Mapping]) -> bool:
    """The specialist's call is over with: answered, or rung ``MAX_RINGS`` times."""
    rows = calls_about(calls)
    return any(c.get("answered") for c in rows) or len(rows) >= MAX_RINGS


def told(calls: Iterable[Mapping], service: str, *, besides: str | None = None) -> bool:
    """The dispatcher knows of the complication: he answered the specialist, or a later call
    of the own organization (not ``besides``, the call being built) has told him."""
    rows = list(calls)
    about = calls_about(rows)
    if not about:
        return False
    if heard(rows):
        return True
    since = str(about[0].get("started_at") or "")
    return any(
        c.get("answered")
        and c.get("id") != besides
        and c.get("service") == service
        and c.get("report_status") != STATUS
        and str(c.get("started_at") or "") >= since
        for c in rows
    )


def specialist_line(scenario: CardResponseScenario) -> str:
    """The specialist's own words when the dispatcher reaches him before hearing of it."""
    service = service_needed(scenario) or ""
    return f"{problem(scenario)}. Нужна помощь {spoken_service(service, service)}, сообщите им."


def unheard_line(scenario: CardResponseScenario) -> str:
    """What the squad says in its next calls when the specialist did not get through."""
    service = service_needed(scenario) or ""
    return (
        f"Специалист до вас не дозвонился: {problem(scenario).lower()}. "
        f"Нужна помощь {spoken_service(service, service)}, сообщите им."
    )


def fact(scenario: CardResponseScenario, calls: Iterable[Mapping]) -> str:
    """The complication as the own organization knows it, for the prompt of anyone of it."""
    service = service_needed(scenario) or ""
    name = spoken_service(service, service)
    if heard(calls):
        return (
            f"на месте осложнение: {problem(scenario).lower()}; специалист попросил диспетчера "
            f"вызвать помощь {name}. Если диспетчер говорит, что вызвал её, — «Понял, ждём, "
            "работаем дальше»; на вопрос о помощи — нужна помощь " + name
        )
    return (
        f"на месте осложнение: {problem(scenario).lower()}; специалист звонил диспетчеру, но "
        f"не дозвонился. Сам скажи диспетчеру и попроси сообщить {name}: нужна их помощь"
    )


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
