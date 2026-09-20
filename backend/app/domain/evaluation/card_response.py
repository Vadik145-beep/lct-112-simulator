"""Evaluation of the «Реагирование на карточку» mode (PRD 9.2), maximum 100.

- decision (30): matches the reference; reject with the right reason 30, right decision but
  a wrong reason 15; a decision corrected after a wrong primary status earns half.
- time (20): from «Добавлена» to the primary status; within the norm 20, twice the norm 0,
  linear in between.
- status_chain (20): share of the reference statuses present in the right order; extras and
  gaps are listed.
- comments (15): required comments present (else 0 for the step), squad number where needed,
  closeness of the text to the example.
- typical_errors (10): 10 minus penalties of the detectors that fired, not below 0.
- grammar (5): 5 minus LanguageTool errors in the comments, not below 0; «not checked» when
  LanguageTool is down and the total is renormalized.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.domain.evaluation import data_check, service_call
from app.domain.evaluation import status_machine as sm
from app.domain.evaluation.detectors import CARD_DETECTORS, CardContext, run_detectors
from app.domain.evaluation.result import (
    DEFAULT_PASS_THRESHOLD,
    Component,
    ErrorItem,
    EvaluationResult,
    apply_weights,
    finalize,
    scale,
)
from app.domain.evaluation.schemas import CardResponseAttempt, CardResponseScenario, StatusEntry
from app.domain.evaluation.text import normalize_text
from app.domain.evaluation.timing import seconds_between, time_fraction
from app.domain.reference_data import REJECT_REASONS
from app.providers.embeddings import EmbeddingProvider, TfidfEmbedding
from app.providers.grammar import GrammarResult

MODE = "card_response"
# The six classic components sum to 100 on their own; «Проверка данных» (issue #35) exists only
# for cards with planted operator mistakes. When it applies, all seven weights are normalized
# to 100 together (see ``apply_weights``), so the six classic components shrink proportionally
# (30 → 25, 20 → 16, 15 → 12, 10 → 8, 5 → 4, the check gets 16, and the rounding remainder goes
# to the heaviest one). A card without planted mistakes scores exactly as before.
DEFAULT_WEIGHTS: dict[str, int] = {
    "decision": 30,
    "time": 20,
    "status_chain": 20,
    "comments": 15,
    "typical_errors": 10,
    "grammar": 5,
    data_check.KEY: 20,
    service_call.KEY: 15,
}
TITLES = {
    "decision": "Решение",
    "time": "Время принятия",
    "status_chain": "Цепочка статусов",
    "comments": "Комментарии и наряд",
    "typical_errors": "Типичные ошибки",
    "grammar": "Грамотность",
    data_check.KEY: data_check.TITLE,
    service_call.KEY: service_call.TITLE,
}


def applicable_components(scenario: CardResponseScenario | Mapping | None) -> list[str]:
    """Components that apply to the scenario: «Проверка данных» only with planted errors,
    «Звонки в службы» only with reference service calls (issues #35, #36)."""
    if isinstance(scenario, CardResponseScenario):
        has_errors = bool(scenario.injected_errors)
        has_calls = bool(scenario.reference.service_calls)
    else:
        body = scenario or {}
        has_errors = bool(body.get("injected_errors"))
        has_calls = bool((body.get("reference") or {}).get("service_calls"))
    skip = set()
    if not has_errors:
        skip.add(data_check.KEY)
    if not has_calls:
        skip.add(service_call.KEY)
    return [k for k in DEFAULT_WEIGHTS if k not in skip]


NO_REJECT_SERVICES = frozenset({"103"})
REJECT_REASON_TITLES = {r["code"]: r["title"] for r in REJECT_REASONS}
# How a reason is phrased in a free-text comment when the drop-down was not used.
REASON_PHRASES: list[tuple[str, tuple[str, ...]]] = [
    ("duplicate", ("дубл", "повторн")),
    ("other_card", ("по другой карточке", "по кп", "другой карточк")),
    ("no_contract", ("нет договора",)),
    ("not_our_territory", ("территор",)),
    ("not_in_competence", ("компетенц", "не относится")),
    ("transferred", ("передан", "передал")),
    ("not_our_object", ("не обслужива", "объект")),
]


def infer_reject_reason(entry: StatusEntry) -> str | None:
    if entry.reject_reason:
        return entry.reject_reason
    comment = normalize_text(entry.comment)
    for code, phrases in REASON_PHRASES:
        if any(p in comment for p in phrases):
            return code
    return None


def _lcs(reference: list[str], actual: list[str]) -> list[str]:
    """Longest common subsequence: reference statuses that appear in order."""
    n, m = len(reference), len(actual)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if reference[i] == actual[j]:
                table[i][j] = table[i + 1][j + 1] + 1
            else:
                table[i][j] = max(table[i + 1][j], table[i][j + 1])
    result, i, j = [], 0, 0
    while i < n and j < m:
        if reference[i] == actual[j]:
            result.append(reference[i])
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return result


def _decision(ctx: CardContext, max_points: int) -> Component:
    reference = ctx.scenario.reference
    expected = reference.decision
    expected_reason = reference.reject_reason
    items: list[dict] = []
    if ctx.primary is None:
        items.append({"expected": expected, "actual": None, "note": "решение не принято"})
        return Component("decision", TITLES["decision"], 0.0, max_points, items=items)

    actual = "accept" if ctx.final_primary.status == sm.ACCEPTED else "reject"
    corrected = ctx.primary is not ctx.final_primary
    reason_entry = ctx.final_primary
    # «Принята» later closed by «Отказ от выполнения работ» is a corrected refusal (memo p. 32).
    refusals = ctx.entries(sm.WORKS_REFUSED)
    if actual == "accept" and refusals and (refusals[-1].comment or "").strip():
        actual, corrected, reason_entry = "reject", True, refusals[-1]

    fraction = 0.0
    note = "решение не совпало с эталоном"
    if actual == expected:
        if expected == "reject":
            actual_reason = infer_reject_reason(reason_entry)
            if expected_reason is None or actual_reason == expected_reason:
                fraction, note = 1.0, "решение и причина верны"
            else:
                fraction, note = 0.5, "решение верно, причина отказа не совпала"
            items.append(
                {
                    "expected_reason": expected_reason,
                    "actual_reason": actual_reason,
                    "expected_reason_title": REJECT_REASON_TITLES.get(expected_reason or ""),
                }
            )
        else:
            fraction, note = 1.0, "решение верно"
        if corrected:
            fraction /= 2
            note += "; решение исправлено после ошибочного первичного статуса"
    items.insert(0, {"expected": expected, "actual": actual, "note": note})
    return Component(
        "decision", TITLES["decision"], scale(max_points, fraction), max_points, items=items
    )


def _time(ctx: CardContext, max_points: int) -> Component:
    seconds = None
    if ctx.primary is not None:
        seconds = seconds_between(ctx.attempt.issued_at, ctx.primary.at)
    fraction = time_fraction(seconds, ctx.scenario.norm_seconds)
    items = [
        {
            "seconds": None if seconds is None else round(seconds, 1),
            "norm_seconds": ctx.scenario.norm_seconds,
            "note": "первичный статус не проставлен" if seconds is None else None,
        }
    ]
    return Component("time", TITLES["time"], scale(max_points, fraction), max_points, items=items)


def _status_chain(
    ctx: CardContext, max_points: int, invalid: list[tuple[StatusEntry, sm.TransitionError]]
) -> Component:
    reference = [s.status for s in ctx.scenario.reference.status_chain]
    actual = ctx.statuses
    matched = _lcs(reference, actual) if reference else []
    missing = [s for s in reference if s not in matched]
    extra = [s for s in actual if s not in reference]
    fraction = len(matched) / len(reference) if reference else 1.0
    items = [
        {
            "expected": reference,
            "actual": actual,
            "matched": matched,
            "missing": missing,
            "extra": extra,
        }
    ]
    for entry, error in invalid:
        items.append({"invalid": entry.status, "message": error.message})
    return Component(
        "status_chain", TITLES["status_chain"], scale(max_points, fraction), max_points, items=items
    )


def _comments(ctx: CardContext, max_points: int, embeddings: EmbeddingProvider) -> Component:
    low, high = embeddings.thresholds
    steps = []
    for step in ctx.scenario.reference.status_chain:
        spec = sm.STATUSES.get(step.status, {})
        needs_comment = bool(step.comment_example) or bool(spec.get("requires_comment"))
        needs_order = step.order_number or bool(spec.get("requires_order_number"))
        if not needs_comment and not needs_order:
            continue
        entries = ctx.entries(step.status)
        entry = entries[0] if entries else None
        parts: list[float] = []
        item: dict = {"status": step.status, "title": sm.title(step.status)}
        if entry is None:
            item["note"] = "статус не проставлен"
            steps.append((0.0, item))
            continue
        if needs_comment:
            comment = (entry.comment or "").strip()
            if not comment:
                parts.append(0.0)
                item["comment"] = "нет"
            elif step.comment_example:
                similarity = embeddings.similarity(comment, step.comment_example)
                item["comment"] = comment
                item["similarity"] = similarity
                parts.append(1.0 if similarity >= high else 0.8 if similarity >= low else 0.6)
            else:
                item["comment"] = comment
                parts.append(1.0)
        if needs_order:
            has_order = bool((entry.order_number or "").strip())
            item["order_number"] = entry.order_number if has_order else "нет"
            parts.append(1.0 if has_order else 0.0)
        steps.append((sum(parts) / len(parts), item))
    if not steps:
        return Component("comments", TITLES["comments"], float(max_points), max_points)
    fraction = sum(score for score, _ in steps) / len(steps)
    items = [{**item, "fraction": round(score, 2)} for score, item in steps]
    return Component(
        "comments", TITLES["comments"], scale(max_points, fraction), max_points, items=items
    )


def _typical_errors(errors: list[ErrorItem], max_points: int) -> Component:
    penalty = sum(e.penalty for e in errors)
    score = max(0.0, float(max_points - penalty))
    items = [{"code": e.code, "penalty": e.penalty} for e in errors]
    return Component("typical_errors", TITLES["typical_errors"], score, max_points, items=items)


def _grammar(grammar: GrammarResult | None, max_points: int) -> Component:
    if grammar is None or not grammar.available:
        return Component("grammar", TITLES["grammar"], 0.0, max_points, status="not_checked")
    score = max(0.0, float(max_points - grammar.error_count))
    items = [
        {
            "offset": m.offset,
            "length": m.length,
            "message": m.message,
            "replacements": m.replacements,
        }
        for m in grammar.matches
    ]
    return Component("grammar", TITLES["grammar"], score, max_points, items=items)


def comment_texts(attempt: CardResponseAttempt) -> str:
    """Text the grammar provider should check for this attempt."""
    return "\n".join(
        (e.comment or "").strip() for e in attempt.status_log if (e.comment or "").strip()
    )


def evaluate_card_response(
    scenario: CardResponseScenario,
    attempt: CardResponseAttempt,
    *,
    weights: Mapping[str, int] | None = None,
    grammar: GrammarResult | None = None,
    embeddings: EmbeddingProvider | None = None,
    pass_threshold: int = DEFAULT_PASS_THRESHOLD,
    no_reject: bool | None = None,
) -> EvaluationResult:
    maxima = apply_weights(DEFAULT_WEIGHTS, weights, applicable_components(scenario))
    embeddings = embeddings or TfidfEmbedding()
    if no_reject is None:
        no_reject = scenario.service in NO_REJECT_SERVICES
    check = sm.check_log(attempt.status_log, no_reject=no_reject)
    log = [e for e in check.valid if e.status not in sm.SYSTEM_STATUSES]
    ctx = CardContext(scenario=scenario, attempt=attempt, log=log, no_reject=no_reject)

    errors = run_detectors(CARD_DETECTORS, ctx)
    critical = set(scenario.reference.critical_errors)
    for error in errors:
        error.critical = error.code in critical

    components = [
        _decision(ctx, maxima["decision"]),
        _time(ctx, maxima["time"]),
        _status_chain(ctx, maxima["status_chain"], check.invalid),
        _comments(ctx, maxima["comments"], embeddings),
        _typical_errors(errors, maxima["typical_errors"]),
        _grammar(grammar, maxima["grammar"]),
    ]
    if data_check.KEY in maxima:
        components.insert(
            4, data_check.data_check_component(scenario, attempt, maxima[data_check.KEY])
        )
    if service_call.KEY in maxima:
        components.insert(
            -2, service_call.service_call_component(scenario, attempt, maxima[service_call.KEY])
        )
    for component in components:
        if component.max == 0:
            component.status = "disabled"
    methods = {
        "grammar": grammar.method if grammar is not None and grammar.available else "not_checked",
        "similarity": embeddings.method,
    }
    return finalize(MODE, components, errors, methods, pass_threshold)
