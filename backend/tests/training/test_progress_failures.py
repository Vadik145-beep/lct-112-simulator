"""«Мой прогресс»: why a card with enough points is still «не зачтено»."""

from app.models import Attempt
from app.training.progress import _critical_failures


def card(total: float, passed: bool, *errors: dict) -> Attempt:
    return Attempt(result={"total": total, "passed": passed, "errors": list(errors)})


CRITICAL = {"code": "no_status", "title": "Не проставлен первичный статус", "critical": True}
MINOR = {"code": "late_primary", "title": "Поздно", "critical": False}


def test_critical_error_explains_a_failed_card_above_the_threshold() -> None:
    attempts = [card(82, False, CRITICAL, MINOR), card(85, False, CRITICAL), card(90, True)]
    assert _critical_failures(attempts, 70) == ["Не проставлен первичный статус"]


def test_low_score_or_passed_cards_need_no_reason() -> None:
    assert _critical_failures([card(40, False, CRITICAL), card(90, True, MINOR)], 70) == []
    assert _critical_failures([Attempt(result=None)], 70) == []
