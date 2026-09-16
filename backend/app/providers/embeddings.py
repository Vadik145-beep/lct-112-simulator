"""EmbeddingProvider: semantic similarity of short Russian texts (comments vs reference).

Main implementation: ``intfloat/multilingual-e5-small`` through sentence-transformers,
loaded from a local folder (EMBEDDING_MODEL_DIR, filled by scripts/fetch_models.sh in wave 5).
Fallback without a model: TF-IDF over character n-grams and words, pure Python, no network.
Both expose ``method`` and the similarity thresholds the evaluation uses for «partially» and
«fully» matching text (PRD 9.3: e5 0.70/0.90, TF-IDF 0.30/0.70).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

from app.config import get_settings
from app.logging import get_logger

log = get_logger(__name__)

E5_MODEL_NAME = "intfloat/multilingual-e5-small"
E5_THRESHOLDS = (0.70, 0.90)
TFIDF_THRESHOLDS = (0.30, 0.70)
NGRAM_SIZES = (3, 4)
_WORD = re.compile(r"[а-яёa-z0-9]+")


class EmbeddingProvider(Protocol):
    method: str
    thresholds: tuple[float, float]

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def similarity(self, a: str, b: str) -> float: ...


def cosine(u: Sequence[float], v: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(u, v, strict=True))
    nu = math.sqrt(sum(x * x for x in u))
    nv = math.sqrt(sum(y * y for y in v))
    if nu == 0 or nv == 0:
        return 0.0
    return dot / (nu * nv)


def _features(text: str) -> Counter[str]:
    """Words plus character n-grams: robust to Russian inflection and small typos."""
    normalized = re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()
    counts: Counter[str] = Counter()
    for word in _WORD.findall(normalized):
        counts[f"w:{word}"] += 1
        padded = f" {word} "
        for n in NGRAM_SIZES:
            for i in range(max(len(padded) - n + 1, 0)):
                counts[f"c:{padded[i : i + n]}"] += 1
    return counts


class TfidfEmbedding:
    """TF-IDF with IDF learned from the texts seen so far (``fit`` with a corpus to start).

    Vectors are sparse dictionaries internally; ``embed`` returns dense lists over the
    vocabulary known at call time, so compare vectors from the same call.
    """

    method = "tfidf"
    thresholds = TFIDF_THRESHOLDS

    def __init__(self, corpus: Iterable[str] = ()) -> None:
        self._doc_freq: Counter[str] = Counter()
        self._docs = 0
        self.fit(corpus)

    def fit(self, corpus: Iterable[str]) -> None:
        for text in corpus:
            self._docs += 1
            self._doc_freq.update(set(_features(text)))

    def _vector(self, text: str) -> dict[str, float]:
        counts = _features(text)
        total = sum(counts.values()) or 1
        vector = {}
        for feature, count in counts.items():
            idf = math.log((1 + self._docs) / (1 + self._doc_freq.get(feature, 0))) + 1
            vector[feature] = (count / total) * idf
        return vector

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = [self._vector(t) for t in texts]
        vocabulary = sorted({f for v in vectors for f in v})
        return [[v.get(f, 0.0) for f in vocabulary] for v in vectors]

    def similarity(self, a: str, b: str) -> float:
        va, vb = self._vector(a), self._vector(b)
        dot = sum(w * vb.get(f, 0.0) for f, w in va.items())
        na = math.sqrt(sum(w * w for w in va.values()))
        nb = math.sqrt(sum(w * w for w in vb.values()))
        if na == 0 or nb == 0:
            return 0.0
        return round(dot / (na * nb), 4)


class E5Embedding:
    method = "e5-small"
    thresholds = E5_THRESHOLDS

    def __init__(self, model_dir: Path) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(str(model_dir), device="cpu")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        # e5 expects a task prefix; «query:» suits symmetric similarity of short texts.
        vectors = self._model.encode([f"query: {t}" for t in texts], normalize_embeddings=True)
        return [list(map(float, v)) for v in vectors]

    def similarity(self, a: str, b: str) -> float:
        u, v = self.embed([a, b])
        return round(cosine(u, v), 4)


_provider: EmbeddingProvider | None = None


def build_embedding_provider(model_dir: str | None) -> EmbeddingProvider:
    """e5 when the model folder exists and the library is installed, otherwise TF-IDF."""
    if model_dir and Path(model_dir).exists():
        try:
            provider = E5Embedding(Path(model_dir))
            log.info("embedding provider", method=provider.method, model_dir=model_dir)
            return provider
        except Exception as exc:  # missing library, broken files: fall back, do not crash
            log.warning("e5 unavailable, using tf-idf", error=str(exc))
    return TfidfEmbedding()


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        _provider = build_embedding_provider(get_settings().embedding_model_dir)
    return _provider
