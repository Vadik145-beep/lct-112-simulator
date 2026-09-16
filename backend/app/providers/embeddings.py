"""EmbeddingProvider: semantic similarity of short Russian texts (comments vs reference).

Main implementation: ``intfloat/multilingual-e5-small`` as an ONNX model run by onnxruntime
with the HF ``tokenizers`` library (no torch in the image), loaded from a local folder
(EMBEDDING_MODEL_DIR, filled by scripts/fetch_models.sh).
Fallback without a model: TF-IDF over character n-grams and words, pure Python, no network.
Both expose ``method`` and the similarity thresholds the evaluation uses for «partially» and
«fully» matching text (TF-IDF 0.30/0.70 as in PRD 9.3). For e5 the PRD guessed 0.70/0.90, but
the cosine of e5 vectors lives in a narrow band: measured on ten pairs of dispatcher texts,
unrelated ones score 0.78–0.84 and paraphrases 0.88–0.92 (docs/DECISIONS.md, wave 5), so the
thresholds are 0.85/0.88.
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
E5_THRESHOLDS = (0.85, 0.88)
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
    """e5-small: mean pooling over the last hidden state, then L2 normalization."""

    method = "e5-small"
    thresholds = E5_THRESHOLDS
    MAX_TOKENS = 512

    def __init__(self, model_dir: Path) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._tokenizer.enable_truncation(self.MAX_TOKENS)
        self._tokenizer.enable_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        self._session = ort.InferenceSession(
            str(model_dir / "onnx" / "model.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self._input_names = [i.name for i in self._session.get_inputs()]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        import numpy as np

        # e5 expects a task prefix; «query:» suits symmetric similarity of short texts.
        encoded = self._tokenizer.encode_batch([f"query: {t}" for t in texts])
        ids = np.array([e.ids for e in encoded], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, feed)[0]  # (batch, tokens, dim)
        weights = mask[:, :, None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-9)
        norms = np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9)
        return (pooled / norms).tolist()

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
