"""Reporte de uso desde campo (M&E): el operador registra su propia jornada con el equipo.

A diferencia de la programación (web, requiere `schedule`), el reporte lo hace el operador
sobre un equipo que tiene asignado y es siempre suyo: queda como completado, sin solaparse
con otro uso del equipo o del operador, y alimenta las lecturas de medidor del equipo.
Es idempotente por `id` (el móvil reintenta).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from suno.core.access import Capability
from suno.core.context import RequestContext
from suno.core.models import (
    EquipmentOperatorAssignment,
    EquipmentProject,
    EquipmentStatus,
    EquipmentUsage,
    EquipmentUsageStatus,
    Project,
)
from suno.core.scoping import scoped_qs
from suno.equipment.datasheet import record_meter_reading
from suno.equipment.services import (
    _create_usage,
    _require_for_equipment,
    get_visible_equipment,
)

__all__ = ["recent_usage", "report_equipment_usage", "usage_projects"]


def usage_projects(
    ctx: RequestContext, equipment_ids: list[UUID], *, on: date | None = None
) -> dict[UUID, list[Project]]:
    """Proyectos en los que quien llama puede reportar uso de cada equipo hoy.

    Son los de sus asignaciones vigentes que además tienen el equipo vinculado (la misma
    regla que valida el reporte). Un equipo repartido entre proyectos ofrece varios.
    """

    day = on or date.today()
    linked = {
        (row.equipment_id, row.project_id)
        for row in scoped_qs(ctx, EquipmentProject)
        .filter(equipment_id__in=equipment_ids, started_on__lte=day)
        .filter(Q(ended_on__isnull=True) | Q(ended_on__gte=day))
    }
    result: dict[UUID, list[Project]] = {}
    assignments = (
        scoped_qs(ctx, EquipmentOperatorAssignment)
        .filter(
            operator_id=ctx.user_id,
            equipment_id__in=equipment_ids,
            valid_from__lte=day,
        )
        .select_related("project")
        .order_by("project__name")
    )
    for row in assignments:
        if row.valid_until is not None and row.valid_until < day:
            continue
        if (row.equipment_id, row.project_id) in linked:
            bucket = result.setdefault(row.equipment_id, [])
            if all(project.pk != row.project_id for project in bucket):
                bucket.append(row.project)
    return result


def recent_usage(
    ctx: RequestContext, equipment_ids: list[UUID], *, per_equipment: int = 5
) -> dict[UUID, list[EquipmentUsage]]:
    """Los últimos usos propios de cada equipo, el más reciente primero."""

    rows = (
        scoped_qs(ctx, EquipmentUsage)
        .filter(equipment_id__in=equipment_ids, operator_id=ctx.user_id)
        .exclude(status=EquipmentUsageStatus.CANCELLED)
        .select_related("project")
        .order_by("-usage_date", "-start_time")
    )
    grouped: dict[UUID, list[EquipmentUsage]] = {}
    for row in rows:
        bucket = grouped.setdefault(row.equipment_id, [])
        if len(bucket) < per_equipment:
            bucket.append(row)
    return grouped


@transaction.atomic
def report_equipment_usage(
    ctx: RequestContext,
    *,
    usage_id: UUID,
    equipment_id: UUID,
    project_id: UUID,
    usage_date: date,
    start_time: time,
    end_time: time,
    activity: str,
    location: str = "",
    start_meter: Decimal | None = None,
    end_meter: Decimal | None = None,
    notes: str = "",
) -> EquipmentUsage:
    """Registra la jornada de quien llama con un equipo asignado, como completada."""

    equipment = get_visible_equipment(ctx, equipment_id)
    _require_for_equipment(ctx, Capability.CREATE, equipment)

    existing = scoped_qs(ctx, EquipmentUsage).filter(pk=usage_id).first()
    if existing is not None:
        if existing.equipment_id != equipment.pk or existing.operator_id != ctx.user_id:
            raise ValidationError("El identificador del reporte ya está en uso.")
        return existing

    if equipment.status == EquipmentStatus.RETIRED:
        raise ValidationError("El equipo está retirado.")
    today = timezone.localdate()
    if usage_date > today:
        raise ValidationError("La fecha del uso no puede estar en el futuro.")
    if not activity.strip():
        raise ValidationError("Describe la actividad realizada.")
    if end_time <= start_time:
        raise ValidationError("La hora final debe ser posterior a la hora de inicio.")
    stamp = timezone.make_aware(datetime.combine(usage_date, end_time))
    if stamp > timezone.now() + timedelta(minutes=5):
        raise ValidationError("La hora final no puede estar en el futuro.")

    unit = ""
    if equipment.control_scheme == "meter" and equipment.primary_meter_unit:
        if start_meter is None or end_meter is None:
            raise ValidationError("Las lecturas inicial y final del medidor son obligatorias.")
        if end_meter < start_meter:
            raise ValidationError("La lectura final no puede ser menor que la inicial.")
        unit = equipment.primary_meter_unit

    usage = _create_usage(
        ctx,
        usage_id=usage_id,
        equipment_id=equipment.pk,
        project_id=project_id,
        operator_id=ctx.user_id,
        usage_date=usage_date,
        start_time=start_time,
        end_time=end_time,
        activity=activity,
        status="completed",
        location=location.strip(),
        meter_unit=unit or "hours",
        start_meter=start_meter if unit else None,
        end_meter=end_meter if unit else None,
        notes=notes.strip(),
    )
    if unit and end_meter is not None:
        # La lectura final del uso es la última lectura conocida del medidor.
        record_meter_reading(
            ctx,
            equipment_id=equipment.pk,
            meter_unit=unit,
            value=end_meter,
            read_at=stamp,
            source="usage",
            notes="Reporte de uso",
        )
    return usage
