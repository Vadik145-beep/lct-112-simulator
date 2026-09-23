"""Export of the session report (PRD 13.7): PDF, XLSX and CSV built from ``ReportOut``.

PDF is rendered with ReportLab and the embedded DejaVu Sans font (Cyrillic, the license is
next to the files); nothing is fetched from the network. CSV uses «;» and a BOM so that a
Russian Excel opens it as a table without an import dialog. Rendering is CPU work and runs
in a thread so the API keeps answering (a seed-size report takes well under a second).
"""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.config import get_settings
from app.training.teacher_schemas import ReportAttempt, ReportOut, ReportStudent

FONT = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"
FONTS_DIR = Path(__file__).parent / "fonts"
# Byte order mark: Excel then reads the CSV as UTF-8.
BOM = "\ufeff"

STATE_TITLES = {
    "issued": "выдана",
    "received": "открыта",
    "in_progress": "в работе",
    "finished": "закрыта",
    "evaluated": "оценена",
}
DECISION_TITLES = {"accept": "Принята", "reject": "Не принята"}

ATTEMPT_COLUMNS = [
    "Обучающийся",
    "Логин",
    "Карточка",
    "Происшествие",
    "Выдана",
    "Закрыта",
    "Балл",
    "Зачтено",
    "Время, с",
    "Норматив, с",
    "Отличие от норматива, с",
    "Решение",
    "Эталон решения",
    "Грамотность, %",
    "Ошибки",
    "Замечания",
    "Оценка изменена",
    "Причина изменения",
    "Действия",
]


