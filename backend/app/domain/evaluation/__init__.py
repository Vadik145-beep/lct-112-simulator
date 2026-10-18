"""Evaluation engines of both training modes (PRD 9.2, 9.3).

Pure functions: ``evaluate_card_response`` and ``evaluate_call_intake`` take a validated
scenario body, the attempt data and the already obtained provider results, and return an
``EvaluationResult``. ``evaluate_attempt`` is the thin async wrapper the attempt endpoints use:
it asks the grammar provider for the student's text and picks the engine by scenario kind.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace

from app.domain.evaluation.call_intake import description_text, evaluate_call_intake
from app.domain.evaluation.card_response import comments_of, evaluate_card_response
from app.domain.evaluation.result import (
    DEFAULT_PASS_THRESHOLD,
    Component,
    ErrorItem,
    EvaluationResult,
    apply_weights,
)
from app.domain.evaluation.schemas import (
    CallIntakeAttempt,
    CallIntakeScenario,
    CardResponseAttempt,
    CardResponseScenario,
    parse_scenario,
)
from app.domain.evaluation.status_machine import TransitionError, allowed_next, validate_transition
from app.providers.embeddings import EmbeddingProvider, get_embedding_provider
from app.providers.grammar import GrammarProvider, GrammarResult, get_grammar_provider

__all__ = [
    "DEFAULT_PASS_THRESHOLD",
    "Component",
    "ErrorItem",
    "EvaluationResult",
    "TransitionError",
    "allowed_next",
    "apply_weights",
    "check_comments",
    "evaluate_attempt",
    "evaluate_call_intake",
    "evaluate_card_response",
    "evaluate_sync",
    "parse_scenario",
    "validate_transition",
]


def evaluate_sync(
    body: Mapping,
    attempt: Mapping,
    *,
    weights: Mapping[str, int] | None = None,
    grammar: GrammarResult | None = None,
    embeddings: EmbeddingProvider | None = None,
    pass_threshold: int = DEFAULT_PASS_THRESHOLD,
) -> EvaluationResult:
    """Evaluate raw dictionaries (scenario body and attempt data) without any provider call."""
    scenario = parse_scenario(dict(body))
    if isinstance(scenario, CardResponseScenario):
        return evaluate_card_response(
            scenario,
            CardResponseAttempt.model_validate(dict(attempt)),
            weights=weights,
            grammar=grammar,
            embeddings=embeddings,
            pass_threshold=pass_threshold,
        )
    assert isinstance(scenario, CallIntakeScenario)
    return evaluate_call_intake(
        scenario,
        CallIntakeAttempt.model_validate(dict(attempt)),
        weights=weights,
        grammar=grammar,
        embeddings=embeddings,
        pass_threshold=pass_threshold,
    )


async def evaluate_attempt(
    body: Mapping,
    attempt: Mapping,
    *,
    weights: Mapping[str, int] | None = None,
    pass_threshold: int = DEFAULT_PASS_THRESHOLD,
    grammar_provider: GrammarProvider | None = None,
    embeddings: EmbeddingProvider | None = None,
) -> EvaluationResult:
    """Evaluate with the configured providers: grammar over the student's text (comments or
    the description), similarity through the embedding provider. When LanguageTool is down
    the grammar component is «not checked» and the total is renormalized."""
    scenario = parse_scenario(dict(body))
    grammar_provider = grammar_provider or get_grammar_provider()
    embeddings = embeddings or get_embedding_provider()
    if isinstance(scenario, CardResponseScenario):
        parsed = CardResponseAttempt.model_validate(dict(attempt))
        grammar = await check_comments(grammar_provider, comments_of(parsed))
    else:
        parsed = CallIntakeAttempt.model_validate(dict(attempt))
        text = description_text(parsed)
        grammar = (
            await grammar_provider.check(text)
            if text
            else GrammarResult([], grammar_provider.method)
        )
    return evaluate_sync(
        body,
        attempt,
        weights=weights,
        grammar=grammar,
        embeddings=embeddings,
        pass_threshold=pass_threshold,
    )


async def check_comments(provider: GrammarProvider, comments: list[str]) -> GrammarResult:
    """Each comment checked on its own, the offsets pointing into ``comment_texts``: comments
    of different statuses run together read as one sentence (stand 18.10.2026: «выехали
    прибыли, Приступили…» came back as «однородные члены»). A comment is a note in a status
    field, so its first letter is checked as a capital: alone «на месте» would otherwise be an
    error of its own, which it never was in the joined text."""
    results = await asyncio.gather(*(provider.check(c[:1].upper() + c[1:]) for c in comments))
    if not all(r.available for r in results):
        return GrammarResult([], "unavailable")
    matches = []
    start = 0
    for comment, result in zip(comments, results, strict=True):
        matches += [replace(m, offset=m.offset + start) for m in result.matches]
        start += len(comment) + 1  # the line break of ``comment_texts``
    return GrammarResult(matches, provider.method)
