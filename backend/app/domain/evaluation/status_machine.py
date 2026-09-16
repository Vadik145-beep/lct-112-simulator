"""Response status machine of a service on a card (memo pages 21-23, PRD 9.1).

Statuses go strictly in order. «Не принята» allows only «Принята» afterwards, final statuses
close the card, service 103 (``no_reject``) has neither «Не принята» nor «Отказ от выполнения
работ». Used by the attempt endpoints to refuse a bad transition with a clear message and by the
evaluation to skip entries that could not have happened.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.evaluation.schemas import StatusEntry
from app.domain.reference_data import RESPONSE_STATUSES

STATUSES: dict[str, dict] = {s["code"]: s for s in RESPONSE_STATUSES}
TITLES: dict[str, str] = {code: s["title"] for code, s in STATUSES.items()}

ADDED = "added"
RECEIVED = "received"
ACCEPTED = "accepted"
REJECTED = "rejected"
RESPONSE_STARTED = "response_started"
ARRIVED = "arrived"
WORKS_STARTED = "works_started"
WORKS_DONE = "works_done"
WORKS_REFUSED = "works_refused"

SYSTEM_STATUSES = frozenset(code for code, s in STATUSES.items() if s["is_system"])
PRIMARY_STATUSES = frozenset(code for code, s in STATUSES.items() if s["is_primary"])
FINAL_STATUSES = frozenset(code for code, s in STATUSES.items() if s["is_final"])
PROGRESS_STATUSES = (RESPONSE_STARTED, ARRIVED, WORKS_STARTED)
REJECT_STATUSES = frozenset({REJECTED, WORKS_REFUSED})
# Service 103 closes without a squad instead of refusing (memo page 23).
NO_REJECT_CLOSING_COMMENT = "завершение работ без бригады"


class TransitionError(ValueError):
    """A transition the interface must refuse; ``message`` is shown to the student."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def title(code: str) -> str:
    return TITLES.get(code, code)


def allowed_next(current: str | None, *, no_reject: bool = False) -> list[str]:
    """Statuses the dispatcher may set after ``current`` (``None`` = card not yet sent)."""
    if current is None:
        return [ADDED]
    if current not in STATUSES:
        raise TransitionError("unknown_status", f"Неизвестный статус «{current}»")
    result = list(STATUSES[current]["allowed_next"])
    if no_reject:
        result = [code for code in result if code not in REJECT_STATUSES]
    return result


def validate_transition(
    current: str | None,
    new: str,
    *,
    comment: str | None = None,
    order_number: str | None = None,
    no_reject: bool = False,
) -> None:
    """Raises ``TransitionError`` when ``new`` cannot follow ``current`` or lacks required
    fields. Returns nothing on success."""
    if new not in STATUSES:
        raise TransitionError("unknown_status", f"Неизвестный статус «{new}»")
    if current is not None and current in FINAL_STATUSES:
        raise TransitionError(
            "card_closed",
            f"Карточка закрыта статусом «{title(current)}», изменения невозможны",
        )
    if no_reject and new in REJECT_STATUSES:
        raise TransitionError(
            "no_reject_service",
            f"Служба 103 не проставляет «{title(new)}»: используйте «{title(WORKS_DONE)}» "
            f"с комментарием «{NO_REJECT_CLOSING_COMMENT}»",
        )
    allowed = allowed_next(current, no_reject=no_reject)
    if new not in allowed:
        current_title = title(current) if current else "нет статуса"
        options = ", ".join(f"«{title(code)}»" for code in allowed) or "нет"
        raise TransitionError(
            "not_allowed",
            f"После «{current_title}» нельзя проставить «{title(new)}». Доступно: {options}",
        )
    spec = STATUSES[new]
    if spec["requires_comment"] and not (comment or "").strip():
        raise TransitionError(
            "comment_required", f"К статусу «{title(new)}» обязателен комментарий"
        )
    if spec["requires_order_number"] and not (order_number or "").strip():
        raise TransitionError(
            "order_number_required", f"К статусу «{title(new)}» обязателен номер наряда"
        )


@dataclass
class LogCheck:
    valid: list[StatusEntry]
    invalid: list[tuple[StatusEntry, TransitionError]]
    last_status: str | None


def check_log(
    entries: Iterable[StatusEntry], *, no_reject: bool = False, start: str | None = ADDED
) -> LogCheck:
    """Replays a status log from ``start`` («Добавлена» once the card is sent). Invalid entries
    are collected with their error and do not change the state; the interface never saves
    them, so in practice the list is empty and this is a safety net for imported data."""
    current = start
    valid: list[StatusEntry] = []
    invalid: list[tuple[StatusEntry, TransitionError]] = []
    for entry in entries:
        if entry.status == RECEIVED and current == ADDED:
            current = RECEIVED
            valid.append(entry)
            continue
        try:
            validate_transition(
                current,
                entry.status,
                comment=entry.comment,
                order_number=entry.order_number,
                no_reject=no_reject,
            )
        except TransitionError as exc:
            # Missing comment / squad number is a scoring matter, not an impossible event:
            # keep the entry so the detectors can penalize it.
            if exc.code in {"comment_required", "order_number_required"}:
                valid.append(entry)
                current = entry.status
            else:
                invalid.append((entry, exc))
            continue
        valid.append(entry)
        current = entry.status
    return LogCheck(valid=valid, invalid=invalid, last_status=current)
