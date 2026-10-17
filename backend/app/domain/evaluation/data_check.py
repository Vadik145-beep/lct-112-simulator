"""«Проверка данных»: did the dispatcher find the mistakes of the 112 operator (issue #35).

The scenario lists the planted mistakes (``injected_errors``: field, wrong value shown, correct
value); the attempt lists the fields the dispatcher flagged with the value they entered. Each
planted mistake is worth an equal share of the component: a flag with a matching correction
earns the share, a flag with a wrong correction half of it, a missed mistake nothing. A flag on
a field that was right (a false alarm) costs half a share. A card sent to the wrong service
(field ``service``, issue #265) is found by refusing it as «не в компетенции службы»; a refusal
with another reason counts as a wrong correction. Values are compared after the same
normalization the call-intake engine uses for a saved card (streets by ``rapidfuzz``, houses
without «д.» and spaces, everything else lower-cased without punctuation).
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from app.domain.evaluation import status_machine as sm
from app.domain.evaluation.result import Component, scale
from app.domain.evaluation.schemas import (
    CardResponseAttempt,
    CardResponseScenario,
    FlaggedField,
    InjectedError,
)
from app.domain.evaluation.text import normalize_house, normalize_text, streets_match
from app.domain.reference_data import REJECT_REASONS

KEY = "data_check"
TITLE = "Проверка данных"
FALSE_ALARM_FRACTION = 0.5  # of one share
WRONG_CORRECTION_FRACTION = 0.5
LABEL_THRESHOLD = 80.0  # rapidfuzz token_set_ratio for a title typed instead of a code
# The whole card went to a service it does not concern: the answer is a refusal, not a flag.
WRONG_SERVICE_FIELD = "service"
WRONG_SERVICE_ANSWER = sm.REJECTED
WRONG_SERVICE_REASON = "not_in_competence"
_REASON_TITLES = {r["code"]: r["title"] for r in REJECT_REASONS}

FIELD_TITLES: dict[str, str] = {
    "address.region": "Регион",
    "address.city": "Город",
    "address.street": "Улица",
    "address.house": "Дом",
    "address.building": "Корпус",
    "address.structure": "Строение",
    "address.entrance": "Подъезд",
    "address.floor": "Этаж",
    "address.apartment": "Квартира",
    "address.descriptive": "Описательный адрес",
    "incident_type": "Тип происшествия",
    "flags.injured": "Пострадавшие",
    "flags.no_access": "Нет доступа",
    "flags.threat": "Угроза людям",
    "services": "Оповещённые службы",
    "caller.name": "Заявитель",
    "caller.phone": "Телефон заявителя",
    "description": "Описание",
    WRONG_SERVICE_FIELD: "Служба-получатель",
}
BOOL_TRUE = {"true", "1", "да", "есть", "yes"}
BOOL_FALSE = {"false", "0", "нет", "no"}


def field_title(field: str) -> str:
    return FIELD_TITLES.get(field, field)


def values_match(field: str, expected: str, actual: str | None, label: str | None = None) -> bool:
    """Whether the dispatcher's correction names the right value for the field."""
    if actual is None:
        return False
    if field == "address.street":
        return streets_match(expected, actual)
    if field.startswith("address."):
        return normalize_house(expected) == normalize_house(actual)
    if field.startswith("flags."):
        return _as_bool(expected) is not None and _as_bool(expected) == _as_bool(actual)
    if field == "services":
        return _service_action(expected) == _service_action(actual) or (
            label is not None
            and _service_action(expected)[0] == _service_action(actual)[0]
            and fuzz.token_set_ratio(normalize_text(label), _service_action(actual)[1])
            >= LABEL_THRESHOLD
        )
    normalized_expected = normalize_text(expected)
    normalized_actual = normalize_text(actual)
    if normalized_expected == normalized_actual:
        return True
    if field == "incident_type" and label:
        return fuzz.token_set_ratio(normalize_text(label), normalized_actual) >= LABEL_THRESHOLD
    if field in {"description", "caller.name"}:
        return fuzz.token_set_ratio(normalized_expected, normalized_actual) >= LABEL_THRESHOLD
    if field == "caller.phone":
        return _digits(expected) == _digits(actual)
    return False


def _as_bool(value: str | None) -> bool | None:
    text = normalize_text(value)
    if text in BOOL_TRUE:
        return True
    if text in BOOL_FALSE:
        return False
    return None


