"""Extracts the text of the organizers' memo «Работа на АРМ-112» into data/seed/memo.txt.

The PDF has a text layer, so no OCR is needed; text blocks are joined per paragraph and
pages are separated by a marker line so later waves can reference a page (e.g. the table
of response statuses is on pages 21-23, typical violations on pages 28-31).

One-off preparation step: uv run --group dataset python -m app.importers.memo_extract
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[3]
DEFAULT_PDF = REPO_DIR / "data" / "organizers" / "Работа_с_АРМ-112_для_ДДС.pdf"
DEFAULT_OUT = REPO_DIR / "data" / "seed" / "memo.txt"
PAGE_MARKER = "===== Страница {n} ====="


def _join_block(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    joined = " ".join(lines)
    joined = re.sub(r"\s+", " ", joined)
    # Hyphenated words split by the layout: «АРМ -112» → «АРМ-112».
    return re.sub(r"(\S) -(\d)", r"\1-\2", joined)


def extract(pdf_path: Path) -> str:
    import pymupdf

    document = pymupdf.open(pdf_path)
    pages: list[str] = []
    for number, page in enumerate(document, start=1):
        blocks = page.get_text("blocks", sort=True)
        paragraphs = [_join_block(b[4]) for b in blocks if b[6] == 0]
        # Drop the bare page number that the layout puts on top of each page.
        paragraphs = [p for p in paragraphs if p and p != str(number)]
        pages.append(PAGE_MARKER.format(n=number) + "\n" + "\n".join(paragraphs))
    return "\n\n".join(pages) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Текст памятки «Работа на АРМ-112»")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    if not args.pdf.exists():
        print(f"Файл не найден: {args.pdf}. Положите файлы организаторов в data/organizers/.")
        return 1
    text = extract(args.pdf)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"Страниц: {text.count('===== Страница')}, символов: {len(text)} → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
