"""Evaluation of the «Приём вызова» mode (PRD 9.3), maximum 100.

- survey_card (25): path of signs against the reference; full path 25, right type with a wrong
  lowest level 15, right group only 5.
- flags_services (10): flags compared one by one (5); services by Jaccard against
  ``expected_services`` (5).
- address (15): street rapidfuzz ratio ≥ 90 after normalization, house exact, entrance, floor,
  code and the rest only when the reference has them.
- required_topics (15): 15 × clarified ∩ required / required.
- description (10): reference keywords present (5) and semantic closeness to the reference
  description (5).
- time (10): from answering the call to saving the card, norm of the session; same formula as
  the response time.
- typical_errors (5): 5 minus penalties of the detectors that fired, not below 0.
- grammar (10): 10 − 2 × LanguageTool errors in the description, not below 0; «not checked»
  when LanguageTool is down and the total is renormalized.
"""

from __future__ import annotations

from collections.abc import Mapping

from rapidfuzz import fuzz

from app.domain.evaluation.detectors import CALL_DETECTORS, CallContext, run_detectors
from app.domain.evaluation.result import (
    DEFAULT_PASS_THRESHOLD,
    Component,
    ErrorItem,
    EvaluationResult,
    apply_weights,
    finalize,
    scale,
)
from app.domain.evaluation.schemas import Address, CallIntakeAttempt, CallIntakeScenario
from app.domain.evaluation.text import (
    detect_topics,
    keyword_coverage,
    normalize_house,
    normalize_text,
    street_ratio,
)
from app.domain.evaluation.timing import seconds_between, time_fraction
from app.providers.embeddings import EmbeddingProvider, TfidfEmbedding
from app.providers.grammar import GrammarResult

MODE = "call_intake"
DEFAULT_WEIGHTS: dict[str, int] = {
    "survey_card": 25,
    "flags_services": 10,
    "address": 15,
    "required_topics": 15,
    "description": 10,
    "time": 10,
    "typical_errors": 5,
    "grammar": 10,
}
TITLES = {
    "survey_card": "Опросная карта",
    "flags_services": "Признаки и службы",
    "address": "Адрес",
    "required_topics": "Обязательные вопросы",
    "description": "Описание",
    "time": "Время",
    "typical_errors": "Типичные ошибки",
    "grammar": "Грамотность",
}
STREET_THRESHOLD = 90.0
# Optional address parts scored only when the reference has them (PRD 9.3).
OPTIONAL_ADDRESS_PARTS = (
    "building",
    "structure",
    "entrance",
    "floor",
    "code",
    "apartment",
    "region",
    "city",
)


def _path_levels(code: str) -> list[str]:
    """Code «1.5.6.2» → group and three sign levels; trailing zeros mean «no level»."""
    parts = code.split(".")
    return [p for p in parts if p != "0"]


def _survey_card(
    scenario: CallIntakeScenario, attempt: CallIntakeAttempt, max_points: int
) -> Component:
    reference = scenario.reference_card
    card = attempt.card
    item: dict = {"expected_type": reference.incident_type, "actual_type": card.incident_type}
    if card.incident_type and reference.incident_type:
        expected, actual = _path_levels(reference.incident_type), _path_levels(card.incident_type)
    else:
        expected = [normalize_text(s) for s in reference.signs_path]
        actual = [normalize_text(s) for s in card.signs_path]
        item.update({"expected_path": reference.signs_path, "actual_path": card.signs_path})
    matched = 0
    for e, a in zip(expected, actual, strict=False):
        if e != a:
            break
        matched += 1
    depth = len(expected)
    if depth == 0:
        fraction, note = 1.0, "эталон не задаёт тип"
    elif matched == depth:
        fraction, note = 1.0, "тип происшествия верен"
    elif matched == depth - 1 and matched >= 2:
        fraction, note = 0.6, "верный тип, неверен нижний уровень"
    elif matched >= 1:
        fraction, note = 0.2, "верна только группа"
    else:
        fraction, note = 0.0, "тип происшествия не совпал"
    item.update({"matched_levels": matched, "levels": depth, "note": note})
    return Component(
        "survey_card", TITLES["survey_card"], scale(max_points, fraction), max_points, items=[item]
    )