def _register_fonts() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if FONT in pdfmetrics.getRegisteredFontNames():
        return
    pdfmetrics.registerFont(TTFont(FONT, str(FONTS_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONTS_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(
        FONT, normal=FONT, bold=FONT_BOLD, italic=FONT, boldItalic=FONT_BOLD
    )


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().tz)
    except Exception:  # an unknown TZ in .env must not break the export
        return ZoneInfo("Europe/Moscow")


def _dt(value: datetime | None, tz: ZoneInfo) -> str:
    return value.astimezone(tz).strftime("%d.%m.%Y %H:%M:%S") if value else ""


def _num(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _passed(value: bool | None) -> str:
    if value is None:
        return ""
    return "да" if value else "нет"


def _decision(code: str | None) -> str:
    return DECISION_TITLES.get(code or "", code or "")


def _mmss(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def actions_text(a: ReportAttempt) -> list[str]:
    """«0:41 Принята», «1:05 Начало реагирования — наряд 14-217; …» — one line per step."""
    return [
        f"{_mmss(x.seconds)} {x.title}" + (f" — {x.detail}" if x.detail else "") for x in a.actions
    ]


def attempt_row(student: ReportStudent, a: ReportAttempt, tz: ZoneInfo) -> list[str]:
    return [
        student.full_name,
        student.login,
        a.card_number,
        a.incident_title or a.scenario_title,
        _dt(a.issued_at, tz),
        _dt(a.submitted_at, tz),
        _num(a.total) if a.total is not None else STATE_TITLES.get(a.state, a.state),
        _passed(a.passed),
        _num(a.seconds),
        str(a.norm_seconds),
        _num(a.deviation),
        _decision(a.decision_actual),
        _decision(a.decision_expected),
        _num(a.grammar_percent),
        "; ".join(a.errors),
        " | ".join(a.remarks),
        "да" if a.overridden else "",
        a.override_reason or "",
        " | ".join(actions_text(a)),
    ]


def student_row(s: ReportStudent) -> list[str]:
    return [
        s.full_name,
        s.login,
        s.service_code or "",
        str(s.attempts_total),
        str(s.evaluated),
        str(s.passed),
        _num(s.average),
        _num(s.average_seconds),
        _num(s.average_deviation),
        str(s.wrong_decisions),
        "; ".join(f"{e.title} ×{e.count}" for e in s.typical_errors),
        _num(s.grammar_percent),
    ]


STUDENT_COLUMNS = [
    "Обучающийся",
    "Логин",
    "Служба",
    "Попыток",
    "Оценено",
    "Зачтено",
    "Средний балл",
    "Среднее время, с",
    "Отличие от норматива, с",
    "Неверных решений",
    "Типичные ошибки",
    "Грамотность, %",
]


def _summary_rows(report: ReportOut, tz: ZoneInfo) -> list[tuple[str, str]]:
    s = report.summary
    return [
        ("Занятие", report.title),
        ("Начато", _dt(report.started_at, tz)),
        ("Завершено", _dt(report.finished_at, tz)),
        ("Норматив, с", str(report.norm_seconds)),
        ("Порог зачёта", str(report.pass_threshold)),
        ("Обучающихся в группе", str(s.students)),
        ("Участвовали", str(s.participated)),
        ("Карточек оценено", str(s.evaluated)),
        ("Зачтено", str(s.passed)),
        ("Средний балл", _num(s.average)),
        ("Среднее время, с", _num(s.average_seconds)),
        ("Типичные ошибки группы", "; ".join(f"{e.title} ×{e.count}" for e in s.typical_errors)),
    ]


# ---------------------------------------------------------------- CSV


def to_csv(report: ReportOut) -> bytes:
    tz = _tz()
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(ATTEMPT_COLUMNS)
    for student in report.students:
        for a in student.attempts:
            writer.writerow(attempt_row(student, a, tz))
    return (BOM + buffer.getvalue()).encode("utf-8")


# ---------------------------------------------------------------- XLSX


def _autosize(sheet, widths: list[int]) -> None:
    for i, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(i)].width = width


def to_xlsx(report: ReportOut) -> bytes:
    tz = _tz()
    book = Workbook()
    head_font = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="DDE3EA")

    summary = book.active
    summary.title = "Итоги"
    summary.append(["Отчёт о занятии"])
    summary["A1"].font = Font(bold=True, size=14)
    for label, value in _summary_rows(report, tz):
        summary.append([label, value])
    _autosize(summary, [34, 80])

    students = book.create_sheet("Обучающиеся")
    students.append(STUDENT_COLUMNS)
    for s in report.students:
        students.append(student_row(s))
    _autosize(students, [32, 14, 16, 10, 10, 10, 14, 16, 22, 16, 50, 14])

    attempts = book.create_sheet("Попытки")
    attempts.append(ATTEMPT_COLUMNS)
    for s in report.students:
        for a in s.attempts:
            attempts.append(attempt_row(s, a, tz))
    _autosize(attempts, [32, 14, 12, 40, 20, 20, 8, 10, 12, 10, 14, 14, 14, 12, 40, 70, 10, 40])

    for sheet in (students, attempts):
        for cell in sheet[1]:
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------- PDF


def to_pdf(report: ReportOut) -> bytes:
    # ReportLab is imported here: it takes seconds to load and only the PDF export needs it.
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    _register_fonts()
    tz = _tz()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"Отчёт о занятии: {report.title}",
        author="Тренажёр оператора ДДС-112",
    )
    body = ParagraphStyle("body", fontName=FONT, fontSize=8, leading=10, alignment=TA_LEFT)
    small = ParagraphStyle("small", parent=body, fontSize=7, leading=8.5)
    h1 = ParagraphStyle("h1", fontName=FONT_BOLD, fontSize=14, leading=18, spaceAfter=4)
    h2 = ParagraphStyle(
        "h2", fontName=FONT_BOLD, fontSize=10, leading=13, spaceBefore=6, spaceAfter=3
    )
    grid = TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, -1), FONT),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDE3EA")),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#9AA4B2")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]
    )

    def p(text: str, style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(_escape(text), style)

    story: list = [p(f"Отчёт о занятии: {report.title}", h1)]
    summary_table = Table(
        [[p(label, body), p(value, body)] for label, value in _summary_rows(report, tz)],
        colWidths=[60 * mm, 200 * mm],
    )
    summary_table.setStyle(grid)
    story += [summary_table, Spacer(1, 4 * mm), p("Обучающиеся", h2)]

    student_widths = [40, 18, 18, 12, 12, 12, 14, 16, 18, 14, 74, 16]
    rows = [[p(c, small) for c in STUDENT_COLUMNS]]
    for s in report.students:
        rows.append([p(c, small) for c in student_row(s)])
    table = Table(rows, colWidths=[w * mm for w in student_widths], repeatRows=1)
    table.setStyle(grid)
    story.append(table)

    for s in report.students:
        if not s.attempts:
            continue
        story.append(p(f"Попытки: {s.full_name} ({s.login})", h2))
        columns = [
            "Карточка",
            "Происшествие",
            "Выдана",
            "Балл",
            "Зачтено",
            "Время / норматив",
            "Решение",
            "Ошибки",
            "Замечания",
        ]
        widths = [16, 46, 24, 12, 14, 26, 24, 44, 60]
        rows = [[p(c, small) for c in columns]]
        for a in s.attempts:
            sign = "+" if (a.deviation or 0) > 0 else ""
            time_text = (
                ""
                if a.seconds is None
                else f"{_num(a.seconds)} / {a.norm_seconds} ({sign}{_num(a.deviation)})"
            )
            decision = _decision(a.decision_actual)
            if a.decision_correct is False:
                decision += f" (эталон: {_decision(a.decision_expected)})"
            score = _num(a.total) if a.total is not None else STATE_TITLES.get(a.state, a.state)
            if a.overridden:
                score += " *"
            rows.append(
                [
                    p(a.card_number, small),
                    p(a.incident_title or a.scenario_title, small),
                    p(_dt(a.issued_at, tz), small),
                    p(score, small),
                    p(_passed(a.passed), small),
                    p(time_text, small),
                    p(decision, small),
                    p("; ".join(a.errors), small),
                    # One paragraph per line: a Paragraph escapes its text, so a «<br/>»
                    # inside would print literally.
                    [p(r, small) for r in a.remarks]
                    + ([p("Действия:", small)] if a.actions else [])
                    + [p(r, small) for r in actions_text(a)],
                ]
            )
        table = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1)
        table.setStyle(grid)
        story.append(KeepTogether([table]) if len(rows) < 8 else table)
    if any(a.overridden for s in report.students for a in s.attempts):
        story.append(p("* оценка изменена преподавателем, причина в замечаниях", small))
    doc.build(story)
    return buffer.getvalue()


# ---------------------------------------------------------------- async wrappers

FORMATS = {
    "pdf": ("application/pdf", to_pdf),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", to_xlsx),
    "csv": ("text/csv; charset=utf-8", to_csv),
}


async def render(report: ReportOut, fmt: str) -> tuple[bytes, str]:
    """Bytes and media type of the export; the format must be one of FORMATS."""
    media_type, builder = FORMATS[fmt]
    data = await asyncio.to_thread(builder, report)
    return data, media_type
