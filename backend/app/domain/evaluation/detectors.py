"""Typical error detectors: one named pure function per code of ``typical_errors``.

Each detector receives the prepared context of an attempt and returns an ``ErrorItem`` when the
error is present, otherwise ``None``. Penalties and titles come from the memo data
(``app.domain.reference_data.TYPICAL_ERRORS``); explanations are written for the student.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

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


def service_not_informed(ctx: CardContext) -> ErrorItem | None:
    """The card is accepted, but a required service officer was never reached (issue #36)."""
    required = ctx.scenario.reference.service_calls
    if not required or ctx.final_primary is None or ctx.final_primary.status != sm.ACCEPTED:
        return None
    reached = {c.service for c in ctx.attempt.service_calls if c.answered}
    missing = [r.service for r in required if r.service not in reached]
    if not missing:
        return None
    return _item(
        "service_not_informed",
        "Карточка принята, но в службу по телефону не позвонили: "
        + ", ".join(f"«{s}»" for s in missing)
        + ". Руководителю службы передаются адрес, тип происшествия, пострадавшие и номер наряда.",
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
}


# --- call_intake -----------------------------------------------------------------------------


@dataclass
class CallContext:
    scenario: CallIntakeScenario
    attempt: CallIntakeAttempt
    topics: set[str]  # topics clarified during the conversation


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


CALL_DETECTORS: dict[str, Callable[[CallContext], ErrorItem | None]] = {
    "address_not_asked": address_not_asked,
    "no_call_dropped_mark": no_call_dropped_mark,
    "region_not_clarified": region_not_clarified,
}


def run_detectors[C](
    detectors: dict[str, Callable[[C], ErrorItem | None]], ctx: C
) -> list[ErrorItem]:
    return [item for detector in detectors.values() if (item := detector(ctx)) is not None]
