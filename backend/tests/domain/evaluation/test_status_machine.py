"""Response status machine: allowed transitions, required fields, service 103."""

import pytest

from app.domain.evaluation import status_machine as sm
from app.domain.evaluation.schemas import StatusEntry
from tests.domain.evaluation.helpers import at


def test_allowed_next_follows_the_live_workstation() -> None:
    """One step at a time, as the АРМ-112 of 17.09.2026 offers them (docs/DECISIONS.md)."""
    assert sm.allowed_next(None) == ["added"]
    assert sm.allowed_next("added") == ["received", "accepted", "rejected"]
    assert sm.allowed_next("received") == ["accepted", "rejected"]
    assert sm.allowed_next("rejected") == ["accepted"]  # only «Принята» after «Не принята»
    assert sm.allowed_next("accepted") == ["response_started", "works_done", "works_refused"]
    assert sm.allowed_next("response_started") == ["arrived", "works_done", "works_refused"]
    assert sm.allowed_next("arrived") == ["works_started", "works_done", "works_refused"]
    assert sm.allowed_next("works_started") == ["works_done", "works_refused"]
    assert sm.allowed_next("works_done") == []
    assert sm.allowed_next("works_refused") == []


def test_service_103_has_no_reject_statuses() -> None:
    assert sm.allowed_next("received", no_reject=True) == ["accepted"]
    assert "works_refused" not in sm.allowed_next("accepted", no_reject=True)
    with pytest.raises(sm.TransitionError) as exc:
        sm.validate_transition("received", "rejected", comment="нет бригады", no_reject=True)
    assert exc.value.code == "no_reject_service"
    assert "завершение работ без бригады" in exc.value.message


@pytest.mark.parametrize(
    ("current", "new", "code", "fragment"),
    [
        (
            "received",
            "arrived",
            "not_allowed",
            "После «Получена службой» нельзя проставить «Прибытие»",
        ),
        ("rejected", "response_started", "not_allowed", "Доступно: «Принята»"),
        ("accepted", "arrived", "not_allowed", "После «Принята» нельзя проставить «Прибытие»"),
        ("works_done", "accepted", "card_closed", "Карточка закрыта статусом «Работы завершены»"),
        ("accepted", "unknown", "unknown_status", "Неизвестный статус"),
        ("received", "rejected", "comment_required", "обязателен комментарий"),
        ("accepted", "works_done", "comment_required", "«Работы завершены» обязателен комментарий"),
        ("accepted", "response_started", "order_number_required", "обязателен номер наряда"),
    ],
)
def test_invalid_transition_gives_a_clear_error(
    current: str, new: str, code: str, fragment: str
) -> None:
    with pytest.raises(sm.TransitionError) as exc:
        sm.validate_transition(current, new)
    assert exc.value.code == code
    assert fragment in exc.value.message


def test_valid_transitions_pass() -> None:
    sm.validate_transition("added", "received")
    sm.validate_transition("received", "accepted")
    sm.validate_transition("received", "rejected", comment="дубль")
    sm.validate_transition("rejected", "accepted")
    sm.validate_transition("accepted", "response_started", order_number="14-217")
    sm.validate_transition("response_started", "works_done", comment="сделано")
    sm.validate_transition("accepted", "works_refused", comment="передано в 101")


def test_check_log_keeps_valid_entries_and_reports_invalid_ones() -> None:
    entries = [
        StatusEntry(status="received", at=at(5)),
        StatusEntry(status="accepted", at=at(10)),
        StatusEntry(status="accepted", at=at(12)),  # repeated: not allowed after «Принята»
        StatusEntry(status="arrived", at=at(60)),  # skips «Начало реагирования»
        StatusEntry(status="works_done", at=at(90)),  # missing comment: kept for scoring
        StatusEntry(status="arrived", at=at(100)),  # card is closed
    ]
    check = sm.check_log(entries)
    assert [e.status for e in check.valid] == ["received", "accepted", "works_done"]
    assert [(e.status, err.code) for e, err in check.invalid] == [
        ("accepted", "not_allowed"),
        ("arrived", "not_allowed"),
        ("arrived", "card_closed"),
    ]
    assert check.last_status == "works_done"
