"""The async wrapper: grammar provider over the student's text, fallback when it is down."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx

from app.domain.evaluation import check_comments, evaluate_attempt
from app.domain.evaluation.card_response import comment_texts
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


def _languagetool(seen: list[str], typo_in: str | None = None) -> LanguageToolGrammar:
    """LanguageTool that finds the typo at the start of every text, or only of the text that
    starts with ``typo_in``."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        text = parse_qs(request.content.decode())["text"][0]
        found = typo_in is None or text.startswith(typo_in)
        return httpx.Response(200, json=LT_ONE_TYPO if found else {"matches": []})

    return LanguageToolGrammar("http://lt:8010", transport=httpx.MockTransport(handler))


def _texts(seen: list[str]) -> list[str]:
    return [parse_qs(row)["text"][0] for row in seen]


async def test_card_comments_go_to_languagetool_one_by_one() -> None:
    """Stand 18.10.2026: «выехали» and «прибыли» of two statuses checked as one text came back
    as one sentence with «однородные члены». Each comment is checked on its own."""
    seen: list[str] = []
    body = load("card_2-1_zadymlenie_musoroprovoda")
    attempt = perfect_card_attempt(card_scenario())
    result = await evaluate_attempt(
        body,
        attempt.model_dump(mode="json"),
        grammar_provider=_languagetool(seen, typo_in="Мусоропровод"),
        embeddings=TfidfEmbedding(),
    )
    comments = [e.comment for e in attempt.status_log if e.comment]
    assert sorted(_texts(seen)) == sorted(comments)
    assert all("language=ru-RU" in row for row in seen)
    assert result.components["grammar"].score == 4  # one typo reported
    assert result.methods["grammar"] == "languagetool"
    assert result.total == 99
    # The offset points into the text the review shows, at the comment with the typo.
    item = result.components["grammar"].items[0]
    shown = comment_texts(attempt)
    assert shown[item["offset"] :].startswith("Мусоропровод")


async def test_a_comment_is_checked_from_a_capital_letter() -> None:
    """Alone, «на месте» would be «предложение со строчной буквы» — never an error while the
    comments were checked together, and a status comment is a note, not a sentence."""
    seen: list[str] = []
    await check_comments(_languagetool(seen), ["на месте", "проводятся работы"])
    assert sorted(_texts(seen)) == ["На месте", "Проводятся работы"]


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
