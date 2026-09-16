"""Evaluation without a model must be fast: under 300 ms per attempt (PRD phase 1 check)."""

from __future__ import annotations

import time

from app.domain.evaluation import evaluate_sync
from app.providers.embeddings import TfidfEmbedding
from tests.domain.evaluation.helpers import (
    call_scenario,
    card_scenario,
    grammar_ok,
    load,
    perfect_call_attempt,
    perfect_card_attempt,
)

LIMIT_MS = 300
ROUNDS = 20


def _measure(body: dict, attempt: dict) -> float:
    """Worst single run over ROUNDS, in milliseconds (a fresh TF-IDF provider each time)."""
    worst = 0.0
    for _ in range(ROUNDS):
        started = time.perf_counter()
        evaluate_sync(body, attempt, grammar=grammar_ok(), embeddings=TfidfEmbedding())
        worst = max(worst, (time.perf_counter() - started) * 1000)
    return worst


def test_card_response_is_faster_than_300ms() -> None:
    body = load("card_2-1_zadymlenie_musoroprovoda")
    attempt = perfect_card_attempt(card_scenario()).model_dump(mode="json")
    worst = _measure(body, attempt)
    print(f"card_response: worst of {ROUNDS} runs {worst:.1f} ms")
    assert worst < LIMIT_MS


def test_call_intake_is_faster_than_300ms() -> None:
    body = load("call_2-1_zadymlenie_musoroprovoda")
    attempt = perfect_call_attempt(call_scenario()).model_dump(mode="json")
    worst = _measure(body, attempt)
    print(f"call_intake: worst of {ROUNDS} runs {worst:.1f} ms")
    assert worst < LIMIT_MS
