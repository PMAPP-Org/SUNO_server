"""Corte de consumos para firmar (costos, pieza 6c, spec 3.4), con reportlab como el acta de
6b (`contracts/pdf.py`). A4 horizontal: destinos con su desglose por material, totales y
firmas. Imprime el resumen guardado del corte (`summary`): lo firmado es lo que se aprueba."""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
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

from suno.core.models import ConsumptionCut, Project
from suno.core.templatetags.money_tags import money, qty

__all__ = ["cut_pdf"]

PAGE = landscape(A4)
ACCENT = colors.HexColor("#8C2633")
INK = colors.HexColor("#171717")
GRID = colors.HexColor("#CCCCCC")
HEAD = colors.HexColor("#F0EEE9")


def _font() -> None:
    if "SunoReport" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont("SunoReport", str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"))
        )


def cut_pdf(project: Project, cut: ConsumptionCut, *, generated_on: date) -> bytes:
    _font()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "CutBody", parent=styles["BodyText"], fontName="SunoReport", fontSize=8, leading=10.5
    )
    heading = ParagraphStyle(
        "CutHeading", parent=body, fontSize=15, leading=20, spaceAfter=4, textColor=ACCENT
    )
    section = ParagraphStyle(
        "CutSection", parent=body, fontSize=10.5, leading=14, spaceBefore=10, spaceAfter=4,
        textColor=INK, keepWithNext=True,
    )  # fmt: skip

    def table(rows: list[list[str]], widths: list[float], *, header: bool = True) -> Table:
        built = Table(
            [[Paragraph(escape(c), body) for c in row] for row in rows],
            colWidths=widths,
            repeatRows=int(header),
        )
        style: list[Any] = [
            ("GRID", (0, 0), (-1, -1), 0.4, GRID),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        if header:
            style.append(("BACKGROUND", (0, 0), (-1, 0), HEAD))
        built.setStyle(TableStyle(style))
        return built

    width = PAGE[0] - 72
    summary = cut.summary or {}
    fingerprint = summary.get("fingerprint", {})
    story: list[Any] = [
        Paragraph(f"Corte de consumos N.º {cut.number}", heading),
        table(
            [
                ["Proyecto", project.name],
                ["Periodo", f"{cut.period_start:%d/%m/%Y} – {cut.period_end:%d/%m/%Y}"],
                [
                    "Salidas y devoluciones",
                    f"{fingerprint.get('issues', 0)} salidas · {fingerprint.get('returns', 0)} "
                    "devoluciones",
                ],
            ],
            [width * 0.22, width * 0.78],
            header=False,
        ),  # fmt: skip
    ]
    for group in summary.get("groups", []):
        story.append(Paragraph(escape(f"{group['label']} · neto {money(group['net'])}"), section))
        story.append(
            table(
                [
                    ["Código", "Material", "Unidad", "Salió", "Valor", "Devuelto", "Valor",
                     "Neto"],
                    *[
                        [
                            line["code"], line["name"], line["unit"],
                            qty(line["issued_quantity"]), money(line["issued_value"]),
                            qty(line["returned_quantity"]), money(line["returned_value"]),
                            money(Decimal(line["issued_value"]) - Decimal(line["returned_value"])),
                        ]
                        for line in group["materials"]
                    ],
                ],
                [width * 0.1, width * 0.28, width * 0.07, *[width * 0.11] * 5],
            )
        )  # fmt: skip
    story += [
        Spacer(1, 6),
        table([["Total del corte (costo de materiales del periodo)",
                money(fingerprint.get("total", "0"))]],
              [width * 0.6, width * 0.2], header=False),
        Spacer(1, 28),
        table(
            [["", "", ""], ["Preparó (almacén)", "Revisó (residente o interventoría)", "Aprobó"]],
            [width / 3] * 3,
            header=False,
        ),
        Spacer(1, 6),
        Paragraph(f"Generado por SUNO el {generated_on:%d/%m/%Y}.", body),
    ]  # fmt: skip

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("SunoReport", 8)
        canvas.drawString(36, 20, f"SUNO · {project.name} · {cut.code}")
        canvas.drawRightString(PAGE[0] - 36, 20, str(doc.page))
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=PAGE, leftMargin=36, rightMargin=36, topMargin=30, bottomMargin=36,
        title=f"{cut.code} de consumos", author="SUNO",
    ).build(story, onFirstPage=footer, onLaterPages=footer)  # fmt: skip
    return buffer.getvalue()
