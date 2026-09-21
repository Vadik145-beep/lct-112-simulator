"""Methodical materials the trainee can read in full: the organizers' memo and the
documents a teacher uploaded to the reference (ТЗ, роль обучающегося: «просматривать
инструкции и методические материалы»)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.domain.memo_search import MIN_QUERY_LENGTH, load_paragraphs, normalize
from app.providers.rag import split_document

MEMO_NAME = "memo"
MEMO_TITLE = "Работа на АРМ-112. Памятка для дежурно-диспетчерских служб"
SAFE_NAME = re.compile(r"^[\w\-. ]{1,80}$")


@dataclass(frozen=True)
class Material:
    name: str
    title: str
    builtin: bool
    paragraphs: int
    size: int
    updated_at: datetime | None


@dataclass(frozen=True)
class Paragraph:
    page: int | None
    text: str


def _memo(memo_path: str) -> Material | None:
    file = Path(memo_path)
    if not file.exists():
        return None
    paragraphs = load_paragraphs(memo_path)
    return Material(
        name=MEMO_NAME,
        title=MEMO_TITLE,
        builtin=True,
        paragraphs=len(paragraphs),
        size=sum(len(p) for _, p in paragraphs),
        updated_at=None,
    )


def _uploaded(docs_dir: Path) -> list[Material]:
    result = []
    for file in sorted(docs_dir.glob("*.txt")):
        text = file.read_text(encoding="utf-8")
        result.append(
            Material(
                name=file.stem,
                title=file.stem,
                builtin=False,
                paragraphs=len(split_document(text)),
                size=len(text),
                updated_at=datetime.fromtimestamp(file.stat().st_mtime, tz=UTC),
            )
        )
    return result


def list_materials(memo_path: str, docs_dir: Path) -> list[Material]:
    memo = _memo(memo_path)
    return ([memo] if memo else []) + _uploaded(docs_dir)


def read_material(
    name: str, memo_path: str, docs_dir: Path
) -> tuple[Material, list[Paragraph]] | None:
    if name == MEMO_NAME:
        memo = _memo(memo_path)
        if memo is None:
            return None
        return memo, [Paragraph(page=page, text=text) for page, text in load_paragraphs(memo_path)]
    if not SAFE_NAME.match(name):
        return None
    file = docs_dir / f"{name}.txt"
    if not file.exists():
        return None
    text = file.read_text(encoding="utf-8")
    material = Material(
        name=name,
        title=name,
        builtin=False,
        paragraphs=0,
        size=len(text),
        updated_at=datetime.fromtimestamp(file.stat().st_mtime, tz=UTC),
    )
    chunks = split_document(text)
    return material, [Paragraph(page=None, text=c) for c in chunks]


@dataclass(frozen=True)
class DocHit:
    name: str
    title: str
    text: str


def search_uploaded(docs_dir: Path, query: str, limit: int) -> list[DocHit]:
    """Paragraphs of the uploaded documents containing every word of the query."""
    words = [w for w in normalize(query).split(" ") if len(w) >= MIN_QUERY_LENGTH]
    if not words:
        return []
    hits: list[DocHit] = []
    for file in sorted(docs_dir.glob("*.txt")):
        for chunk in split_document(file.read_text(encoding="utf-8")):
            haystack = normalize(chunk)
            if all(w in haystack for w in words):
                hits.append(DocHit(name=file.stem, title=file.stem, text=chunk))
                if len(hits) >= limit:
                    return hits
    return hits
