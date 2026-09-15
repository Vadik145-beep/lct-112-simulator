"""OCR of the organizers' ticket scans into data/seed/tickets.json.

The PDF holds 32 scanned pages, one ticket per page, each with a three-row table
"№ | Ситуация | Адрес". Pages carry /Rotate 180, so the renderer already turns them
upright. The table grid is found by projecting dark pixels onto the axes; every cell
is then recognized separately (Tesseract, language ``rus``), which keeps the two text
columns from being interleaved.

This is a one-off preparation step, not part of the running application:

    uv run --group dataset python -m app.importers.tickets_ocr

Environment: TESSERACT_CMD (path to the binary), TESSDATA_PREFIX (folder with
rus.traineddata). The result is reviewed by a person and committed.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
DEFAULT_PDF = REPO_DIR / "data" / "organizers" / "Билеты-задачи_по_С112_АГС_ГСИ.pdf"
DEFAULT_OUT = REPO_DIR / "data" / "seed" / "tickets.json"
# Manual fixes found while comparing the recognized text with the scans; applied after OCR so
# the whole pipeline stays reproducible. Format: [{ticket_no, item_no, field, from, to, note}];
# an entry with {"confirmed": true} instead of a replacement marks the row as checked by eye.
DEFAULT_CORRECTIONS = REPO_DIR / "data" / "seed" / "tickets_corrections.json"

RENDER_DPI = 300
# A vertical grid line covers a good part of the page height; a horizontal one most of the
# table width. Both thresholds are fractions of the image size.
V_LINE_MIN_FRACTION = 0.12
H_LINE_MIN_FRACTION = 0.45
# Mean Tesseract word confidence below which a cell is flagged for manual review.
CONFIDENT_THRESHOLD = 80.0
CELL_PADDING_PX = 8
ROWS_PER_TICKET = 3
# Characters that do not occur in the printed tickets: their presence means a misread glyph.
SUSPICIOUS = re.compile(r"[\[\]\\{}|#@$^*_~`<>]|[A-Za-z]{2,}")


@dataclass
class TicketItem:
    ticket_no: int
    item_no: int
    situation: str
    address: str
    ocr_confident: bool
    confidence: float


def is_confident(situation: str, address: str, confidence: float) -> bool:
    if not situation or confidence < CONFIDENT_THRESHOLD:
        return False
    return not (SUSPICIOUS.search(situation) or SUSPICIOUS.search(address))


def apply_corrections(items: list[TicketItem], corrections: list[dict]) -> list[str]:
    """Applies manual fixes in place; returns problems (a fix whose text was not found)."""
    by_key = {(i.ticket_no, i.item_no): i for i in items}
    problems: list[str] = []
    for fix in corrections:
        item = by_key.get((fix["ticket_no"], fix["item_no"]))
        if item is None:
            problems.append(f"билет {fix['ticket_no']} №{fix['item_no']}: нет такой ситуации")
            continue
        if fix.get("confirmed"):
            item.ocr_confident = True
            continue
        current = getattr(item, fix["field"])
        if fix["from"] not in current:
            problems.append(
                f"билет {fix['ticket_no']} №{fix['item_no']}: не найдено «{fix['from']}»"
            )
            continue
        setattr(item, fix["field"], current.replace(fix["from"], fix["to"]))
        item.ocr_confident = is_confident(item.situation, item.address, item.confidence)
    return problems


def _group_runs(indexes: list[int], gap: int = 3) -> list[tuple[int, int]]:
    """Collapses consecutive pixel indexes into (start, end) runs."""
    runs: list[list[int]] = []
    for i in indexes:
        if runs and i - runs[-1][-1] <= gap:
            runs[-1].append(i)
        else:
            runs.append([i])
    return [(r[0], r[-1]) for r in runs]


def find_grid(image) -> tuple[list[int], list[int]]:
    """Returns x positions of vertical lines and y positions of horizontal lines.

    On some scans the table touches the bottom edge and its last horizontal line is cut
    off; then the lower end of the vertical lines serves as the last row boundary.
    """
    import numpy as np

    dark = np.array(image.convert("L")) < 128
    height, width = dark.shape
    vertical = _group_runs(list(np.where(dark.sum(axis=0) > height * V_LINE_MIN_FRACTION)[0]))
    horizontal = _group_runs(list(np.where(dark.sum(axis=1) > width * H_LINE_MIN_FRACTION)[0]))
    xs = [int((a + b) / 2) for a, b in vertical]
    ys = [int((a + b) / 2) for a, b in horizontal]
    if xs and len(ys) == ROWS_PER_TICKET + 1:
        column = dark[:, xs[0] - 1 : xs[0] + 2].any(axis=1)
        bottom = int(np.where(column)[0].max())
        if bottom > ys[-1] + CELL_PADDING_PX:
            ys.append(bottom)
    return xs, ys


def _clean(text: str) -> str:
    text = text.replace("|", "").replace("\u00ad", "")
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    return " ".join(ln for ln in lines if ln).strip()


def ocr_cell(image, box: tuple[int, int, int, int], lang: str) -> tuple[str, float]:
    import pytesseract

    cell = image.crop(box)
    data = pytesseract.image_to_data(
        cell, lang=lang, config="--psm 6", output_type=pytesseract.Output.DICT
    )
    words = [
        (w, float(c))
        for w, c in zip(data["text"], data["conf"], strict=True)
        if str(w).strip() and float(c) >= 0
    ]
    text = _clean(pytesseract.image_to_string(cell, lang=lang, config="--psm 6"))
    confidence = sum(c for _, c in words) / len(words) if words else 0.0
    return text, round(confidence, 1)


def _ticket_number(image, lang: str, top_y: int) -> int | None:
    import pytesseract

    header = image.crop((0, max(0, top_y - 500), image.width, top_y))
    text = pytesseract.image_to_string(header, lang=lang, config="--psm 6")
    match = re.search(r"БИЛЕТ\s*(\d+)", text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def recognize_page(image, page_index: int, lang: str) -> list[TicketItem]:
    xs, ys = find_grid(image)
    if len(xs) < 4 or len(ys) < ROWS_PER_TICKET + 2:
        raise ValueError(
            f"страница {page_index + 1}: не найдена таблица (вертикалей {len(xs)}, "
            f"горизонталей {len(ys)})"
        )
    xs, ys = xs[:4], ys[: ROWS_PER_TICKET + 2]
    ticket_no = _ticket_number(image, lang, ys[0]) or page_index + 1
    items: list[TicketItem] = []
    for row in range(ROWS_PER_TICKET):
        top, bottom = ys[row + 1] + CELL_PADDING_PX, ys[row + 2] - CELL_PADDING_PX
        situation, conf_s = ocr_cell(
            image, (xs[1] + CELL_PADDING_PX, top, xs[2] - CELL_PADDING_PX, bottom), lang
        )
        address, conf_a = ocr_cell(
            image, (xs[2] + CELL_PADDING_PX, top, xs[3] - CELL_PADDING_PX, bottom), lang
        )
        confidence = round(min(conf_s, conf_a), 1)
        items.append(
            TicketItem(
                ticket_no=ticket_no,
                item_no=row + 1,
                situation=situation,
                address=address,
                ocr_confident=is_confident(situation, address, confidence),
                confidence=confidence,
            )
        )
    return items


def recognize_pdf(pdf_path: Path, lang: str = "rus") -> list[TicketItem]:
    import pymupdf
    import pytesseract
    from PIL import Image

    if cmd := os.environ.get("TESSERACT_CMD"):
        pytesseract.pytesseract.tesseract_cmd = cmd
    document = pymupdf.open(pdf_path)
    items: list[TicketItem] = []
    for index, page in enumerate(document):
        pixmap = page.get_pixmap(dpi=RENDER_DPI)
        image = Image.open(io.BytesIO(pixmap.tobytes("png")))
        page_items = recognize_page(image, index, lang)
        items.extend(page_items)
        low = sum(1 for i in page_items if not i.ocr_confident)
        print(f"билет {page_items[0].ticket_no}: {len(page_items)} ситуации, неуверенных {low}")
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Распознавание билетов организаторов")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--lang", default="rus")
    parser.add_argument("--corrections", type=Path, default=DEFAULT_CORRECTIONS)
    args = parser.parse_args(argv)
    if not args.pdf.exists():
        print(f"Файл не найден: {args.pdf}. Положите файлы организаторов в data/organizers/.")
        return 1
    items = recognize_pdf(args.pdf, args.lang)
    if args.corrections.exists():
        corrections = json.loads(args.corrections.read_text(encoding="utf-8"))
        for problem in apply_corrections(items, corrections):
            print(f"Правка не применена: {problem}")
        print(f"Ручных правок: {len(corrections)}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps([asdict(i) for i in items], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    unsure = [i for i in items if not i.ocr_confident]
    print(f"Всего ситуаций: {len(items)}, неуверенных: {len(unsure)} → {args.out}")
    for i in unsure:
        print(f"  билет {i.ticket_no} №{i.item_no} ({i.confidence}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