def _service_action(value: str) -> tuple[str, str]:
    """``+code`` / ``-code`` → (sign, normalized code or title)."""
    text = (value or "").strip()
    sign = text[0] if text[:1] in "+-" else ""
    return sign, normalize_text(text[1:] if sign else text)


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


@dataclass
class DataCheckOutcome:
    found: list[InjectedError]
    wrong_correction: list[tuple[InjectedError, FlaggedField]]
    missed: list[InjectedError]
    false_alarms: list[FlaggedField]


def check(scenario: CardResponseScenario, attempt: CardResponseAttempt) -> DataCheckOutcome:
    flagged = {f.field: f for f in attempt.flagged_fields}
    found: list[InjectedError] = []
    wrong: list[tuple[InjectedError, FlaggedField]] = []
    missed: list[InjectedError] = []
    planted_fields = set()
    for error in scenario.injected_errors:
        planted_fields.add(error.field)
        flag = _refusal(attempt) if error.field == WRONG_SERVICE_FIELD else flagged.get(error.field)
        if flag is None:
            missed.append(error)
        elif error.field == WRONG_SERVICE_FIELD:
            if flag.corrected_value == WRONG_SERVICE_REASON:
                found.append(error)
            else:
                wrong.append((error, flag))
        elif values_match(
            error.field, error.correct_value, flag.corrected_value, error.correct_label
        ):
            found.append(error)
        else:
            wrong.append((error, flag))
    false_alarms = [f for f in attempt.flagged_fields if f.field not in planted_fields]
    return DataCheckOutcome(found, wrong, missed, false_alarms)


def _refusal(attempt: CardResponseAttempt) -> FlaggedField | None:
    """The last refusal of the card as a «flag» of the service, its reason as the value."""
    refusals = [e for e in attempt.status_log if e.status in sm.REJECT_STATUSES]
    if not refusals:
        return None
    last = refusals[-1]
    return FlaggedField(
        field=WRONG_SERVICE_FIELD,
        corrected_value=sm.infer_reject_reason(last) or "",
        at=last.at,
    )


def _shown_correction(flag: FlaggedField | None) -> str | None:
    """The dispatcher's value as the review shows it (a refusal by its reason's title)."""
    if flag is None:
        return None
    if flag.field == WRONG_SERVICE_FIELD:
        reason = _REASON_TITLES.get(flag.corrected_value, flag.corrected_value)
        return f"Не принята: {reason.lower()}" if reason else "Не принята без причины"
    return flag.corrected_value


def data_check_component(
    scenario: CardResponseScenario, attempt: CardResponseAttempt, max_points: int
) -> Component:
    outcome = check(scenario, attempt)
    planted = len(scenario.injected_errors)
    share = 1.0 / planted if planted else 0.0
    fraction = len(outcome.found) * share + len(outcome.wrong_correction) * share * (
        WRONG_CORRECTION_FRACTION
    )
    fraction -= len(outcome.false_alarms) * share * FALSE_ALARM_FRACTION
    items: list[dict] = []
    corrections = {id(error): flag for error, flag in outcome.wrong_correction}
    flagged = {f.field: f for f in attempt.flagged_fields}
    for error in scenario.injected_errors:
        flag = corrections.get(id(error)) or flagged.get(error.field)
        if any(error is f for f in outcome.found):
            verdict, part = "found", 1.0
        elif id(error) in corrections:
            verdict, part = "wrong_correction", WRONG_CORRECTION_FRACTION
        else:
            verdict, part = "missed", 0.0
        items.append(
            {
                "field": error.field,
                "title": field_title(error.field),
                "wrong_value": error.wrong_value,
                "correct_value": error.correct_value,
                "wrong_label": error.wrong_label,
                "correct_label": error.correct_label,
                "hint_level": error.hint_level,
                "corrected_value": _shown_correction(flag),
                "verdict": verdict,
                "fraction": round(part, 2),
            }
        )
    for flag in outcome.false_alarms:
        items.append(
            {
                "field": flag.field,
                "title": field_title(flag.field),
                "corrected_value": flag.corrected_value,
                "verdict": "false_alarm",
                "fraction": -FALSE_ALARM_FRACTION,
            }
        )
    return Component(KEY, TITLE, scale(max_points, fraction), max_points, items=items)
