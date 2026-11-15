"""Acta para firmar (costos, pieza 6b, spec 4 "PDF del acta"), con reportlab como la Hoja de
Vida de equipos (`equipment/hoja_de_vida.py`). Sin recursos externos.

Contenido: proyecto y contrato; por línea, contratado, anterior, esta acta, acumulado y saldo,
en cantidad y en valor; cargos, amortización, retención, descuentos y neto; las
justificaciones de lo que difiere de lo validado; y las firmas.
"""

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

from suno.core.templatetags.money_tags import money, qty

from .queries import CertificateDocument

__all__ = ["certificate_pdf"]

PAGE = landscape(A4)
INK = colors.HexColor("#171717")
ACCENT = colors.HexColor("#8C2633")
GRID = colors.HexColor("#CCCCCC")
HEAD = colors.HexColor("#F0EEE9")


def _font() -> None:
    if "SunoReport" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont("SunoReport", str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"))
        )


def certificate_pdf(document: CertificateDocument, *, generated_on: date) -> bytes:
    _font()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "ActBody", parent=styles["BodyText"], fontName="SunoReport", fontSize=8, leading=10.5
    )
    heading = ParagraphStyle(
        "ActHeading", parent=body, fontSize=15, leading=20, spaceAfter=4, textColor=ACCENT
    )
    section = ParagraphStyle(
        "ActSection", parent=body, fontSize=10.5, leading=14, spaceBefore=10, spaceAfter=4,
        textColor=INK, keepWithNext=True,
    )  # fmt: skip

    def cell(text: str) -> Paragraph:
        return Paragraph(escape(text), body)

    def table(
        rows: list[list[str]],
        widths: list[float],
        *,
        header: bool = True,
        heights: list[float | None] | None = None,
    ) -> Table:
        built = Table(
            [[cell(c) for c in row] for row in rows],
            colWidths=widths,
            rowHeights=heights,
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
    certificate, contract = document.certificate, document.contract
    kind = "liquidación" if certificate.is_settlement else "avance"
    story: list[Any] = [
        Paragraph(f"Acta de {kind} N.º {certificate.number}", heading),
        table(
            [
                ["Proyecto", document.project.name],
                ["Contrato", f"{contract.number} · {contract.get_kind_display()}"],
                ["Contratista", contract.contractor],
                ["Objeto", contract.purpose],
                [
                    "Periodo",
                    f"{certificate.period_start:%d/%m/%Y} – {certificate.period_end:%d/%m/%Y}",
                ],
            ],
            [width * 0.18, width * 0.82],
            header=False,
        ),
        Paragraph("Cantidades", section),
        table(
            [
                [
                    "Ítem",
                    "Unidad",
                    "Precio",
                    "Contratado",
                    "Anterior",
                    "Esta acta",
                    "Acumulado",
                    "Saldo",
                ],
                *[
                    [
                        line.label,
                        line.unit,
                        money(line.unit_price),
                        qty(line.contracted),
                        qty(line.previous),
                        qty(line.this),
                        qty(line.accumulated),
                        qty(line.balance),
                    ]
                    for line in document.lines
                ],
            ],
            [width * 0.28, width * 0.06, *[width * 0.11] * 6],
        ),  # fmt: skip
        Paragraph("Valores", section),
        table(
            [
                ["Ítem", "Contratado", "Anterior", "Esta acta", "Acumulado", "Saldo"],
                *[
                    [
                        line.label,
                        money(line.contracted_value),
                        money(line.previous_value),
                        money(line.amount),
                        money(line.accumulated_value),
                        money(line.balance_value),
                    ]
                    for line in document.lines
                ],
            ],
            [width * 0.30, *[width * 0.14] * 5],
        ),  # fmt: skip
        Paragraph("Liquidación del acta", section),
        table(
            [
                ["Bruto directo", money(certificate.direct)],
                *[
                    [f"{c['name']} ({qty(c['pct'])} %)", money(c["amount"])]
                    for c in document.charges
                ],
                ["Bruto (costo del periodo)", money(certificate.gross)],
                ["Amortización del anticipo", money(-certificate.amortization)],
                ["Retención de garantía", money(-certificate.retention)],
                *[
                    [f"{d['name']} ({qty(d['pct'])} %)", money(-Decimal(d["amount"]))]
                    for d in document.deductions
                ],
                *(
                    [["Devolución de la retención", money(certificate.retention_returned)]]
                    if certificate.retention_returned
                    else []
                ),
                ["Neto a pagar", money(certificate.net)],
            ],
            [width * 0.5, width * 0.2],
            header=False,
        ),  # fmt: skip
    ]
    differing = [line for line in document.lines if line.differs]
    if differing:
        story.append(Paragraph("Ajustes frente a lo validado en campo", section))
        story += [
            Paragraph(
                escape(
                    f"{line.label}: propuesta {qty(line.proposed)}, acta {qty(line.this)}. "
                    f"{line.justification}"
                ),
                body,
            )
            for line in differing
        ]
    story += [
        Spacer(1, 28),
        table(
            [
                ["", "", ""],
                ["Contratista", "Residente o interventoría", "Aprobó"],
                [contract.contractor, "", ""],
            ],
            [width / 3] * 3,
            header=False,
            heights=[40, None, None],
        ),
        Spacer(1, 6),
        Paragraph(f"Generado por SUNO el {generated_on:%d/%m/%Y}.", body),
    ]

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("SunoReport", 8)
        canvas.drawString(36, 20, f"SUNO · {contract.number} · Acta {certificate.number}")
        canvas.drawRightString(PAGE[0] - 36, 20, str(doc.page))
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=PAGE,
        leftMargin=36,
        rightMargin=36,
        topMargin=30,
        bottomMargin=36,
        title=f"Acta {certificate.number} {contract.number}",
        author="SUNO",
    ).build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
