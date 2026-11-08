"""Печатный отчёт по результату SQL-запроса."""

import os
import re
from html import escape
from io import BytesIO
from pathlib import Path
from threading import Lock

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle
from reportlab.platypus.doctemplate import LayoutError


FONT = 'QueryReport'
GREEN = colors.HexColor('#245741')
INK = colors.HexColor('#1b2922')
MUTED = colors.HexColor('#506259')
PDF_LOCK = Lock()
UNPRINTABLE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]')


def register_report_font():
    if FONT in pdfmetrics.getRegisteredFontNames():
        return
    candidates = (
        Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/arial.ttf',
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        Path('/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'),
        Path('/System/Library/Fonts/Supplemental/Arial.ttf'),
    )
    for path in candidates:
        if path.is_file():
            pdfmetrics.registerFont(TTFont(FONT, str(path)))
            return
    raise RuntimeError('Для PDF нужен шрифт с кириллицей: Arial, DejaVu Sans или Liberation Sans.')


def paragraph(value, style, *, preserve_spaces=False):
    text = UNPRINTABLE.sub('�', value).replace('\r\n', '\n').replace('\r', '\n').expandtabs(4)
    text = escape(text, quote=False)
    if preserve_spaces:
        text = text.replace(' ', '&#160;')
    return Paragraph(text.replace('\n', '<br/>'), style)


def footer(canvas, doc):
    canvas.saveState()
    width, _ = doc.pagesize
    canvas.setStrokeColor(colors.HexColor('#dce5df'))
    canvas.line(doc.leftMargin, 31, width - doc.rightMargin, 31)
    canvas.setFont(FONT, 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, 19, 'SQL Конструктор')
    canvas.drawRightString(width - doc.rightMargin, 19, f'Страница {doc.page}')
    canvas.restoreState()


def build_pdf_report(columns, rows, question, sql, truncated=False):
    if len(columns) * len(rows) > 40_000:
        raise ValueError('Таблица слишком большая для PDF. Для полной выгрузки используйте CSV или Excel.')
    headers = [str(value) for value in columns]
    records = [['NULL' if value is None else str(value) for value in row] for row in rows]
    if any(len(header) > 256 for header in headers) or any(len(value) > 4000 for row in records for value in row):
        raise ValueError('В таблице есть слишком длинные поля для PDF. Используйте CSV или Excel.')
    if sum(len(value) for row in records for value in row) > 300_000:
        raise ValueError('Таблица слишком большая для PDF. Для полной выгрузки используйте CSV или Excel.')

    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=36, rightMargin=36,
                            topMargin=31, bottomMargin=44, title='Результат SQL-запроса', author='SQL Конструктор')
    # Шрифт TrueType ведёт внутреннее состояние при построении документа.
    with PDF_LOCK:
        register_report_font()
        body = ParagraphStyle('ReportBody', fontName=FONT, fontSize=10, leading=15, textColor=INK)
        heading = ParagraphStyle('ReportHeading', parent=body, textColor=GREEN, spaceBefore=12,
                                 spaceAfter=5, keepWithNext=True)
        title = ParagraphStyle('ReportTitle', parent=body, fontSize=22, leading=28, spaceAfter=7)
        cell = ParagraphStyle('ReportCell', parent=body, fontSize=9, leading=12)
        header = ParagraphStyle('ReportHeader', parent=cell, textColor=colors.white)
        sql_style = ParagraphStyle('ReportSQL', parent=cell, backColor=colors.HexColor('#f0f4f1'),
                                   borderPadding=7, leftIndent=7, rightIndent=7)
        note = ParagraphStyle('ReportNote', parent=body, textColor=MUTED, spaceAfter=6)
        story = [
            paragraph('Результат SQL-запроса', title),
            paragraph(f'Строк в отчёте: {len(rows)}   |   Столбцов: {len(columns)}', note),
            paragraph('Исходный вопрос', heading),
            paragraph(question or 'Вопрос не указан', body),
            paragraph('Выполненный SQL', heading),
            paragraph(sql or 'SQL не указан', sql_style, preserve_spaces=True),
            Spacer(1, 8),
        ]
        if truncated:
            story.append(paragraph('Результат ограничен: в отчёт вошли только строки, показанные приложением.', note))
        if not rows:
            story.append(paragraph('Запрос не вернул строк.', note))

        for start in range(0, len(headers), 7):
            end = min(start + 7, len(headers))
            if start:
                story.append(PageBreak())
            label = f'Столбцы {start + 1}–{end} из {len(headers)}' if len(headers) > 7 else 'Результаты'
            story.append(paragraph(label, heading))
            cells = [[paragraph(name, header) for name in ['№', *headers[start:end]]]]
            cells.extend([paragraph(str(index), cell), *(paragraph(value, cell) for value in row[start:end])]
                         for index, row in enumerate(records, start=1))
            usable_width = doc.width - 12
            widths = [32, *([(usable_width - 32) / (end - start)] * (end - start))]
            table = LongTable(cells, colWidths=widths, repeatRows=1, splitInRow=1, hAlign='LEFT')
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), GREEN),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f7f5')]),
                ('LINEBELOW', (0, 0), (-1, -1), 0.3, colors.HexColor('#dce5df')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            story.append(table)
        try:
            doc.build(story, onFirstPage=footer, onLaterPages=footer)
        except LayoutError as error:
            raise ValueError('Не удалось разместить таблицу в PDF. Используйте CSV или Excel.') from error
    output.seek(0)
    return output
