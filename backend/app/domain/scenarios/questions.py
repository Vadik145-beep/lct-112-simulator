"""The everyday questions of a dispatcher on the phone (stand 19.10.2026: «Нужна ли
дополнительная помощь?» got «Это не ко мне, давайте по происшествию», «Когда примерно
закончите?» — «Принято, работаем дальше», «Как обстановка на месте?» — the report again).

With prerecorded replies the model picks a line out of a long list and misses these. They are
answered here by meaning and by where the squad is, mostly with lines recorded in the studio
for the squad (the voice of the squad chief too). Only the squad chief of the dispatcher's own
service and the squad on a report call answer them; the cloud mode never comes here.
"""

from __future__ import annotations

import re

from app.domain.evaluation.text import normalize_text

INTENT_HELP = "help"
INTENT_SITUATION = "situation"
INTENT_FINISH = "finish"
INTENT_ARRIVAL = "arrival"
INTENT_FORCES = "forces"
INTENT_RESIDENTS = "residents"
INTENT_THANKS = "thanks"

# Order matters: «когда закончите» before «как дела», «сколько машин» before «когда».
_INTENTS: list[tuple[str, re.Pattern[str]]] = [
    (
        INTENT_HELP,
        re.compile(
            r"(нужн|требу|надо)\w*\b.{0,40}\b(помощ|подмог|подкреплен|служб|сил\b|техник|от нас)"
            r"|(помощ|подмог)\w*\b.{0,15}\b(нужн|требу|надо)"
            r"|чем (вам )?помочь|что(-то)? (еще|ещё) (нужно|надо)"
        ),
    ),
    (
        INTENT_FORCES,
        re.compile(r"сколько\b.{0,20}\b(машин|человек|людей|бригад|сотрудник|экипаж|народ)"),
    ),
    (
        INTENT_FINISH,
        re.compile(
            r"(когда|скоро|сколько|долго)\b.{0,30}\b(законч|заверш|справит|продлит|займ|"
            r"восстанов|устран|по времени)"
        ),
    ),
    (
        INTENT_ARRIVAL,
        re.compile(
            r"(когда|скоро|через сколько|долго)\b.{0,30}\b(будете|приедете|прибудете|доедете|"
            r"подъедете|на месте|на адресе)"
        ),
    ),
    (INTENT_RESIDENTS, re.compile(r"жалоб|претензи|жител|жильц")),
    (
        INTENT_SITUATION,
        re.compile(
            r"\b(как|какая|что)\b.{0,15}\b(обстановк|ситуаци|дела|у вас|там|на месте|происходит)"
        ),
    ),
    (
        INTENT_THANKS,
        re.compile(
            r"^(ну |все |всё )?(спасибо|благодарю|до связи|жду доклад|ждем доклад)\b"
            r"|^(ну )?(принял|понял|принято|хорошо|ясно|добро|отлично)( спасибо)?$"
        ),
    ),
]


# «Добрый день, это диспетчер ДДС» before anything else: the chief asks what happened (stand
# 19.10.2026: the model picked «Повторите, плохо слышно»).
_GREETING = re.compile(
    r"^(алло|ало|здравствуйте|добрый (день|вечер)|доброе утро|приветствую)\b|\bэто диспетчер"
)


def is_greeting(text: str) -> bool:
    return bool(_GREETING.search(normalize_text(text)))


def intent_of(text: str) -> str | None:
    """What the dispatcher asks about, or ``None`` for anything else."""
    said = normalize_text(text)
    if not said:
        return None
    return next((name for name, pattern in _INTENTS if pattern.search(said)), None)


# Squad states (``officers.PROGRESS_REPLIES`` keys and report statuses).
_BEFORE_SCENE = frozenset({"pending", "waiting", "no_vehicles", "response_started"})
_DONE = "works_done"

# Lines recorded in the studio for the squad (``officers.REPORT_REPLIES``) and its new ones.
NO_HELP = "Дополнительных служб пока не требуется, справляемся."
STILL_WORKING = "Ещё в работе, думаю час-полтора. Точнее скажу позже."
BY_PLAN = "Работаем, пока по плану. Как будет результат — доложу."
FORCES_ON_SCENE = "На месте бригада и дежурная машина, сил хватает."
NO_DANGER = "Опасности для жильцов нет."
ROGER = "Понял вас, диспетчер."
GOING_ON = "Принято, работаем дальше."
TAKEN = "Принял."
# Without a studio recording (the stand's voice says them).
FORCES_ON_WAY = "Выезжает одна машина с бригадой, людей хватает."
ALREADY_DONE = "Уже закончили, работы завершены."
ALL_CALM = "Всё сделано, обстановка спокойная."
ALREADY_THERE = "Мы уже на месте."


def chief_answer(intent: str, state: str, progress: str) -> str | None:
    """The squad chief of the own service, once he has sent the squad (``state`` is where it
    is, ``progress`` the line for it). Before that he has nothing to say on these and the
    usual way goes on (he asks what happened and where)."""
    before = state in _BEFORE_SCENE
    if intent == INTENT_HELP:
        return NO_HELP
    if intent == INTENT_SITUATION:
        return progress
    if intent == INTENT_ARRIVAL:
        return progress if before else ALREADY_THERE
    if intent == INTENT_FINISH:
        return ALREADY_DONE if state == _DONE else progress if before else STILL_WORKING
    if intent == INTENT_FORCES:
        return FORCES_ON_WAY if before else FORCES_ON_SCENE
    if intent == INTENT_RESIDENTS:
        return progress if before else NO_DANGER
    if intent == INTENT_THANKS:
        return TAKEN
    return None


def report_answer(intent: str, status: str, progress: str) -> str | None:
    """The squad on a report call; ``status`` is the report's, ``progress`` the chief's line for
    that state. A complication is answered by its own way (the squad needs another service),
    so «помощь нужна?» is not here then."""
    done = status == _DONE
    before = status in _BEFORE_SCENE
    if intent == INTENT_HELP:
        return NO_HELP
    if intent == INTENT_SITUATION:
        return ALL_CALM if done else progress if before else BY_PLAN
    if intent == INTENT_ARRIVAL:
        return progress if before else ALREADY_THERE
    if intent == INTENT_FINISH:
        return ALREADY_DONE if done else progress if before else STILL_WORKING
    if intent == INTENT_FORCES:
        return FORCES_ON_WAY if before else FORCES_ON_SCENE
    if intent == INTENT_RESIDENTS:
        return NO_DANGER
    if intent == INTENT_THANKS:
        return ROGER if done else GOING_ON
    return None
