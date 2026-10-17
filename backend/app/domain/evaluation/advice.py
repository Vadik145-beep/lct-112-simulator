"""Advice for further training (issue #265, recommendations 5 and 9–10 of the customer:
«результат – рекомендации для дальнейшего обучения»).

Template text from what the evaluation found, no model: after a card — 3–5 concrete points
from its mistakes (the operator's mistakes the dispatcher missed first, then the detected
errors from the heaviest); after a lesson — what repeated across its cards, how the operator's
mistakes were found and how the score went. Pure: works on the stored results.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

MAX_POINTS = 5
REPEATED = 2  # an error in this many cards of the lesson is a habit, not a slip

# What to do about each error of a card (detector codes of ``detectors.CARD_DETECTORS``).
ERROR_ADVICE: dict[str, str] = {
    "no_status": "Отмечайте каждую новую карточку «Принята» или «Не принята»: без первичного "
    "статуса карточка уходит в «Не оповещено».",
    "late_primary": "Ставьте первичный статус сразу, в пределах норматива: сначала «Принята» "
    "или «Не принята», подробности — потом.",
    "status_mismatch": "Сверяйте решение с описанием: принимайте карточки своей службы, "
    "остальные — «Не принята» с причиной.",
    "competence_refusal": "Не отказывайте по компетенции, если происшествие относится к вашей "
    "службе: проверьте тип происшествия и службы карточки.",
    "profile_refusal": "Отказ не по профилю службы: сверяйтесь с классификатором, какие службы "
    "оповещаются о таком происшествии.",
    "empty_reject_comment": "«Не принята» ставьте с комментарием: причина и кому передана "
    "информация.",
    "incomplete_comment": "В комментарии к «Началу реагирования» указывайте номер наряда и что "
    "направлено на место.",
    "progress_missing": "Ведите карточку до конца: «Прибытие», «Проведение работ», «Работы "
    "завершены» — по докладам бригады.",
    "duplicate_accepted": "Повторную карточку о том же происшествии ставьте «Не принята» с "
    "причиной «дубль».",
    "wrong_accept_unfixed": "Ошибочно принятую карточку исправляйте «Отказом от выполнения "
    "работ» с комментарием.",
    "wrong_final_status": "Закрывайте карточку тем итоговым статусом, о котором доложила бригада.",
    "false_alarm": "Отмечайте флажком только поле, которое расходится с описанием со слов "
    "заявителя.",
    "service_not_informed": "После «Принята» звоните руководителю службы: адрес, тип "
    "происшествия, пострадавшие и номер наряда.",
    "service_call_silent": "В разговоре со службой называйте адрес, тип происшествия и "
    "пострадавших: звонок без этих сведений не засчитывается.",
    "status_before_report": "Статусы хода работ ставьте после доклада бригады, а не заранее.",
    "report_not_reflected": "Отражайте доклад бригады в карточке: ставьте статус, о котором "
    "доложили.",
    "report_not_taken": "Отвечайте на звонки бригады: доклад о ходе работ принимается по телефону.",
}
# Covered by the specific advice on each missed operator's mistake.
SKIP_ERRORS = frozenset({"error_missed"})
MISSED = ("missed", "wrong_correction")
PASSED_ADVICE = (
    "Карточка отработана верно. Держите темп: первичный статус — сразу, ход работ — по "
    "докладам бригады."
)


def card_advice(result: Mapping) -> list[str]:
    """3–5 points for one scored card (``attempts.result`` / the evaluation row)."""
    points = [_missed_advice(item) for item in _data_check(result) if item.get("verdict") in MISSED]
    for error in sorted(
        result.get("errors") or [],
        key=lambda e: (not e.get("critical"), -float(e.get("penalty") or 0)),
    ):
        code = str(error.get("code") or "")
        if code in SKIP_ERRORS:
            continue
        points.append(ERROR_ADVICE.get(code) or _from_error(error))
    points = list(dict.fromkeys(p for p in points if p))[:MAX_POINTS]
    if not points and result.get("passed"):
        points.append(PASSED_ADVICE)
    return points


def session_advice(results: Sequence[Mapping]) -> list[str]:
    """3–5 points for a trainee's lesson: what repeated, the operator's mistakes, the trend."""
    scored = [r for r in results if r]
    if not scored:
        return []
    points: list[str] = []
    habits = Counter(
        code
        for r in scored
        for code in {str(e.get("code") or "") for e in r.get("errors") or []}
        if code and code not in SKIP_ERRORS
    )
    for code, count in habits.most_common():
        if count < REPEATED or code not in ERROR_ADVICE:
            continue
        points.append(f"Повторялось в {count} из {len(scored)} карточек. {ERROR_ADVICE[code]}")
    planted = [
        item for r in scored for item in _data_check(r) if item.get("verdict") != "false_alarm"
    ]
    if planted:
        found = sum(1 for item in planted if item.get("verdict") not in MISSED)
        missed = Counter(
            str(item.get("title") or item.get("field"))
            for item in planted
            if item.get("verdict") in MISSED
        )
        line = f"Ошибки оператора 112 найдены в {found} из {len(planted)} случаев."
        if missed:
            worst, _ = missed.most_common(1)[0]
            line += (
                f" Чаще всего пропущено поле «{worst}»: сверяйте его с описанием со слов заявителя."
            )
        points.append(line)
    totals = [float(r.get("total") or 0) for r in scored]
    passed = sum(1 for r in scored if r.get("passed"))
    if len(totals) >= 2 and totals[-1] != totals[0]:
        trend = "выросли" if totals[-1] > totals[0] else "снизились"
        points.append(
            f"Баллы {trend}: первая карточка — {round(totals[0])}, последняя — "
            f"{round(totals[-1])}; зачтено {passed} из {len(scored)}."
        )
    else:
        points.append(f"Зачтено {passed} из {len(scored)} карточек.")
    return points[:MAX_POINTS]


def _data_check(result: Mapping) -> list[Mapping]:
    component = (result.get("components") or {}).get("data_check") or {}
    return list(component.get("items") or [])


def _missed_advice(item: Mapping) -> str:
    field = str(item.get("field") or "")
    title = str(item.get("title") or field)
    wrong = item.get("wrong_label") or item.get("wrong_value")
    right = item.get("correct_label") or item.get("correct_value")
    if item.get("verdict") == "wrong_correction":
        return (
            f"Поле «{title}» отмечено верно, но исправлено неточно: впишите значение так, как "
            f"его назвал заявитель («{right}»)."
        )
    if field == "service":
        return (
            f"Карточка пришла не в вашу службу ({wrong}): ставьте «Не принята» с причиной "
            "«не в компетенции службы»."
        )
    if field.startswith("address."):
        return (
            f"Сверяйте адрес с описанием: заявитель назвал «{right}», а в карточке «{wrong}». "
            "Отметьте поле флажком и передавайте бригаде верный адрес."
        )
    return (
        f"Сверяйте поле «{title}» с описанием со слов заявителя: верно «{right}», в карточке "
        f"«{wrong}». Отметьте ошибку флажком у поля."
    )


def _from_error(error: Mapping) -> str:
    title = str(error.get("title") or "").rstrip(".")
    ref = error.get("memo_ref")
    return f"{title}: см. {ref}." if title and ref else (f"{title}." if title else "")
