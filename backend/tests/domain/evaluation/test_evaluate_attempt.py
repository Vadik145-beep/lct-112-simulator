"""The async wrapper: grammar provider over the student's text, fallback when it is down."""

from __future__ import annotations

import httpx

from app.domain.evaluation import evaluate_attempt
from app.providers.embeddings import TfidfEmbedding
from app.providers.grammar import LanguageToolGrammar, NoGrammar
from tests.domain.evaluation.helpers import (
    call_scenario,
    card_scenario,
    load,
    perfect_call_attempt,
    perfect_card_attempt,
)

LT_ONE_TYPO = {
    "matches": [
        {
            "offset": 0,
            "length": 9,
            "message": "Найдена орфографическая ошибка",
            "replacements": [{"value": "Направлен"}],
            "rule": {"id": "MORFOLOGIK_RULE_RU_RU", "category": {"id": "TYPOS"}},
        }
    ]
}


def _languagetool(seen: list[str]) -> LanguageToolGrammar:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        return httpx.Response(200, json=LT_ONE_TYPO)

    return LanguageToolGrammar("http://lt:8010", transport=httpx.MockTransport(handler))


async def test_card_comments_go_to_languagetool() -> None:
    seen: list[str] = []
    body = load("card_2-1_zadymlenie_musoroprovoda")
    attempt = perfect_card_attempt(card_scenario()).model_dump(mode="json")
    result = await evaluate_attempt(
        body, attempt, grammar_provider=_languagetool(seen), embeddings=TfidfEmbedding()
    )
    assert len(seen) == 1
    assert "language=ru-RU" in seen[0]
    assert result.components["grammar"].score == 4  # one typo reported
    assert result.methods["grammar"] == "languagetool"
    assert result.total == 99


async def test_call_description_goes_to_languagetool() -> None:
    seen: list[str] = []
    body = load("call_2-1_zadymlenie_musoroprovoda")
    attempt = perfect_call_attempt(call_scenario()).model_dump(mode="json")
    result = await evaluate_attempt(
        body, attempt, grammar_provider=_languagetool(seen), embeddings=TfidfEmbedding()
    )
    assert len(seen) == 1
    assert result.components["grammar"].score == 8  # 10 − 2 × 1
    assert result.total == 98


async def test_languagetool_down_means_not_checked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = LanguageToolGrammar("http://lt:8010", transport=httpx.MockTransport(handler))
    body = load("card_2-1_zadymlenie_musoroprovoda")
    attempt = perfect_card_attempt(card_scenario()).model_dump(mode="json")
    result = await evaluate_attempt(
        body, attempt, grammar_provider=provider, embeddings=TfidfEmbedding()
    )
    assert result.components["grammar"].status == "not_checked"
    assert result.methods["grammar"] == "not_checked"
    assert result.total == 100  # renormalized over the other 95 points


async def test_no_grammar_provider_configured() -> None:
    body = load("call_31-3_svist_gazovoy_truby")
    attempt = perfect_call_attempt(call_scenario("call_31-3_svist_gazovoy_truby"))
    result = await evaluate_attempt(
        body,
        attempt.model_dump(mode="json"),
        grammar_provider=NoGrammar(),
        embeddings=TfidfEmbedding(),
    )
    assert result.components["grammar"].status == "not_checked"
    assert result.total == 100
