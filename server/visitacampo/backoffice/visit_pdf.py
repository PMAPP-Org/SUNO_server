"""Printable per-visit report. No external resources, scripts or attachment URLs."""

from __future__ import annotations

import io
import json
import textwrap
from html import escape
from pathlib import Path
from typing import Any

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from visitacampo.core.models import Visit
from visitacampo.forms.definition import TemplateDefinitionError, validate_definition


def answer_lines(
    fields: list[Any], answers: dict[str, Any], prefix: str = ""
) -> list[tuple[str, Any]]:
    lines = []
    declared = set()
    for field in fields:
        declared.add(field.key)
        if field.key not in answers:
            continue
        value = answers[field.key]
        label = prefix + (field.label.get("es") or next(iter(field.label.values())))
        if field.type == "repeat" and isinstance(value, list):
            for i, row in enumerate(value):
                if isinstance(row, dict):
                    lines.extend(answer_lines(field.fields, row, f"{label} / {i + 1} / "))
        else:
            if field.type in ("single_choice", "multi_choice"):
                options = {
                    o.value: o.label.get("es") or next(iter(o.label.values()))
                    for o in field.options
                }
                value = (
                    [options.get(v, v) for v in value]
                    if isinstance(value, list)
                    else options.get(value, value)
                )
            lines.append((label, value))
    lines.extend((prefix + key, value) for key, value in answers.items() if key not in declared)
    return lines


def visit_pdf(visit: Visit) -> bytes:
    if "SunoReport" not in pdfmetrics.getRegisteredFontNames():
        # ReportLab ships Bitstream Vera and its redistribution license.
        pdfmetrics.registerFont(
            TTFont("SunoReport", str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"))
        )
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontName="SunoReport",
        fontSize=9,
        leading=14,
        wordWrap="CJK",
    )
    heading = ParagraphStyle(
        "ReportHeading",
        parent=body,
        fontSize=16,
        leading=21,
        spaceAfter=14,
        textColor=colors.HexColor("#8C2633"),
    )
    label_style = ParagraphStyle(
        "ReportLabel",
        parent=body,
        spaceBefore=10,
        textColor=colors.HexColor("#555555"),
        keepWithNext=True,
    )
    story: list[Any] = [Paragraph("SUNO · Informe de visita", heading)]

    def add(label: str, value: Any) -> None:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        story.append(Paragraph(escape(label), label_style))
        # Chunk large text so a single paragraph cannot exceed a whole page.
        for paragraph in text.splitlines() or [""]:
            for chunk in textwrap.wrap(paragraph, width=1800, break_on_hyphens=False) or [""]:
                story.append(Paragraph(escape(chunk), body))

    add("Identificador", str(visit.pk))
    add(
        "Formulario",
        f"{visit.template_version.name or 'Visita'} · "
        f"versión {visit.template_version.version_number}",
    )
    add("Capturada (fecha y zona horaria)", visit.captured_at.isoformat())
    add("Autor", visit.created_by.username)
    add(
        "Ubicación · EPSG:4326",
        f"Latitud: {visit.lat}; longitud: {visit.lon}; precisión: {visit.accuracy_m} m",
    )
    try:
        fields = validate_definition(visit.template_version.definition).fields
    except TemplateDefinitionError:
        fields = []
    for label, value in answer_lines(fields, visit.answers or {}):
        add(label, value)
    story.append(Spacer(1, 14))
    story.append(
        Paragraph(
            "Las referencias de fotos y firmas identifican los adjuntos de la visita. "
            "Este informe no incorpora sus archivos.",
            body,
        )
    )

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("SunoReport", 8)
        canvas.drawString(42, 24, f"SUNO · {visit.pk}")
        canvas.drawRightString(A4[0] - 42, 24, str(doc.page))
        canvas.restoreState()

    SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=42,
        rightMargin=42,
        topMargin=36,
        bottomMargin=44,
        title="Informe de visita SUNO",
        author="SUNO",
    ).build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