def _flags_services(
    scenario: CallIntakeScenario, attempt: CallIntakeAttempt, max_points: int
) -> Component:
    reference = scenario.reference_card
    card = attempt.card
    half = max_points / 2
    keys = set(reference.flags) | {k for k, v in card.flags.items() if v}
    wrong = sorted(k for k in keys if bool(reference.flags.get(k)) != bool(card.flags.get(k)))
    flags_fraction = 1.0 if not keys else 1 - len(wrong) / len(keys)
    expected = set(reference.expected_services)
    actual = set(card.services)
    union = expected | actual
    jaccard = 1.0 if not union else len(expected & actual) / len(union)
    score = round(scale(half, flags_fraction) + scale(half, jaccard), 1)
    items = [
        {"flags_wrong": wrong, "flags_fraction": round(flags_fraction, 2)},
        {
            "expected_services": sorted(expected),
            "actual_services": sorted(actual),
            "missing": sorted(expected - actual),
            "extra": sorted(actual - expected),
            "jaccard": round(jaccard, 2),
        },
    ]
    return Component("flags_services", TITLES["flags_services"], score, max_points, items=items)


def _address(reference: Address, actual: Address, max_points: int) -> Component:
    parts: list[tuple[str, float, float, str]] = []  # (part, weight, match, note)
    if reference.street:
        ratio = street_ratio(reference.street, actual.street)
        parts.append(
            ("street", 3, 1.0 if ratio >= STREET_THRESHOLD else 0.0, f"совпадение {ratio:.0f}%")
        )
    elif reference.descriptive:
        ratio = fuzz.token_set_ratio(
            normalize_text(reference.descriptive),
            normalize_text(actual.descriptive or actual.street),
        )
        parts.append(("descriptive", 3, 1.0 if ratio >= 70 else 0.0, f"совпадение {ratio:.0f}%"))
    if reference.house:
        same = normalize_house(reference.house) == normalize_house(actual.house)
        parts.append(
            ("house", 2, 1.0 if same else 0.0, "точное совпадение" if same else "не совпал")
        )
    for name in OPTIONAL_ADDRESS_PARTS:
        expected = getattr(reference, name)
        if not expected:
            continue
        got = getattr(actual, name)
        same = normalize_text(expected).replace(" ", "") == normalize_text(got).replace(" ", "")
        if not same and got and name in {"region", "city"}:
            same = fuzz.partial_ratio(normalize_text(expected), normalize_text(got)) >= 85
        parts.append(
            (name, 1, 1.0 if same else 0.0, "совпал" if same else f"ожидалось «{expected}»")
        )
    if not parts:
        return Component("address", TITLES["address"], float(max_points), max_points)
    total_weight = sum(w for _, w, _, _ in parts)
    fraction = sum(w * m for _, w, m, _ in parts) / total_weight
    items = [{"part": p, "weight": w, "match": m, "note": n} for p, w, m, n in parts]
    return Component(
        "address", TITLES["address"], scale(max_points, fraction), max_points, items=items
    )


def clarified_topics(attempt: CallIntakeAttempt) -> set[str]:
    """Topics touched in the conversation: labels from the dialog engine when present,
    otherwise keywords of ``caller_topics`` over the text."""
    topics: set[str] = set()
    for turn in attempt.dialog:
        topics.update(turn.topics or detect_topics(turn.text))
    return topics


def _required_topics(scenario: CallIntakeScenario, topics: set[str], max_points: int) -> Component:
    required = list(scenario.required_topics)
    if not required:
        return Component(
            "required_topics", TITLES["required_topics"], float(max_points), max_points
        )
    covered = [t for t in required if t in topics]
    missing = [t for t in required if t not in topics]
    fraction = len(covered) / len(required)
    items = [{"required": required, "covered": covered, "missing": missing}]
    return Component(
        "required_topics",
        TITLES["required_topics"],
        scale(max_points, fraction),
        max_points,
        items=items,
    )


