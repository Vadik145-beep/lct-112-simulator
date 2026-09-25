"""Сборка сопроводительной документации в один DOCX."""

import re
import sys
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, Cm

ROOT = Path(sys.argv[1])
OUT = Path(sys.argv[2])

FILES = [
    ("Пояснительная записка", "docs/EXPLANATORY_NOTE.md"),
    ("Маршрут проверки решения", "docs/EXPERT_GUIDE.md"),
    ("Архитектура решения", "docs/ARCHITECTURE.md"),
    ("Методы обработки данных", "docs/METHODS.md"),
    ("Установка, настройка и восстановление", "docs/INSTALL.md"),
    ("Руководство пользователя", "docs/USER_GUIDE.md"),
    ("Данные заказчика", "docs/DATASET.md"),
    ("Условия и ограничения решения", "docs/LIMITATIONS.md"),
    ("Производительность и нагрузка", "docs/PERFORMANCE.md"),
    ("Проверка подсчёта оценки на живых звонках", "docs/VOICE_TESTS.md"),
    ("Использованные библиотеки и компоненты", "docs/LIBRARIES.md"),
    ("Соответствие техническому заданию", "docs/REQUIREMENTS_MATRIX.md"),
]

LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
CODE = re.compile(r"`([^`]+)`")


def add_runs(par, text):
    """Пишет текст с учётом **жирного** и `кода`."""
    text = LINK.sub(r"\1", text)
    pos = 0
    pattern = re.compile(r"\*\*([^*]+)\*\*|`([^`]+)`")
    for m in pattern.finditer(text):
        if m.start() > pos:
            par.add_run(text[pos : m.start()])
        if m.group(1) is not None:
            par.add_run(m.group(1)).bold = True
        else:
            r = par.add_run(m.group(2))
            r.font.name = "Consolas"
            r.font.size = Pt(10)
        pos = m.end()
    if pos < len(text):
        par.add_run(text[pos:])


def add_toc(doc):
    par = doc.add_paragraph()
    run = par.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-2" \\h \\z \\u'
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "Оглавление обновится при открытии документа."
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    for el in (fld_begin, instr, fld_sep, placeholder, fld_end):
        run._r.append(el)


def flush_paragraph(doc, buf):
    if not buf:
        return
    par = doc.add_paragraph()
    add_runs(par, " ".join(buf))
    buf.clear()


def add_table(doc, rows):
    rows = [r for r in rows if not re.match(r"^\|[\s:|-]+\|$", r.strip())]
    if not rows:
        return
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    width = max(len(r) for r in cells)
    table = doc.add_table(rows=0, cols=width)
    table.style = "Table Grid"
    for n, row in enumerate(cells):
        cs = table.add_row().cells
        for j in range(width):
            text = row[j] if j < len(row) else ""
            par = cs[j].paragraphs[0]
            add_runs(par, text)
            for run in par.runs:
                run.font.size = Pt(9)
                if n == 0:
                    run.bold = True


def render(doc, md):
    lines = md.splitlines()
    buf, table_buf, code_buf = [], [], []
    in_code = False
    for line in lines:
        if line.strip().startswith("```"):
            if in_code:
                flush_paragraph(doc, buf)
                par = doc.add_paragraph()
                run = par.add_run("\n".join(code_buf))
                run.font.name = "Consolas"
                run.font.size = Pt(9)
                code_buf.clear()
            in_code = not in_code
            continue
        if in_code:
            code_buf.append(line)
            continue
        if line.strip().startswith("|"):
            flush_paragraph(doc, buf)
            table_buf.append(line)
            continue
        if table_buf:
            add_table(doc, table_buf)
            table_buf = []
        img = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", line)
        if img:
            flush_paragraph(doc, buf)
            path = (ROOT / "docs" / img.group(2)).resolve()
            if path.exists():
                doc.add_picture(str(path), width=Cm(16))
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                if img.group(1):
                    cap = doc.add_paragraph()
                    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    run = cap.add_run(img.group(1))
                    run.italic = True
                    run.font.size = Pt(10)
            continue
        m = re.match(r"^(#{2,6})\s+(.*)$", line)
        if m:
            flush_paragraph(doc, buf)
            level = min(len(m.group(1)), 4)
            doc.add_heading(LINK.sub(r"\1", BOLD.sub(r"\1", m.group(2))), level=level)
            continue
        if re.match(r"^\s*[-*]\s+", line):
            flush_paragraph(doc, buf)
            par = doc.add_paragraph(style="List Bullet")
            add_runs(par, re.sub(r"^\s*[-*]\s+", "", line))
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            flush_paragraph(doc, buf)
            par = doc.add_paragraph(style="List Number")
            add_runs(par, re.sub(r"^\s*\d+\.\s+", "", line))
            continue
        if not line.strip():
            flush_paragraph(doc, buf)
            continue
        buf.append(line.strip())
    flush_paragraph(doc, buf)
    if table_buf:
        add_table(doc, table_buf)


doc = Document()
style = doc.styles["Normal"]
style.font.name = "Times New Roman"
style.font.size = Pt(12)
for section in doc.sections:
    section.left_margin = Cm(2)
    section.right_margin = Cm(1.5)
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)

# титульный лист
for _ in range(6):
    doc.add_paragraph()
title = doc.add_paragraph()
title.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = title.add_run("Тренажёр оператора ДДС-112")
run.bold = True
run.font.size = Pt(24)
for text, size, bold in [
    ("Сопроводительная документация к решению", 14, False),
    ("", 12, False),
    ("Хакатон «Лидеры цифровой трансформации 2026»", 12, False),
    (
        "Задача № 9. Учебное программное обеспечение для подготовки оператора ДДС города Москвы "
        "с использованием искусственного интеллекта",
        12,
        False,
    ),
    ("", 12, False),
    (
        "Постановщик: Департамент по делам гражданской обороны, чрезвычайным ситуациям "
        "и пожарной безопасности города Москвы, ГБУ «Система 112»",
        12,
        False,
    ),
    ("", 12, False),
    ("Команда «Ультратех»", 12, True),
    (date.today().strftime("%d.%m.%Y"), 12, False),
]:
    par = doc.add_paragraph()
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = par.add_run(text)
    r.font.size = Pt(size)
    r.bold = bold

doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
doc.add_heading("Содержание", level=1)
add_toc(doc)

for n, (title_text, rel) in enumerate(FILES, 1):
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    doc.add_heading(f"{n}. {title_text}", level=1)
    md = (ROOT / rel).read_text(encoding="utf-8")
    md = re.sub(r"^#\s+.*$", "", md, count=1, flags=re.M)
    render(doc, md)

doc.save(OUT)
print("DOCX собран:", OUT)
