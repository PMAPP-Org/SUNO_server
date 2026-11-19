"""Planilla de tareo para firmar (costos, pieza 6d, spec 3.4), con reportlab como el corte de
6c. A4 horizontal (decisión 24 del plan): encabezado del proyecto, la cuadrícula del periodo
en bloques de hasta 16 días, totales, el resumen guardado por destino (lo que se compara al
aprobar) y firmas. Documento enmascarado; todo texto de usuario se escapa."""

from __future__ import annotations

import io
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from suno.core.context import RequestContext
from suno.core.models import Project, Timesheet
from suno.core.templatetags.money_tags import money, qty
from suno.costs.catalog import load_catalog

from .grid import period_table

__all__ = ["timesheet_pdf"]

PAGE = landscape(A4)
ACCENT = colors.HexColor("#8C2633")
GRID = colors.HexColor("#CCCCCC")
HEAD = colors.HexColor("#F0EEE9")
BLOCK_DAYS = 16


def _font() -> None:
    if "SunoReport" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont("SunoReport", str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"))
        )


def timesheet_pdf(
    ctx: RequestContext, project: Project, sheet: Timesheet, *, generated_on: date
) -> bytes:
    _font()
    styles = getSampleStyleSheet()
    body = ParagraphStyle("TaBody", parent=styles["BodyText"], fontName="SunoReport",
                          fontSize=7, leading=9)  # fmt: skip
    heading = ParagraphStyle("TaHeading", parent=body, fontSize=15, leading=20, spaceAfter=4,
                             textColor=ACCENT)  # fmt: skip
    section = ParagraphStyle("TaSection", parent=body, fontSize=10, leading=13, spaceBefore=8,
                             spaceAfter=4, keepWithNext=True)  # fmt: skip

    def table(rows: list[list[str]], widths: list[float], *, header: bool = True) -> Table:
        built = Table([[Paragraph(escape(c), body) for c in row] for row in rows],
                      colWidths=widths, repeatRows=int(header))  # fmt: skip
        style: list[Any] = [("GRID", (0, 0), (-1, -1), 0.4, GRID),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]  # fmt: skip
        if header:
            style.append(("BACKGROUND", (0, 0), (-1, 0), HEAD))
        built.setStyle(TableStyle(style))
        return built

    def grid_table(rows: list[list[str]], widths: list[float]) -> Table:
        """La cuadrícula: nombre y destino como `Paragraph` escapado (texto de usuario que se
        parte en líneas); las cifras como texto plano, que reportlab no interpreta y pinta mucho
        más rápido (60 trabajadores × 3 destinos × 31 días, en menos de 1 s)."""
        built = Table(
            [[Paragraph(escape(row[0]), body), Paragraph(escape(row[1]), body), *row[2:]]
             for row in rows],
            colWidths=widths, repeatRows=1,
        )  # fmt: skip
        built.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, GRID),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), HEAD),
            ("FONTNAME", (2, 0), (-1, -1), "SunoReport"),
            ("FONTSIZE", (2, 0), (-1, -1), 7),
            ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ]))  # fmt: skip
        return built

    width = PAGE[0] - 72
    data = period_table(ctx, sheet, load_catalog(ctx, project))
    summary = sheet.summary or {}
    fingerprint = summary.get("fingerprint", {})
    story: list[Any] = [
        Paragraph(f"Planilla de tareo {sheet.code}", heading),
        table([["Proyecto", project.name],
               ["Periodo", f"{sheet.starts_on:%d/%m/%Y} – {sheet.ends_on:%d/%m/%Y}"],
               ["Líneas y jornales", f"{fingerprint.get('lines', 0)} líneas · "
                                     f"{qty(fingerprint.get('days', '0'))} jornales"]],
              [width * 0.2, width * 0.8], header=False),
    ]  # fmt: skip
    for start in range(0, len(data.days), BLOCK_DAYS):
        days = data.days[start : start + BLOCK_DAYS]
        fixed = width * 0.36
        cell = (width - fixed - width * 0.08) / len(days)
        story.append(Paragraph(f"Días {days[0]:%d/%m} a {days[-1]:%d/%m}", section))
        story.append(grid_table(
            [["Trabajador", "Destino", *[f"{d:%a %d}" for d in days], "Jornales"],
             *[[worker, label, *cells[start : start + BLOCK_DAYS], qty(total)]
               for worker, label, cells, total, _amount in data.rows],
             ["Total del día", "", *[qty(t) for t in data.day_totals[start : start + BLOCK_DAYS]],
              qty(sum(data.day_totals[start : start + BLOCK_DAYS]))]],
            [width * 0.18, width * 0.18, *[cell] * len(days), width * 0.08],
        ))  # fmt: skip
    story.append(Paragraph("Resumen por destino (es lo que se compara al aprobar)", section))
    story.append(table(
        [["Destino", "Jornales", "Costo estimado"],
         *[[g["label"], qty(g["days"]), money(g["total"])] for g in summary.get("groups", [])],
         ["Total", qty(fingerprint.get("days", "0")), money(fingerprint.get("total", "0"))]],
        [width * 0.6, width * 0.15, width * 0.25],
    ))  # fmt: skip
    story += [
        Spacer(1, 28),
        table([["", "", ""], ["Maestro de obra", "Residente", "Aprobó"]], [width / 3] * 3,
              header=False),
        Spacer(1, 6),
        Paragraph(f"Costo estimado = fracción × jornal × factor prestacional. Generado por SUNO "
                  f"el {generated_on:%d/%m/%Y}.", body),
    ]  # fmt: skip

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("SunoReport", 8)
        canvas.drawString(36, 20, f"SUNO · {project.name} · {sheet.code}")
        canvas.drawRightString(PAGE[0] - 36, 20, str(doc.page))
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=PAGE, leftMargin=36, rightMargin=36, topMargin=30,
                      bottomMargin=36, title=f"Planilla {sheet.code}", author="SUNO",
                      ).build(story, onFirstPage=footer, onLaterPages=footer)  # fmt: skip
    return buffer.getvalue()
