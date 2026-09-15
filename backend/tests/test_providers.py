"""GrammarProvider and EmbeddingProvider with their fallbacks."""

import httpx
import pytest
from httpx import AsyncClient

from app.providers import grammar as grammar_module
from app.providers.embeddings import TfidfEmbedding, build_embedding_provider
from app.providers.grammar import LanguageToolGrammar, NoGrammar
from tests.conftest import bearer, login

LT_RESPONSE = {
    "matches": [
        {
            "offset": 0,
            "length": 6,
            "message": "Найдена орфографическая ошибка",
            "replacements": [{"value": "Прибыл"}, {"value": "Прибыла"}],
            "rule": {"id": "MORFOLOGIK_RULE_RU_RU", "category": {"id": "TYPOS"}},
        }
    ]
}


def _mock_transport(status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/check"
        return httpx.Response(status, json=LT_RESPONSE if status == 200 else {})

    return httpx.MockTransport(handler)


async def test_languagetool_matches_are_parsed() -> None:
    provider = LanguageToolGrammar("http://lt:8010", transport=_mock_transport())
    result = await provider.check("Прибыл наряд")
    assert result.method == "languagetool"
    assert result.available
    assert result.error_count == 1
    match = result.matches[0]
    assert (match.offset, match.length) == (0, 6)
    assert match.replacements == ["Прибыл", "Прибыла"]
    assert match.category == "TYPOS"


async def test_languagetool_down_falls_back_and_waits_before_retry(monkeypatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection refused")

    provider = LanguageToolGrammar("http://lt:8010", transport=httpx.MockTransport(handler))
    first = await provider.check("текст")
    assert first.method == "unavailable"
    assert not first.available
    assert first.matches == []
    # Within the retry window the server is not bothered again.
    second = await provider.check("текст")
    assert second.method == "unavailable"
    assert calls == 1
    # After the window a restarted LanguageTool is picked up.
    monkeypatch.setattr(grammar_module.time, "monotonic", lambda: 10_000.0)
    provider._transport = _mock_transport()
    third = await provider.check("Прибыл наряд")
    assert third.method == "languagetool"


async def test_languagetool_http_error_is_unavailable() -> None:
    provider = LanguageToolGrammar("http://lt:8010", transport=_mock_transport(500))
    assert (await provider.check("текст")).method == "unavailable"


async def test_no_grammar_provider() -> None:
    result = await NoGrammar().check("любой текст")
    assert result.method == "unavailable" and result.matches == []


async def test_grammar_endpoint_roles(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(
        grammar_module,
        "_provider",
        LanguageToolGrammar("http://lt:8010", transport=_mock_transport()),
    )
    teacher = await login(client, "teacher1")
    r = await client.post(
        "/api/grammar/check", json={"text": "Прибыл наряд"}, headers=bearer(teacher)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "languagetool"
    assert body["error_count"] == 1
    assert body["matches"][0]["replacements"][0] == "Прибыл"

    student = await login(client, "student1")
    r = await client.post("/api/grammar/check", json={"text": "текст"}, headers=bearer(student))
    assert r.status_code == 403


def test_tfidf_similarity_orders_texts_sensibly() -> None:
    provider = TfidfEmbedding(
        [
            "Направлен дежурный слесарь, наряд 14-217",
            "Мусоропровод вскрыт, тлеющий мусор удалён",
            "Задымление устранено, пострадавших нет",
        ]
    )
    same = provider.similarity(
        "Задымление устранено, пострадавших нет", "Задымление устранено, пострадавших нет"
    )
    close = provider.similarity(
        "Задымление устранено, пострадавших нет", "Задымление устранили, пострадавших нету"
    )
    far = provider.similarity("Задымление устранено, пострадавших нет", "Наряд 14-217 выехал")
    assert same == pytest.approx(1.0)
    assert close > provider.thresholds[1] > far
    assert provider.similarity("", "что-то") == 0.0
    assert provider.method == "tfidf"


def test_tfidf_embed_returns_aligned_vectors() -> None:
    vectors = TfidfEmbedding().embed(["пожар в подъезде", "пожар на кухне"])
    assert len(vectors) == 2 and len(vectors[0]) == len(vectors[1]) > 0


def test_embedding_falls_back_to_tfidf_without_model(tmp_path) -> None:
    assert build_embedding_provider(None).method == "tfidf"
    assert build_embedding_provider(str(tmp_path / "missing")).method == "tfidf"
    # A folder without model files: the library (if installed) fails and TF-IDF takes over.
    assert build_embedding_provider(str(tmp_path)).method == "tfidf"
