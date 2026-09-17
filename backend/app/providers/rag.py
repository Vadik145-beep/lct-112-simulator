"""Retrieval for scenario generation (PRD 9.6 item 2): the closest paragraphs of the memo and
the closest ticket situations to a phrase, by the embedding provider (e5 when the model is
downloaded, TF-IDF otherwise).

The corpus is small (243 memo paragraphs, 96 tickets, uploaded methodical documents), so the
index is an in-memory list of vectors built on first use per process and rebuilt when a
document is added; a vector table in PostgreSQL would be more machinery than the data needs.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.domain.memo_search import load_paragraphs
from app.logging import get_logger
from app.providers.embeddings import EmbeddingProvider, cosine, get_embedding_provider

log = get_logger(__name__)

MEMO_FILE = "seed/memo.txt"
MEMO_HITS = 5
TICKET_HITS = 3
# Uploaded methodical documents (POST /reference/docs) are stored as extracted text here.
DOCS_SUBDIR = "reference_docs"
MIN_CHUNK_LENGTH = 40
MAX_CHUNK_LENGTH = 700


@dataclass(frozen=True)
class Chunk:
    source: str  # «Памятка АРМ-112, стр. 12», «Билет 2-1», file name
    text: str
    score: float = 0.0


def split_document(text: str) -> list[str]:
    """Paragraph chunks of an uploaded document, long paragraphs cut by sentences."""
    chunks: list[str] = []
    for paragraph in text.replace("\r", "").split("\n\n"):
        paragraph = " ".join(paragraph.split())
        if len(paragraph) < MIN_CHUNK_LENGTH:
            continue
        while len(paragraph) > MAX_CHUNK_LENGTH:
            cut = paragraph.rfind(". ", 0, MAX_CHUNK_LENGTH)
            cut = cut + 1 if cut > MIN_CHUNK_LENGTH else MAX_CHUNK_LENGTH
            chunks.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if paragraph:
            chunks.append(paragraph)
    return chunks


class ReferenceIndex:
    """Vectors of the memo, the tickets and the uploaded documents."""

    def __init__(self, embeddings: EmbeddingProvider) -> None:
        self._embeddings = embeddings
        # TF-IDF vectors are dense over the vocabulary of one ``embed`` call, so they are only
        # comparable within a call: the query is embedded together with the corpus each time.
        self._stable_vectors = getattr(embeddings, "method", "") != "tfidf"
        self._lock = threading.Lock()
        self._memo: list[tuple[Chunk, list[float]]] | None = None
        self._tickets: list[tuple[Chunk, list[float]]] = []
        self._ticket_keys: list[str] = []

    # --- building ----------------------------------------------------------------------

    def _vectors(self, chunks: Sequence[Chunk]) -> list[tuple[Chunk, list[float]]]:
        if not chunks:
            return []
        if not self._stable_vectors:
            return [(c, []) for c in chunks]  # embedded per query, see _top
        vectors = self._embeddings.embed([c.text for c in chunks])
        return list(zip(chunks, vectors, strict=True))

    def _memo_chunks(self) -> list[Chunk]:
        settings = get_settings()
        chunks = [
            Chunk(f"Памятка АРМ-112, стр. {page}", text)
            for page, text in load_paragraphs(f"{settings.data_dir}/{MEMO_FILE}")
            if len(text) >= MIN_CHUNK_LENGTH
        ]
        docs_dir = Path(settings.storage_dir) / DOCS_SUBDIR
        if docs_dir.exists():
            for file in sorted(docs_dir.glob("*.txt")):
                for piece in split_document(file.read_text(encoding="utf-8")):
                    chunks.append(Chunk(file.stem, piece))
        return chunks

    def ensure_memo(self) -> None:
        with self._lock:
            if self._memo is None:
                chunks = self._memo_chunks()
                self._memo = self._vectors(chunks)
                log.info(
                    "reference index built",
                    chunks=len(chunks),
                    method=getattr(self._embeddings, "method", "?"),
                )

    def invalidate(self) -> None:
        """A document was added or removed: rebuild on next use."""
        with self._lock:
            self._memo = None

    def set_tickets(self, tickets: Sequence[tuple[str, str]]) -> None:
        """(ref, text) pairs; re-embedded only when the list changed."""
        keys = [ref for ref, _ in tickets]
        with self._lock:
            if keys == self._ticket_keys:
                return
            self._tickets = self._vectors([Chunk(f"Билет {ref}", text) for ref, text in tickets])
            self._ticket_keys = keys

    # --- searching ---------------------------------------------------------------------

    def _top(self, query: str, rows: list[tuple[Chunk, list[float]]], limit: int) -> list[Chunk]:
        if not rows or not query.strip():
            return []
        if self._stable_vectors:
            vector = self._embeddings.embed([query])[0]
            pairs = [(cosine(vector, v), chunk) for chunk, v in rows]
        else:
            vectors = self._embeddings.embed([query, *(chunk.text for chunk, _ in rows)])
            pairs = [
                (cosine(vectors[0], v), chunk)
                for (chunk, _), v in zip(rows, vectors[1:], strict=True)
            ]
        scored = sorted(pairs, key=lambda x: -x[0])
        return [Chunk(c.source, c.text, round(score, 4)) for score, c in scored[:limit]]

    def search_memo(self, query: str, limit: int = MEMO_HITS) -> list[Chunk]:
        self.ensure_memo()
        return self._top(query, self._memo or [], limit)

    def similar_tickets(self, query: str, limit: int = TICKET_HITS) -> list[Chunk]:
        return self._top(query, self._tickets, limit)


_index: ReferenceIndex | None = None


def get_reference_index() -> ReferenceIndex:
    global _index
    if _index is None:
        _index = ReferenceIndex(get_embedding_provider())
    return _index
