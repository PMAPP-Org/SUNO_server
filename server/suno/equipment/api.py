"""Router de `/v1/equipment` (móvil, fase 1). Solo traducción: contexto, servicio, respuesta."""

from __future__ import annotations

import uuid

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpResponse
from ninja import Query, Router
from ninja.errors import HttpError

from suno.core.access import Section
from suno.core.context import get_request_context
from suno.equipment import datasheet, field_equipment, hoja_de_vida, inspections
from suno.equipment.schemas import (
    FieldEquipmentList,
    InspectionIn,
    InspectionOut,
    MeterReadingIn,
    MeterReadingOut,
    UsageReportIn,
    UsageReportOut,
)
from suno.equipment.services import get_visible_equipment
from suno.equipment.usage_reports import report_equipment_usage

__all__ = ["router"]

router = Router(tags=["equipment"])


@router.get(
    "/equipment",
    response={200: FieldEquipmentList},
    summary="Equipos que quien llama puede ver",
    operation_id="equipmentList",
)
def list_equipment(request, version: str | None = Query(None, max_length=64)):
    """`GET /v1/equipment?version=`: la lista, o "sin cambios" si `version` coincide.

    Sin acceso a la sección Equipos la lista va vacía: la sección no existe para ese usuario.
    """
    ctx = get_request_context(request)
    items = (
        field_equipment.field_equipment(ctx) if ctx.has_section_access(Section.EQUIPMENT) else []
    )
    current = field_equipment.list_version(items)
    if version == current:
        return {"version": current, "changed": False, "items": []}
    return {"version": current, "changed": True, "items": items}


@router.post(
    "/equipment/{equipment_id}/meter-readings",
    response={200: MeterReadingOut},
    summary="Registra una lectura de medidor (idempotente por `id`)",
    operation_id="equipmentMeterReadingCreate",
)
def create_meter_reading(request, equipment_id: uuid.UUID, payload: MeterReadingIn):
    """`POST /v1/equipment/{id}/meter-readings`.

    `404` si el equipo no es visible para quien llama; `403` sin permiso de escritura;
    `422` si la lectura retrocede, es futura o el medidor no está configurado.
    """
    ctx = get_request_context(request)
    try:
        reading = datasheet.record_meter_reading(
            ctx,
            equipment_id=equipment_id,
            meter_unit=payload.meter_unit,
            value=payload.value,
            read_at=payload.read_at,
            source="manual",
            notes=payload.notes,
            reading_id=payload.id,
        )
    except Http404 as exc:
        raise HttpError(404, "Equipment not found.") from exc
    except PermissionDenied as exc:
        raise HttpError(403, "Not allowed to record readings for this equipment.") from exc
    except DjangoValidationError as exc:
        raise HttpError(422, "; ".join(exc.messages)) from exc
    return {
        "id": reading.pk,
        "meter_unit": reading.meter_unit,
        "value": float(reading.value),
        "read_at": reading.read_at,
    }


@router.post(
    "/equipment/{equipment_id}/inspections",
    response={200: InspectionOut},
    summary="Envía un preoperacional (idempotente por `id`)",
    operation_id="equipmentInspectionCreate",
)
def create_inspection(request, equipment_id: uuid.UUID, payload: InspectionIn):
    """`POST /v1/equipment/{id}/inspections`.

    Todo o nada: una lista incompleta, un medidor que retrocede o un ítem fallido sin
    descripción responden `422` y no se guarda nada. `404` si el equipo no es visible;
    `403` sin permiso para registrar inspecciones.
    """
    ctx = get_request_context(request)
    try:
        inspection = inspections.submit_inspection(
            ctx,
            inspection_id=payload.id,
            equipment_id=equipment_id,
            captured_at=payload.captured_at,
            answers=[
                inspections.AnswerIn(item_id=item.item_id, ok=item.ok, note=item.note)
                for item in payload.items
            ],
            meter_unit=payload.meter_unit or "",
            meter_value=payload.meter_value,
            notes=payload.notes,
        )
    except Http404 as exc:
        raise HttpError(404, "Equipment not found.") from exc
    except PermissionDenied as exc:
        raise HttpError(403, "Not allowed to submit inspections for this equipment.") from exc
    except DjangoValidationError as exc:
        raise HttpError(422, "; ".join(exc.messages)) from exc
    return {"id": inspection.pk, "status": inspection.status, "has_faults": inspection.has_faults}


@router.get(
    "/equipment/{equipment_id}/life-sheet",
    response={200: None},
    summary="Hoja de Vida del equipo en PDF",
    operation_id="equipmentLifeSheet",
)
def life_sheet(request, equipment_id: uuid.UUID):
    """`GET /v1/equipment/{id}/life-sheet`: el PDF oficial del equipo.

    Los datos financieros solo van si quien llama puede verlos. `404` si el equipo no es
    visible para quien llama.
    """
    ctx = get_request_context(request)
    try:
        equipment = get_visible_equipment(ctx, equipment_id)
    except Http404 as exc:
        raise HttpError(404, "Equipment not found.") from exc
    pdf = hoja_de_vida.hoja_de_vida_pdf(hoja_de_vida.hoja_de_vida_data(ctx, equipment))
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Cache-Control"] = "private, no-store"
    return response


@router.post(
    "/equipment/{equipment_id}/usage-reports",
    response={200: UsageReportOut},
    summary="Reporta el uso de un equipo asignado (idempotente por `id`)",
    operation_id="equipmentUsageReportCreate",
)
def create_usage_report(request, equipment_id: uuid.UUID, payload: UsageReportIn):
    """`POST /v1/equipment/{id}/usage-reports`.

    El operador reporta su propia jornada con un equipo que tiene asignado en un proyecto
    donde el equipo está vinculado. Queda completada; no se solapa con otro uso del equipo o
    del operador; si el equipo se controla por medidor exige las lecturas inicial y final
    (la final pasa a ser la última lectura del equipo). `404` si el equipo no es visible;
    `403` sin permiso; `422` ante cualquier otra violación.
    """
    ctx = get_request_context(request)
    try:
        usage = report_equipment_usage(
            ctx,
            usage_id=payload.id,
            equipment_id=equipment_id,
            project_id=payload.project_id,
            usage_date=payload.usage_date,
            start_time=payload.start_time,
            end_time=payload.end_time,
            activity=payload.activity,
            location=payload.location,
            start_meter=payload.start_meter,
            end_meter=payload.end_meter,
            notes=payload.notes,
        )
    except Http404 as exc:
        raise HttpError(404, "Equipment not found.") from exc
    except PermissionDenied as exc:
        raise HttpError(403, "Not allowed to report usage for this equipment.") from exc
    except DjangoValidationError as exc:
        raise HttpError(422, "; ".join(exc.messages)) from exc
    return {
        "id": usage.pk,
        "hours": float(usage.billable_hours) if usage.billable_hours is not None else None,
        "status": usage.status,
    }
