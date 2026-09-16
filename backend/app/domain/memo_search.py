"""Full-text lookup in the organizers' memo (data/seed/memo.txt) for the trainee's
reference page. Plain substring search over paragraphs, case- and «ё»-insensitive: the memo
is 40 pages, an index would be more code than the search."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PAGE_MARKER = re.compile(r"^===== Страница (\d+) =====$")
SEARCH_LIMIT = 20
MIN_QUERY_LENGTH = 2


@dataclass(frozen=True)
class MemoHit:
    page: int
    text: str


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


@lru_cache(maxsize=4)
def load_paragraphs(path: str) -> tuple[tuple[int, str], ...]:
    """(page, paragraph) pairs; empty when the memo file is missing."""
    file = Path(path)
    if not file.exists():
        return ()
    page = 0
    result: list[tuple[int, str]] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        marker = PAGE_MARKER.match(line.strip())
        if marker:
            page = int(marker.group(1))
            continue
        text = line.strip()
        if len(text) >= MIN_QUERY_LENGTH:
            result.append((page, text))
    return tuple(result)


def search_memo(path: str, query: str, limit: int = SEARCH_LIMIT) -> list[MemoHit]:
    words = [w for w in normalize(query).split(" ") if len(w) >= MIN_QUERY_LENGTH]
    if not words:
        return []
    hits: list[MemoHit] = []
    for page, text in load_paragraphs(path):
        haystack = normalize(text)
        if all(w in haystack for w in words):
            hits.append(MemoHit(page=page, text=text))
            if len(hits) >= limit:
                break
    return hits