def _description(
    scenario: CallIntakeScenario,
    attempt: CallIntakeAttempt,
    max_points: int,
    embeddings: EmbeddingProvider,
) -> Component:
    reference = scenario.reference_card
    text = attempt.card.description
    half = max_points / 2
    found, missing = keyword_coverage(text, reference.description_keywords)
    keywords_fraction = (
        1.0
        if not reference.description_keywords
        else len(found) / len(reference.description_keywords)
    )
    reference_text = reference.description or " ".join(reference.description_keywords)
    low, high = embeddings.thresholds
    similarity = embeddings.similarity(text, reference_text) if text and reference_text else 0.0
    similarity_fraction = 1.0 if similarity >= high else 0.5 if similarity >= low else 0.0
    score = round(scale(half, keywords_fraction) + scale(half, similarity_fraction), 1)
    items = [
        {"keywords_found": found, "keywords_missing": missing},
        {
            "similarity": similarity,
            "thresholds": list(embeddings.thresholds),
            "method": embeddings.method,
        },
    ]
    return Component("description", TITLES["description"], score, max_points, items=items)


def _time(scenario: CallIntakeScenario, attempt: CallIntakeAttempt, max_points: int) -> Component:
    seconds = seconds_between(attempt.answered_at, attempt.submitted_at)
    fraction = time_fraction(seconds, scenario.norm_seconds)
    items = [
        {
            "seconds": None if seconds is None else round(seconds, 1),
            "norm_seconds": scenario.norm_seconds,
            "note": "карточка не сохранена" if seconds is None else None,
        }
    ]
    return Component("time", TITLES["time"], scale(max_points, fraction), max_points, items=items)


def _typical_errors(errors: list[ErrorItem], max_points: int) -> Component:
    penalty = sum(e.penalty for e in errors)
    score = max(0.0, float(max_points - penalty))
    items = [{"code": e.code, "penalty": e.penalty} for e in errors]
    return Component("typical_errors", TITLES["typical_errors"], score, max_points, items=items)


def _grammar(grammar: GrammarResult | None, max_points: int) -> Component:
    if grammar is None or not grammar.available:
        return Component("grammar", TITLES["grammar"], 0.0, max_points, status="not_checked")
    score = max(0.0, float(max_points - 2 * grammar.error_count))
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


def description_text(attempt: CallIntakeAttempt) -> str:
    """Text the grammar provider should check for this attempt."""
    return attempt.card.description.strip()


def evaluate_call_intake(
    scenario: CallIntakeScenario,
    attempt: CallIntakeAttempt,
    *,
    weights: Mapping[str, int] | None = None,
    grammar: GrammarResult | None = None,
    embeddings: EmbeddingProvider | None = None,
    pass_threshold: int = DEFAULT_PASS_THRESHOLD,
) -> EvaluationResult:
    maxima = apply_weights(DEFAULT_WEIGHTS, weights)
    embeddings = embeddings or TfidfEmbedding()
    topics = clarified_topics(attempt)
    ctx = CallContext(scenario=scenario, attempt=attempt, topics=topics)
    errors = run_detectors(CALL_DETECTORS, ctx)

    components = [
        _survey_card(scenario, attempt, maxima["survey_card"]),
        _flags_services(scenario, attempt, maxima["flags_services"]),
        _address(scenario.reference_card.address, attempt.card.address, maxima["address"]),
        _required_topics(scenario, topics, maxima["required_topics"]),
        _description(scenario, attempt, maxima["description"], embeddings),
        _time(scenario, attempt, maxima["time"]),
        _typical_errors(errors, maxima["typical_errors"]),
        _grammar(grammar, maxima["grammar"]),
    ]
    for component in components:
        if component.max == 0:
            component.status = "disabled"
    methods = {
        "grammar": grammar.method if grammar is not None and grammar.available else "not_checked",
        "similarity": embeddings.method,
    }
    return finalize(MODE, components, errors, methods, pass_threshold)
