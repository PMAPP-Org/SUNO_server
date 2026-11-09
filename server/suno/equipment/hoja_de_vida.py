"""Hoja de Vida del equipo (M&E fase 3): datos reunidos y PDF.

`hoja_de_vida_data` reúne todo lo que muestra el documento (la misma fuente sirve a la
web y al PDF). Los datos financieros solo se incluyen si quien consulta puede verlos.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from suno.core.context import RequestContext
from suno.core.models import Equipment, EquipmentFault, FaultStatus
from suno.core.scoping import scoped_qs
from suno.equipment.datasheet import (
    FieldAlert,
    equipment_alerts,
    field_values_for,
    latest_meter_readings,
)
from suno.equipment.maintenance import (
    PlanStatus,
    plan_status,
    preventive_history,
    visible_plans,
)
from suno.equipment.services import can_view_costs

__all__ = ["HojaDeVida", "hoja_de_vida_data", "hoja_de_vida_pdf"]

NONE = "—"


@dataclass(frozen=True)
class HojaDeVida:
    equipment: Equipment
    generated_on: date
    #: Pares (etiqueta, valor) del encabezado.
    header: list[tuple[str, str]]
    #: Datos financieros; vacío si quien consulta no puede verlos.
    financial: list[tuple[str, str]]
    #: Campos adicionales y documentos con su vigencia: (nombre, valor, estado).
    validity: list[tuple[str, str, str]]
    meters: list[tuple[str, str]]
    #: Matriz preventiva: (rutina, frecuencia, próximo, estado).
    plans: list[tuple[str, str, str, str]]
    #: Últimos preventivos: (ciclo, fecha, orden, mecánicos).
    history: list[tuple[str, str, str, str]]
    open_faults: int
    alerts: list[FieldAlert] = field(default_factory=list)


def _money(value: Decimal | None) -> str:
    return NONE if value is None else f"$ {value:,.2f}"


def _date(value: date | datetime | None) -> str:
    return NONE if value is None else value.strftime("%d/%m/%Y")


def _plan_row(status: PlanStatus) -> tuple[str, str, str, str]:
    plan = status.plan
    if plan.kind == "meter":
        every = f"{plan.interval_value:g} {_unit(plan.meter_unit)}"
        due = NONE if status.due_value is None else f"{status.due_value:g} {_unit(plan.meter_unit)}"
    else:
        every = f"{plan.interval_days} días"
        due = _date(status.due_on)
    state = {
        "ok": "Al día",
        "due_soon": "Próximo",
        "overdue": "Vencido",
        "unknown": "Sin lecturas",
    }[status.state]
    return plan.name, every, due, state


def _unit(unit: str) -> str:
    return {"hours": "h", "kilometers": "km"}.get(unit, unit)


def hoja_de_vida_data(
    ctx: RequestContext, equipment: Equipment, *, today: date | None = None
) -> HojaDeVida:
    today = today or date.today()
    department = equipment.department
    header = [
        ("Código interno", equipment.code),
        ("Nombre", equipment.name),
        ("Tipo", equipment.equipment_type.name),
        ("Fabricante", equipment.manufacturer or NONE),
        ("Modelo", equipment.model or NONE),
        ("Serie / chasis", equipment.serial_number or NONE),
        ("Placa", equipment.license_plate or NONE),
        ("Año", str(equipment.manufacture_year or NONE)),
        ("Propiedad", equipment.get_ownership_display()),
        ("Estado", equipment.get_status_display()),
        ("Departamento de maquinaria", str(department) if department else NONE),
    ]
    financial: list[tuple[str, str]] = []
    if can_view_costs(ctx):
        financial = [
            ("Valor de adquisición", _money(equipment.acquisition_value)),
            ("Fecha de adquisición", _date(equipment.acquisition_date)),
            ("Depreciación acumulada", _money(equipment.accumulated_depreciation)),
        ]
    alerts = equipment_alerts(ctx, equipment, today=today)
    alert_by_name = {alert.name: alert for alert in alerts}

    def state_of(name: str, expires: date | None) -> str:
        if expires is None:
            return NONE
        alert = alert_by_name.get(name)
        if alert is None:
            return "Vigente"
        return "Vencido" if alert.expired else f"Vence en {alert.days_left} días"

    validity: list[tuple[str, str, str]] = []
    for value in field_values_for(ctx, equipment):
        definition = value.definition
        if value.date_value is not None:
            validity.append(
                (
                    definition.name,
                    _date(value.date_value),
                    state_of(definition.name, value.date_value),
                )
            )
        elif value.number_value is not None:
            validity.append((definition.name, f"{value.number_value:g}", NONE))
        else:
            validity.append((definition.name, value.text_value or NONE, NONE))
    for document in equipment.documents.all():
        validity.append(
            (
                document.name,
                _date(document.expires_on),
                state_of(document.name, document.expires_on),
            )
        )
    meters = [
        (f"{snapshot.label}", f"{snapshot.value:g} ({_date(snapshot.read_at)})")
        for snapshot in latest_meter_readings(ctx, equipment)
    ]
    plans = [
        _plan_row(plan_status(ctx, plan, today=today))
        for plan in visible_plans(ctx).filter(equipment=equipment, active=True)
    ]
    history = [
        (
            order.plan.name if order.plan else "Preventivo",
            _date(order.closed_at),
            order.code,
            ", ".join(
                link.mechanic.display_name or link.mechanic.username
                for link in order.mechanics.all()
            )
            or NONE,
        )
        for order in preventive_history(ctx, equipment, limit=5)
    ]
    open_faults = (
        scoped_qs(ctx, EquipmentFault).filter(equipment=equipment, status=FaultStatus.OPEN).count()
    )
    return HojaDeVida(
        equipment=equipment,
        generated_on=today,
        header=header,
        financial=financial,
        validity=validity,
        meters=meters,
        plans=plans,
        history=history,
        open_faults=open_faults,
        alerts=alerts,
    )


def hoja_de_vida_pdf(data: HojaDeVida) -> bytes:
    if "SunoReport" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont("SunoReport", str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"))
        )
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "HvBody", parent=styles["BodyText"], fontName="SunoReport", fontSize=8.5, leading=12
    )
    heading = ParagraphStyle(
        "HvHeading",
        parent=body,
        fontSize=16,
        leading=21,
        spaceAfter=4,
        textColor=colors.HexColor("#8C2633"),
    )
    section = ParagraphStyle(
        "HvSection",
        parent=body,
        fontSize=11,
        leading=15,
        spaceBefore=14,
        spaceAfter=4,
        textColor=colors.HexColor("#171717"),
        keepWithNext=True,
    )

    def cell(text: str) -> Paragraph:
        return Paragraph(escape(text), body)

    def table(rows: list[list[str]], widths: list[float], header: bool = True) -> Table:
        built = Table(
            [[cell(c) for c in row] for row in rows], colWidths=widths, repeatRows=int(header)
        )
        style = [
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        if header:
            style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0EEE9")))
        built.setStyle(TableStyle(style))
        return built

    width = A4[0] - 84
    equipment = data.equipment
    story: list[Any] = [
        Paragraph("SUNO · Hoja de Vida del equipo", heading),
        Paragraph(escape(f"{equipment.code} · {equipment.name}"), body),
        Spacer(1, 6),
        Paragraph("Identificación", section),
        table([[k, v] for k, v in data.header], [width * 0.35, width * 0.65], header=False),
    ]
    if data.financial:
        story += [
            Paragraph("Información financiera", section),
            table([[k, v] for k, v in data.financial], [width * 0.35, width * 0.65], header=False),
        ]
    story.append(Paragraph("Medidores", section))
    story.append(
        table([[k, v] for k, v in data.meters], [width * 0.35, width * 0.65], header=False)
        if data.meters
        else Paragraph("Sin lecturas registradas.", body)
    )
    story.append(Paragraph("Vigencia de documentos y campos adicionales", section))
    story.append(
        table(
            [["Concepto", "Valor o vencimiento", "Estado"], *[list(row) for row in data.validity]],
            [width * 0.4, width * 0.3, width * 0.3],
        )
        if data.validity
        else Paragraph("Sin documentos ni campos con vigencia.", body)
    )
    story.append(Paragraph("Matriz de mantenimiento preventivo", section))
    story.append(
        table(
            [["Rutina", "Cada", "Próximo", "Estado"], *[list(row) for row in data.plans]],
            [width * 0.35, width * 0.2, width * 0.25, width * 0.2],
        )
        if data.plans
        else Paragraph("Sin plan preventivo definido.", body)
    )
    story.append(Paragraph("Últimos 5 mantenimientos preventivos", section))
    story.append(
        table(
            [["Ciclo", "Fecha", "Orden", "Mecánicos"], *[list(row) for row in data.history]],
            [width * 0.3, width * 0.2, width * 0.15, width * 0.35],
        )
        if data.history
        else Paragraph("Todavía no hay preventivos ejecutados.", body)
    )
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Novedades abiertas: {data.open_faults}.", body))
    story.append(Paragraph(f"Generado el {data.generated_on.strftime('%d/%m/%Y')}.", body))

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("SunoReport", 8)
        canvas.drawString(42, 24, f"SUNO · {equipment.code}")
        canvas.drawRightString(A4[0] - 42, 24, str(doc.page))
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=42,
        rightMargin=42,
        topMargin=36,
        bottomMargin=44,
        title=f"Hoja de Vida {equipment.code}",
        author="SUNO",
    ).build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
