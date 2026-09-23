"""Typical error detectors: one named pure function per code of ``typical_errors``.

Each detector receives the prepared context of an attempt and returns an ``ErrorItem`` when the
error is present, otherwise ``None``. Penalties and titles come from the memo data
(``app.domain.reference_data.TYPICAL_ERRORS``); explanations are written for the student.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from math import ceil

from rapidfuzz import fuzz

from app.domain.evaluation import data_check
from app.domain.evaluation import status_machine as sm
from app.domain.evaluation.result import ErrorItem
from app.domain.evaluation.schemas import (
    CallIntakeAttempt,
    CallIntakeScenario,
    CardResponseAttempt,
    CardResponseScenario,
    StatusEntry,
)
from app.domain.evaluation.service_call import facts_from_dialog
from app.domain.evaluation.text import normalize_text
from app.domain.evaluation.timing import seconds_between
from app.domain.reference_data import TYPICAL_ERRORS

ERRORS: dict[str, dict] = {e["code"]: e for e in TYPICAL_ERRORS}

# Phrases that state a refusal (memo examples, pages 28-30).
REFUSAL_PHRASES = (
    "не обслужива",
    "не в компетенции",
    "не наша территория",
    "не наш объект",
    "не относится к",
    "дубл",
    "по другой карточке",
    "по кп",
    "нет договора",
    "реагирование не будет",
    "реагировать не будем",
    "не выезжа",
)
# Mentions of another service in a refusal comment («в компетенции 102», «реагирует 101»).
OTHER_SERVICE_PHRASES = (
    "101",
    "102",
    "103",
    "104",
    "мчс",
    "полици",
    "скорая",
    "смп",
    "мосгаз",
    "мослифт",
    "моэк",
    "мосводоканал",
    "жкх",
    "управляющ",
    "ук ",
    "другая служба",
    "другой службы",
    "другую службу",
    "реагирует",
    "реагируют",
    "уже выехал",
    "уже на месте",
)
# Information about hand-over the memo wants in a refusal comment (page 30).
TRANSFER_PHRASES = (
    "передан",
    "передал",
    "передач",
    "сообщ",
    "проинформирован",
    "направлен",
    "уведомл",
    "доведен",
    "переадрес",
    "позвонил",
    "связал",
    "огорожен",
    "устанавливается",
)
DUPLICATE_REASONS = frozenset({"duplicate", "other_card"})
DUPLICATE_PHRASES = ("дубл", "по другой карточке", "по кп", "повторн")
RESPONSE_PHRASES = ("наряд", "выех", "выезд", "бригада", "направлен", "на месте")


def _has(text: str | None, phrases: tuple[str, ...]) -> bool:
    normalized = normalize_text(text)
    return bool(normalized) and any(p in normalized for p in phrases)


def _item(code: str, explanation: str, critical: bool = False) -> ErrorItem:
    spec = ERRORS[code]
    return ErrorItem(
        code=code,
        title=spec["title"],
        penalty=spec["penalty"],
        explanation=explanation,
        memo_ref=spec.get("memo_ref"),
        critical=critical,
    )


# --- card_response ---------------------------------------------------------------------------


@dataclass
class CardContext:
    scenario: CardResponseScenario
    attempt: CardResponseAttempt
    log: list[StatusEntry]  # valid dispatcher entries, in order (system statuses removed)
    no_reject: bool = False
    statuses: list[str] = field(init=False)
    primary: StatusEntry | None = field(init=False)  # first «Принята» / «Не принята»
    final_primary: StatusEntry | None = field(init=False)  # last of them (after a correction)

    def __post_init__(self) -> None:
        self.statuses = [e.status for e in self.log]
        primaries = [e for e in self.log if e.status in sm.PRIMARY_STATUSES]
        self.primary = primaries[0] if primaries else None
        self.final_primary = primaries[-1] if primaries else None

    @property
    def reference_accepts(self) -> bool:
        return self.scenario.reference.decision == "accept"

    def entries(self, status: str) -> list[StatusEntry]:
        return [e for e in self.log if e.status == status]

    def reject_entries(self) -> list[StatusEntry]:
        return [e for e in self.log if e.status in sm.REJECT_STATUSES]

    def mentions_other_service(self, entry: StatusEntry) -> bool:
        return _has(entry.comment, OTHER_SERVICE_PHRASES)


def _reason_is_duplicate(entry: StatusEntry) -> bool:
    return entry.reject_reason in DUPLICATE_REASONS or _has(entry.comment, DUPLICATE_PHRASES)


def no_status(ctx: CardContext) -> ErrorItem | None:
    if ctx.primary is not None:
        return None
    return _item(
        "no_status",
        "Карточка направлена в службу, а «Принята» или «Не принята» так и не проставлены: "
        "карточка ушла в «Не оповещено».",
    )


def late_primary(ctx: CardContext) -> ErrorItem | None:
    if ctx.primary is None:
        return None
    seconds = seconds_between(ctx.attempt.issued_at, ctx.primary.at) or 0.0
    norm = ctx.scenario.norm_seconds
    if seconds <= norm:
        return None
    return _item(
        "late_primary",
        f"«{sm.title(ctx.primary.status)}» проставлена через {seconds:.0f} с после направления "
        f"карточки при нормативе {norm} с.",
    )


def status_mismatch(ctx: CardContext) -> ErrorItem | None:
    for entry in ctx.entries(sm.ACCEPTED):
        if _has(entry.comment, REFUSAL_PHRASES):
            return _item(
                "status_mismatch",
                f"Проставлена «Принята», а в комментарии отказ: «{entry.comment}». "
                "Если реагирования не будет, статус должен быть «Не принята».",
            )
    for entry in ctx.entries(sm.REJECTED):
        if (entry.order_number or "").strip() or _has(entry.comment, RESPONSE_PHRASES):
            return _item(
                "status_mismatch",
                "Проставлена «Не принята», а по комментарию реагирование ведётся: "
                f"«{entry.comment}». Если служба выезжает, статус должен быть «Принята».",
            )
    return None


def competence_refusal(ctx: CardContext) -> ErrorItem | None:
    if not ctx.reference_accepts or ctx.final_primary is None:
        return None
    if ctx.final_primary.status != sm.REJECTED:
        return None
    if not ctx.mentions_other_service(ctx.final_primary):
        return None
    return _item(
        "competence_refusal",
        f"Отказ «{ctx.final_primary.comment}»: на происшествие могут реагировать несколько "
        "служб, реагирование другой службы не снимает ответственности со своей.",
    )


def profile_refusal(ctx: CardContext) -> ErrorItem | None:
    if not ctx.reference_accepts or ctx.final_primary is None:
        return None
    if ctx.final_primary.status != sm.REJECTED:
        return None
    if ctx.mentions_other_service(ctx.final_primary):
        return None  # that case is competence_refusal
    comment = ctx.final_primary.comment or "без комментария"
    return _item(
        "profile_refusal",
        f"«Не принята» ({comment}) по профильному происшествию: оно входит в зону "
        "ответственности службы, отказ неправомерен.",
    )


def empty_reject_comment(ctx: CardContext) -> ErrorItem | None:
    for entry in ctx.reject_entries():
        if not (entry.comment or "").strip():
            return _item(
                "empty_reject_comment",
                f"«{sm.title(entry.status)}» без комментария: нужно указать причину отказа "
                "и кому передана информация.",
            )
    return None


def incomplete_comment(ctx: CardContext) -> ErrorItem | None:
    for entry in ctx.reject_entries():
        comment = (entry.comment or "").strip()
        if not comment or _reason_is_duplicate(entry):
            continue
        if _has(comment, TRANSFER_PHRASES):
            continue
        return _item(
            "incomplete_comment",
            f"В комментарии «{comment}» есть причина отказа, но нет данных о передаче "
            "информации: куда передано и что сделано.",
        )
    return None


def progress_missing(ctx: CardContext) -> ErrorItem | None:
    if sm.ACCEPTED not in ctx.statuses:
        return None
    if sm.WORKS_REFUSED in ctx.statuses:
        return None  # works never started, progress statuses are not expected
    expected = [s for s in ctx.scenario.reference.status_chain if s.status in sm.PROGRESS_STATUSES]
    if not expected:
        return None
    missing = [s.status for s in expected if s.status not in ctx.statuses]
    without_comment = [
        s.status
        for s in expected
        if s.comment_example
        and s.status in ctx.statuses
        and not any((e.comment or "").strip() for e in ctx.entries(s.status))
    ]
    if not missing and not without_comment:
        return None
    parts = []
    if missing:
        parts.append("не проставлены " + ", ".join(f"«{sm.title(s)}»" for s in missing))
    if without_comment:
        parts.append("без комментария " + ", ".join(f"«{sm.title(s)}»" for s in without_comment))
    return _item(
        "progress_missing",
        "Ход работ не отражён в карточке: " + "; ".join(parts) + ".",
    )


def duplicate_accepted(ctx: CardContext) -> ErrorItem | None:
    reference = ctx.scenario.reference
    if reference.decision != "reject" or reference.reject_reason not in DUPLICATE_REASONS:
        return None
    if ctx.primary is None or ctx.primary.status != sm.ACCEPTED:
        return None
    earlier = ctx.scenario.duplicate_of
    where = f" (карточка {earlier})" if earlier else ""
    return _item(
        "duplicate_accepted",
        "Проставлена «Принята» по повторной карточке того же происшествия; реагирование идёт "
        f"по другой карточке{where}. Следовало проставить «Не принята: дубль».",
    )


def wrong_accept_unfixed(ctx: CardContext) -> ErrorItem | None:
    if ctx.scenario.reference.decision != "reject":
        return None
    if sm.ACCEPTED not in ctx.statuses or sm.WORKS_REFUSED in ctx.statuses:
        return None
    return _item(
        "wrong_accept_unfixed",
        "«Принята» проставлена ошибочно: реагирования не будет, а «Отказ от выполнения "
        "работ» с комментарием так и не проставлен.",
    )


def wrong_final_status(ctx: CardContext) -> ErrorItem | None:
    if ctx.no_reject:
        return None  # service 103 closes with «Работы завершены» by the memo
    done = ctx.entries(sm.WORKS_DONE)
    if not done:
        return None
    chain = ctx.scenario.reference.status_chain
    expects_refusal = bool(chain) and chain[-1].status == sm.WORKS_REFUSED
    for entry in done:
        if _has(entry.comment, REFUSAL_PHRASES) or expects_refusal:
            return _item(
                "wrong_final_status",
                f"Карточка закрыта «Работы завершены» ({entry.comment or 'без комментария'}), "
                "хотя работы не проводились: следовало проставить «Отказ от выполнения работ» "
                "с тем же комментарием.",
            )
    return None


def error_missed(ctx: CardContext) -> ErrorItem | None:
    """A planted operator mistake the dispatcher did not flag (issue #35)."""
    outcome = data_check.check(ctx.scenario, ctx.attempt)
    if not outcome.missed:
        return None
    fields = ", ".join(
        f"«{data_check.field_title(e.field)}»: в карточке {_shown(e.wrong_value, e.wrong_label)}, "
        f"верно {_shown(e.correct_value, e.correct_label)}"
        for e in outcome.missed
    )
    return _item(
        "error_missed",
        "В карточке есть ошибка оператора 112, которую следовало найти и отметить: "
        f"{fields}. Данные карточки проверяются до принятия решения: по ним выезжает наряд.",
    )


def false_alarm(ctx: CardContext) -> ErrorItem | None:
    """The dispatcher flagged a field that was right (issue #35)."""
    outcome = data_check.check(ctx.scenario, ctx.attempt)
    if not outcome.false_alarms:
        return None
    fields = ", ".join(f"«{data_check.field_title(f.field)}»" for f in outcome.false_alarms)
    return _item(
        "false_alarm",
        f"Отмечено как ошибка верное поле: {fields}. Прежде чем править карточку, сверьте "
        "поле с описанием со слов заявителя.",
    )


def _shown(value: str, label: str | None) -> str:
    return f"«{label}»" if label else f"«{value}»"


def _informed_services(ctx: CardContext) -> tuple[set[str], set[str]]:
    """Services the officer of which picked up, split by whether the dispatcher passed any of
    the facts the card requires. Reaching somebody is not informing them (issue #127)."""
    reached: set[str] = set()
    told: set[str] = set()
    required_by_service = {
        r.service: r.required_facts for r in ctx.scenario.reference.service_calls
    }
    for call in ctx.attempt.service_calls:
        if call.kind != "outgoing" or not call.answered:
            continue
        reached.add(call.service)
        required = required_by_service.get(call.service) or []
        passed = facts_from_dialog(ctx.scenario.card, call.dialog)
        if not required or any(fact in passed for fact in required):
            told.add(call.service)
    return reached, told


def service_not_informed(ctx: CardContext) -> ErrorItem | None:
    """The card is accepted, but a required service officer was never reached (issue #36)."""
    required = ctx.scenario.reference.service_calls
    if not required or ctx.final_primary is None or ctx.final_primary.status != sm.ACCEPTED:
        return None
    reached, _ = _informed_services(ctx)
    missing = [r.service for r in required if r.service not in reached]
    if not missing:
        return None
    return _item(
        "service_not_informed",
        "Карточка принята, но в службу по телефону не позвонили: "
        + ", ".join(f"«{s}»" for s in missing)
        + ". Руководителю службы передаются адрес, тип происшествия, пострадавшие и номер наряда.",
    )


def service_call_silent(ctx: CardContext) -> ErrorItem | None:
    """The officer picked up and the dispatcher said nothing the card required (issue #127)."""
    required = ctx.scenario.reference.service_calls
    if not required or ctx.final_primary is None or ctx.final_primary.status != sm.ACCEPTED:
        return None
    reached, told = _informed_services(ctx)
    silent = [r.service for r in required if r.service in reached and r.service not in told]
    if not silent:
        return None
    return _item(
        "service_call_silent",
        "Дозвонились в службу, но ничего не передали: "
        + ", ".join(f"«{s}»" for s in silent)
        + ". Дежурному нужны адрес, тип происшествия, пострадавшие и номер наряда — "
        "без них наряду ехать некуда.",
    )


# How long after a report the matching status may still be set without a penalty.
REPORT_REACTION_SECONDS = 120
# Statuses the squad reports about: the progress ones and the closing «Работы завершены».
REPORTED_STATUSES = (*sm.PROGRESS_STATUSES, sm.WORKS_DONE)


def _reports_delivered(ctx: CardContext) -> dict[str, StatusEntry | None]:
    """Progress status → the squad's report call that announced it (the first one). A report
    the dispatcher never picked up told them nothing, so it does not count as delivered; not
    answering is its own error (``report_not_taken``, issue #103)."""
    delivered: dict[str, StatusEntry | None] = {}
    for call in ctx.attempt.service_calls:
        status = call.report_status
        if call.kind == "report" and call.answered and status and status not in delivered:
            delivered[status] = StatusEntry(status=status, at=call.started_at)
    return delivered


def status_before_report(ctx: CardContext) -> ErrorItem | None:
    """A progress status set before the squad reported it (customer, 21.09.2026: the
    dispatcher learns about the departure, arrival and works by phone). Only for cards whose
    reference has reports; a status the reports never cover is not judged."""
    expected = {r.status for r in ctx.scenario.reference.reports}
    if not expected or sm.ACCEPTED not in ctx.statuses:
        return None
    delivered = _reports_delivered(ctx)
    early: list[str] = []
    for entry in ctx.log:
        if entry.status not in expected or entry.status not in REPORTED_STATUSES:
            continue
        report = delivered.get(entry.status)
        if report is None or entry.at < report.at:
            early.append(entry.status)
    if not early:
        return None
    return _item(
        "status_before_report",
        "Проставлены до доклада бригады: "
        + ", ".join(f"«{sm.title(s)}»" for s in dict.fromkeys(early))
        + ". Статусы хода работ ставятся по факту получения информации от наряда.",
    )


def report_not_reflected(ctx: CardContext) -> ErrorItem | None:
    """The squad reported a milestone, the card never got the status (or got it much later)."""
    if sm.ACCEPTED not in ctx.statuses or sm.WORKS_REFUSED in ctx.statuses:
        return None
    delivered = _reports_delivered(ctx)
    if not delivered:
        return None
    missed: list[str] = []
    late: list[str] = []
    for status, report in delivered.items():
        assert report is not None
        entries = [e for e in ctx.entries(status) if e.at >= report.at]
        if not entries:
            missed.append(status)
        elif (seconds_between(report.at, entries[0].at) or 0.0) > REPORT_REACTION_SECONDS:
            late.append(status)
    if not missed and not late:
        return None
    parts = []
    if missed:
        parts.append("не проставлены " + ", ".join(f"«{sm.title(s)}»" for s in missed))
    if late:
        parts.append(
            "позже норматива на отражение доклада " + ", ".join(f"«{sm.title(s)}»" for s in late)
        )
    return _item(
        "report_not_reflected",
        "Доклады бригады не отражены в карточке: " + "; ".join(parts) + ".",
    )


# A report the card closed while it was still ringing is not the dispatcher's fault.
REPORT_END_CARD_CLOSED = "card_closed"


def report_not_taken(ctx: CardContext) -> ErrorItem | None:
    """The squad called with a report and nobody picked up (issue #103). A report still
    ringing when the card was closed is not counted."""
    reports = [c for c in ctx.attempt.service_calls if c.kind == "report"]
    missed = [
        call.report_status or ""
        for call in reports
        if not call.answered and call.end_reason != REPORT_END_CARD_CLOSED
    ]
    if not missed:
        return None
    named = [sm.title(s) for s in dict.fromkeys(missed) if s]
    about = " (" + ", ".join(f"«{t}»" for t in named) + ")" if named else ""
    return _item(
        "report_not_taken",
        f"Бригада звонила с докладом {len(missed)} раз(а){about}, диспетчер не ответил. "
        "Доклад принимается по телефону: без него ход реагирования в карточке не отражается.",
    )


CARD_DETECTORS: dict[str, Callable[[CardContext], ErrorItem | None]] = {
    "no_status": no_status,
    "late_primary": late_primary,
    "status_mismatch": status_mismatch,
    "competence_refusal": competence_refusal,
    "profile_refusal": profile_refusal,
    "empty_reject_comment": empty_reject_comment,
    "incomplete_comment": incomplete_comment,
    "progress_missing": progress_missing,
    "duplicate_accepted": duplicate_accepted,
    "wrong_accept_unfixed": wrong_accept_unfixed,
    "wrong_final_status": wrong_final_status,
    "error_missed": error_missed,
    "false_alarm": false_alarm,
    "service_not_informed": service_not_informed,
    "service_call_silent": service_call_silent,
    "status_before_report": status_before_report,
    "report_not_reflected": report_not_reflected,
    "report_not_taken": report_not_taken,
}


# --- call_intake -----------------------------------------------------------------------------


@dataclass
class CallContext:
    scenario: CallIntakeScenario
    attempt: CallIntakeAttempt
    topics: set[str]  # topics clarified during the conversation
    card_empty: bool = False  # the card has no type, address or description (issue #70)


def address_not_asked(ctx: CallContext) -> ErrorItem | None:
    if "address" in ctx.topics:
        return None
    card = ctx.attempt.card.address
    if card.street or card.descriptive:
        return None  # the address got into the card, even if the turn was not labelled
    return _item(
        "address_not_asked",
        "Разговор завершён, а адрес происшествия не уточнён и в карточке отсутствует.",
    )


def no_call_dropped_mark(ctx: CallContext) -> ErrorItem | None:
    if not ctx.scenario.caller.drops_call or ctx.attempt.call_dropped_marked:
        return None
    return _item(
        "no_call_dropped_mark",
        "Заявитель бросил трубку, а в карточке нет отметки «срыв звонка».",
    )


def region_not_clarified(ctx: CallContext) -> ErrorItem | None:
    reference = ctx.scenario.reference_card.address
    if reference.is_moscow():
        return None
    card = ctx.attempt.card.address
    expected = normalize_text(reference.region)
    actual = normalize_text(" ".join(filter(None, [card.region, card.city])))
    if actual and fuzz.partial_ratio(expected, actual) >= 80:
        return None
    return _item(
        "region_not_clarified",
        f"Происшествие в регионе «{reference.region}», а карточка оформлена как московская: "
        "регион не уточнён.",
    )


def questions_not_asked(ctx: CallContext) -> ErrorItem | None:
    """Blocking rule of issue #69: fewer than ``min_questions_share`` of the required topics
    asked by the trainee → critical error, the attempt fails whatever the total."""
    required = list(ctx.scenario.required_topics)
    share = ctx.scenario.min_questions_share
    if not required or share <= 0:
        return None
    needed = ceil(len(required) * share)
    asked = [t for t in required if t in ctx.topics]
    if len(asked) >= needed:
        return None
    return _item(
        "questions_not_asked",
        f"Незачёт: задано {len(asked)} из {len(required)} обязательных вопросов "
        f"(нужно не меньше {needed}).",
        critical=True,
    )


def card_empty(ctx: CallContext) -> ErrorItem | None:
    """Blocking rule of issue #70: a card without type, address and description."""
    if not ctx.card_empty:
        return None
    return _item(
        "card_empty",
        "Незачёт: карточка сохранена без типа происшествия, адреса и описания.",
        critical=True,
    )


CALL_DETECTORS: dict[str, Callable[[CallContext], ErrorItem | None]] = {
    "questions_not_asked": questions_not_asked,
    "card_empty": card_empty,
    "address_not_asked": address_not_asked,
    "no_call_dropped_mark": no_call_dropped_mark,
    "region_not_clarified": region_not_clarified,
}


def run_detectors[C](
    detectors: dict[str, Callable[[C], ErrorItem | None]], ctx: C
) -> list[ErrorItem]:
    return [item for detector in detectors.values() if (item := detector(ctx)) is not None]
