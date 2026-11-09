"""Ficha ampliada del equipo (SINCO M&E §2): departamento, medidores y campos dinámicos.

Servicios autorizados y sin dependencia de la capa web. Cada operación de escritura
exige una capacidad de la sección Equipos, queda auditada y se valida contra la
organización del contexto.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from suno.core import audit
from suno.core.access import Capability, Section
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    Equipment,
    EquipmentFieldDefinition,
    EquipmentFieldType,
    EquipmentFieldValue,
    EquipmentMeterReading,
    EquipmentMeterUnit,
    MachineryDepartment,
)
from suno.core.scoping import scoped_qs
from suno.equipment.services import _require, _require_for_equipment, get_visible_equipment

__all__ = [
    "FieldAlert",
    "MeterSnapshot",
    "create_department",
    "define_field",
    "equipment_alerts",
    "field_values_for",
    "latest_meter_readings",
    "record_meter_reading",
    "set_field_value",
]


@transaction.atomic
def create_department(
    ctx: RequestContext, *, cost_center_code: str, name: str
) -> MachineryDepartment:
    _require(ctx, Capability.CREATE)
    department = MachineryDepartment(
        org_id=ctx.org_id, cost_center_code=cost_center_code.strip(), name=name.strip()
    )
    department.full_clean()
    department.save()
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_DEPARTMENT_CREATED,
        target=f"machinery-department:{department.pk}",
        metadata={"cost_center_code": department.cost_center_code},
    )
    return department


# ---------------------------------------------------------------------------
# Medidores
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeterSnapshot:
    meter_unit: str
    label: str
    value: Decimal
    read_at: datetime


def _configured_units(equipment: Equipment) -> list[str]:
    units = []
    if equipment.primary_meter_unit:
        units.append(equipment.primary_meter_unit)
    if equipment.secondary_meter_unit:
        units.append(equipment.secondary_meter_unit)
    return units


def latest_meter_readings(ctx: RequestContext, equipment: Equipment) -> list[MeterSnapshot]:
    """Última lectura de cada medidor configurado en la ficha (principal primero)."""

    snapshots: list[MeterSnapshot] = []
    for unit in _configured_units(equipment):
        row = (
            scoped_qs(ctx, EquipmentMeterReading)
            .filter(equipment=equipment, meter_unit=unit)
            .order_by("-read_at", "-created_at")
            .first()
        )
        if row is not None:
            snapshots.append(
                MeterSnapshot(
                    meter_unit=unit,
                    label=EquipmentMeterUnit(unit).label,
                    value=row.value,
                    read_at=row.read_at,
                )
            )
    return snapshots


@transaction.atomic
def record_meter_reading(
    ctx: RequestContext,
    *,
    equipment_id: UUID,
    meter_unit: str,
    value: Decimal,
    read_at: datetime | None = None,
    source: str = "manual",
    notes: str = "",
    reading_id: UUID | None = None,
) -> EquipmentMeterReading:
    """Registra una lectura. Los medidores solo avanzan: una lectura menor que la última
    del mismo medidor se rechaza (un cambio de medidor se documenta aparte).

    `reading_id` hace la operación idempotente: el móvil lo genera y, si reenvía la misma
    lectura tras un corte de red, recibe la ya registrada en vez de un duplicado.
    """

    equipment = get_visible_equipment(ctx, equipment_id)
    # El operador (rol con `update`) puede reportar lecturas del equipo que tiene asignado.
    _require_for_equipment(ctx, Capability.UPDATE, equipment)
    if reading_id is not None:
        existing = scoped_qs(ctx, EquipmentMeterReading).filter(pk=reading_id).first()
        if existing is not None:
            if existing.equipment_id != equipment.pk:
                raise ValidationError("El identificador de la lectura ya pertenece a otro equipo.")
            return existing
    if meter_unit not in _configured_units(equipment):
        raise ValidationError("El equipo no tiene configurado ese medidor.")
    if value < 0:
        raise ValidationError("La lectura no puede ser negativa.")
    read_at = read_at or timezone.now()
    if read_at > timezone.now() + timedelta(minutes=5):
        raise ValidationError("La fecha de la lectura no puede estar en el futuro.")
    previous = (
        scoped_qs(ctx, EquipmentMeterReading)
        .filter(equipment=equipment, meter_unit=meter_unit, read_at__lte=read_at)
        .order_by("-read_at", "-created_at")
        .first()
    )
    if previous is not None and value < previous.value:
        raise ValidationError(
            f"La lectura ({value}) es menor que la anterior ({previous.value}); "
            "los medidores no retroceden."
        )
    following = (
        scoped_qs(ctx, EquipmentMeterReading)
        .filter(equipment=equipment, meter_unit=meter_unit, read_at__gt=read_at)
        .order_by("read_at", "created_at")
        .first()
    )
    if following is not None and value > following.value:
        raise ValidationError(
            f"La lectura ({value}) es mayor que una posterior ({following.value})."
        )
    reading = EquipmentMeterReading(
        **({"pk": reading_id} if reading_id else {}),
        org_id=ctx.org_id,
        equipment=equipment,
        meter_unit=meter_unit,
        value=value,
        read_at=read_at,
        source=source,
        notes=notes.strip(),
        recorded_by_id=ctx.user_id,
    )
    reading.full_clean()
    reading.save()
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_METER_READ,
        target=f"equipment-meter-reading:{reading.pk}",
        metadata={"equipment": str(equipment.pk), "unit": meter_unit, "value": str(value)},
    )
    return reading


# ---------------------------------------------------------------------------
# Campos dinámicos con vigencia
# ---------------------------------------------------------------------------


@transaction.atomic
def define_field(
    ctx: RequestContext,
    *,
    name: str,
    data_type: str,
    alert_days: int | None = None,
    required: bool = False,
) -> EquipmentFieldDefinition:
    _require(ctx, Capability.CREATE)
    definition = EquipmentFieldDefinition(
        org_id=ctx.org_id,
        name=name.strip(),
        data_type=data_type,
        alert_days=alert_days,
        required=required,
    )
    definition.full_clean()
    definition.save()
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_FIELD_DEFINED,
        target=f"equipment-field:{definition.pk}",
        metadata={"name": definition.name, "type": definition.data_type},
    )
    return definition


def _coerce(definition: EquipmentFieldDefinition, raw: Any) -> dict[str, Any]:
    empty = raw is None or (isinstance(raw, str) and not raw.strip())
    if empty:
        if definition.required:
            raise ValidationError(f"El campo «{definition.name}» es obligatorio.")
        return {"number_value": None, "date_value": None, "text_value": ""}
    if definition.data_type == EquipmentFieldType.NUMBER:
        try:
            return {"number_value": Decimal(str(raw)), "date_value": None, "text_value": ""}
        except ArithmeticError as exc:
            raise ValidationError(f"«{definition.name}» debe ser un número.") from exc
    if definition.data_type == EquipmentFieldType.DATE:
        if isinstance(raw, datetime):
            raw = raw.date()
        if not isinstance(raw, date):
            try:
                raw = date.fromisoformat(str(raw))
            except ValueError as exc:
                raise ValidationError(f"«{definition.name}» debe ser una fecha válida.") from exc
        return {"number_value": None, "date_value": raw, "text_value": ""}
    return {"number_value": None, "date_value": None, "text_value": str(raw).strip()}


@transaction.atomic
def set_field_value(
    ctx: RequestContext, *, equipment_id: UUID, definition_id: UUID, value: Any
) -> EquipmentFieldValue:
    equipment = get_visible_equipment(ctx, equipment_id)
    _require_for_equipment(ctx, Capability.UPDATE, equipment)
    try:
        definition = scoped_qs(ctx, EquipmentFieldDefinition).get(pk=definition_id, active=True)
    except EquipmentFieldDefinition.DoesNotExist as exc:
        raise ValidationError("El campo no existe o está inactivo.") from exc
    parts = _coerce(definition, value)
    row, _ = scoped_qs(ctx, EquipmentFieldValue).update_or_create(
        equipment=equipment,
        definition=definition,
        defaults={"org_id": ctx.org_id, "updated_by_id": ctx.user_id, **parts},
    )
    row.full_clean()
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_FIELD_SET,
        target=f"equipment-field-value:{row.pk}",
        metadata={"equipment": str(equipment.pk), "field": definition.name},
    )
    return row


def field_values_for(ctx: RequestContext, equipment: Equipment) -> list[EquipmentFieldValue]:
    return list(
        scoped_qs(ctx, EquipmentFieldValue)
        .filter(equipment=equipment, definition__active=True)
        .select_related("definition")
        .order_by("definition__name")
    )


@dataclass(frozen=True)
class FieldAlert:
    """Vigencia próxima o vencida de un campo de fecha o de un documento."""

    source: str  # "field" | "document"
    name: str
    expires_on: date
    days_left: int  # negativo = vencido

    @property
    def expired(self) -> bool:
        return self.days_left < 0


def equipment_alerts(
    ctx: RequestContext,
    equipment: Equipment,
    *,
    today: date | None = None,
    default_window_days: int = 30,
) -> list[FieldAlert]:
    """Alertas de vigencia del equipo: campos de fecha con aviso y documentos que vencen.

    Un campo de fecha alerta cuando faltan `alert_days` o menos; un documento, dentro de
    `default_window_days`. Los vencidos siempre alertan. Orden: el más urgente primero.
    """

    today = today or date.today()
    alerts: list[FieldAlert] = []
    for value in field_values_for(ctx, equipment):
        definition = value.definition
        if definition.data_type != EquipmentFieldType.DATE or value.date_value is None:
            continue
        window = definition.alert_days if definition.alert_days is not None else 0
        days_left = (value.date_value - today).days
        if days_left <= window:
            alerts.append(FieldAlert("field", definition.name, value.date_value, days_left))
    for document in equipment.documents.filter(expires_on__isnull=False):
        days_left = (document.expires_on - today).days
        if days_left <= default_window_days:
            alerts.append(FieldAlert("document", document.name, document.expires_on, days_left))
    alerts.sort(key=lambda alert: (alert.days_left, alert.name))
    return alerts


def can_read_equipment_section(ctx: RequestContext) -> bool:
    return ctx.has_section_access(Section.EQUIPMENT)
