"""Advice after a card and after a lesson (issue #265): concrete points from what the
evaluation found, the missed operator's mistakes first, at most five; the lesson's habits,
the operator's mistakes found and the trend of the score."""

from __future__ import annotations

from app.domain.evaluation.advice import ERROR_ADVICE, MAX_POINTS, card_advice, session_advice


def _result(*, errors=(), items=(), passed=False, total=50.0) -> dict:
    return {
        "total": total,
        "passed": passed,
        "errors": list(errors),
        "components": {"data_check": {"items": list(items)}} if items else {},
    }


HOUSE_MISSED = {
    "field": "address.house",
    "title": "Дом",
    "wrong_value": "15",
    "correct_value": "13",
    "verdict": "missed",
}
WRONG_SERVICE_MISSED = {
    "field": "service",
    "title": "Служба-получатель",
    "wrong_value": "moslift",
    "wrong_label": "Мослифт",
    "correct_label": "Не принята: не в компетенции службы",
    "verdict": "missed",
}


def test_missed_operator_mistake_comes_first_and_names_the_values() -> None:
    advice = card_advice(
        _result(
            errors=[
                {"code": "late_primary", "title": "Поздно", "penalty": 4, "critical": True},
                {"code": "error_missed", "title": "Пропущена ошибка", "penalty": 5},
            ],
            items=[HOUSE_MISSED],
        )
    )
    assert advice[0].startswith(
        "Сверяйте адрес с описанием: заявитель назвал «13», а в карточке «15»"
    )
    assert advice[1] == ERROR_ADVICE["late_primary"]
    assert len(advice) == 2  # «error_missed» is the address point already


def test_card_sent_to_the_wrong_service_is_explained() -> None:
    advice = card_advice(_result(items=[WRONG_SERVICE_MISSED]))
    assert advice == [
        "Карточка пришла не в вашу службу (Мослифт): ставьте «Не принята» с причиной "
        "«не в компетенции службы»."
    ]


def test_critical_errors_first_at_most_five_points() -> None:
    errors = [{"code": code, "title": code, "penalty": 1} for code in list(ERROR_ADVICE)[:8]]
    errors.append({"code": "empty_reject_comment", "title": "x", "penalty": 0, "critical": True})
    advice = card_advice(_result(errors=errors))
    assert len(advice) == MAX_POINTS
    assert advice[0] == ERROR_ADVICE["empty_reject_comment"]


def test_unknown_error_falls_back_to_its_title_and_memo() -> None:
    advice = card_advice(
        _result(
            errors=[{"code": "new_one", "title": "Что-то не так", "memo_ref": "Памятка, стр. 3"}]
        )
    )
    assert advice == ["Что-то не так: см. Памятка, стр. 3."]


def test_clean_card_gets_one_encouraging_point() -> None:
    assert len(card_advice(_result(passed=True, total=96))) == 1
    assert card_advice(_result(passed=False)) == []


def test_lesson_advice_names_habits_found_mistakes_and_trend() -> None:
    late = {"code": "late_primary", "title": "Поздно", "penalty": 4}
    found = {**HOUSE_MISSED, "verdict": "found"}
    results = [
        _result(errors=[late], items=[HOUSE_MISSED], total=40),
        _result(errors=[late], items=[found], total=62),
        _result(items=[found], passed=True, total=88),
    ]
    advice = session_advice(results)
    assert advice[0] == f"Повторялось в 2 из 3 карточек. {ERROR_ADVICE['late_primary']}"
    assert advice[1].startswith("Ошибки оператора 112 найдены в 2 из 3 случаев.")
    assert "«Дом»" in advice[1]
    assert advice[2] == "Баллы выросли: первая карточка — 40, последняя — 88; зачтено 1 из 3."
    assert session_advice([]) == []
