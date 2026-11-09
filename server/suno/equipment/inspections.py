"""Preoperacionales (M&E fase 2): lista de chequeo, envío, revisión y novedades.

Reglas que este módulo garantiza:
- La inspección es evidencia inmutable: se guarda tal como llegó (con la lista
  respondida, no solo ids) y la revisión solo añade su decisión.
- Reenviar la misma inspección (mismo `id`) devuelve la ya registrada.
- Cada ítem fallido abre una novedad; los críticos quedan marcados.
- Quien revisa no puede revisar su propia inspección (segregación de funciones).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.http import Http404
from django.utils import timezone

from suno.core import audit
from suno.core.access import Capability, Section, SectionRole
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    EquipmentFault,
    EquipmentInspection,
    EquipmentProject,
    EquipmentStatus,
    EquipmentType,
    FaultStatus,
    InspectionStatus,
    PreoperationalItem,
)
from suno.core.scoping import scoped_qs
from suno.equipment.datasheet import record_meter_reading
from suno.equipment.services import (
    _require,
    _require_for_equipment,
    get_visible_equipment,
    visible_equipment,
)

__all__ = [
    "AnswerIn",
    "checklist_for",
    "close_fault",
    "define_checklist_item",
    "is_operator",
    "recent_inspections",
    "retire_checklist_item",
    "review_inspection",
    "submit_inspection",
    "visible_faults",
    "visible_inspections",
]

REVIEW_DECISIONS = {
    "approve": (InspectionStatus.APPROVED, Capability.APPROVE, False),
    "reject": (InspectionStatus.REJECTED, Capability.REJECT, True),
    "request_correction": (InspectionStatus.CORRECTION_REQUESTED, Capability.REVIEW, True),
}


@dataclass(frozen=True)
class AnswerIn:
    item_id: UUID
    ok: bool
    note: str = ""


# ---------------------------------------------------------------------------
# Lista de chequeo
# ---------------------------------------------------------------------------


def checklist_for(ctx: RequestContext, equipment_type_id: UUID) -> list[PreoperationalItem]:
    return list(
        scoped_qs(ctx, PreoperationalItem)
        .filter(equipment_type_id=equipment_type_id, active=True)
        .order_by("position", "label")
    )


@transaction.atomic
def define_checklist_item(
    ctx: RequestContext,
    *,
    equipment_type_id: UUID,
    label: str,
    critical: bool = False,
) -> PreoperationalItem:
    _require(ctx, Capability.UPDATE)
    try:
        equipment_type = scoped_qs(ctx, EquipmentType).get(pk=equipment_type_id)
    except EquipmentType.DoesNotExist as exc:
        raise Http404 from exc
    label = label.strip()
    same = scoped_qs(ctx, PreoperationalItem).filter(
        equipment_type=equipment_type, label__iexact=label
    )
    existing = same.first()
    if existing is not None:
        if existing.active:
            raise ValidationError("Ese ítem ya está en la lista de chequeo.")
        # Un ítem retirado vuelve a la lista en vez de duplicarse.
        existing.active = True
        existing.critical = critical
        existing.save(update_fields=["active", "critical"])
        audit.record(
            ctx,
            action=AuditAction.EQUIPMENT_CHECKLIST_CHANGED,
            target=f"equipment-type:{equipment_type.pk}",
            metadata={"restored": existing.label, "critical": critical},
        )
        return existing
    last = (
        scoped_qs(ctx, PreoperationalItem)
        .filter(equipment_type=equipment_type)
        .order_by("-position")
        .first()
    )
    item = PreoperationalItem(
        org_id=ctx.org_id,
        equipment_type=equipment_type,
        label=label,
        critical=critical,
        position=(last.position + 1) if last else 0,
    )
    item.full_clean()
    item.save()
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_CHECKLIST_CHANGED,
        target=f"equipment-type:{equipment_type.pk}",
        metadata={"added": item.label, "critical": critical},
    )
    return item


@transaction.atomic
def retire_checklist_item(ctx: RequestContext, item_id: UUID) -> PreoperationalItem:
    """Retira un ítem de la lista. No se borra: las inspecciones ya guardadas lo citan."""

    _require(ctx, Capability.UPDATE)
    try:
        item = scoped_qs(ctx, PreoperationalItem).select_for_update().get(pk=item_id)
    except PreoperationalItem.DoesNotExist as exc:
        raise Http404 from exc
    item.active = False
    item.save(update_fields=["active"])
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_CHECKLIST_CHANGED,
        target=f"equipment-type:{item.equipment_type_id}",
        metadata={"retired": item.label},
    )
    return item


# ---------------------------------------------------------------------------
# Visibilidad
# ---------------------------------------------------------------------------


def is_operator(ctx: RequestContext) -> bool:
    access = ctx.access_for(Section.EQUIPMENT)
    return access is not None and access.role == SectionRole.EQUIPMENT_OPERATOR


def visible_inspections(ctx: RequestContext) -> QuerySet[EquipmentInspection]:
    """Inspecciones de los equipos visibles; un operador solo ve las suyas."""

    queryset = scoped_qs(ctx, EquipmentInspection).filter(equipment__in=visible_equipment(ctx))
    if is_operator(ctx):
        queryset = queryset.filter(operator_id=ctx.user_id)
    return queryset


def visible_faults(ctx: RequestContext) -> QuerySet[EquipmentFault]:
    queryset = scoped_qs(ctx, EquipmentFault).filter(equipment__in=visible_equipment(ctx))
    if is_operator(ctx):
        queryset = queryset.filter(inspection__operator_id=ctx.user_id)
    return queryset


def recent_inspections(
    ctx: RequestContext, equipment_ids: Iterable[UUID], *, per_equipment: int = 5
) -> dict[UUID, list[EquipmentInspection]]:
    """Las últimas inspecciones propias por equipo (para que el operador vea su estado)."""

    rows = (
        scoped_qs(ctx, EquipmentInspection)
        .filter(equipment_id__in=list(equipment_ids), operator_id=ctx.user_id)
        .order_by("-captured_at")
    )
    grouped: dict[UUID, list[EquipmentInspection]] = {}
    for row in rows:
        bucket = grouped.setdefault(row.equipment_id, [])
        if len(bucket) < per_equipment:
            bucket.append(row)
    return grouped


# ---------------------------------------------------------------------------
# Envío
# ---------------------------------------------------------------------------


@transaction.atomic
def submit_inspection(
    ctx: RequestContext,
    *,
    inspection_id: UUID,
    equipment_id: UUID,
    captured_at: datetime,
    answers: list[AnswerIn],
    meter_unit: str = "",
    meter_value: Decimal | None = None,
    notes: str = "",
) -> EquipmentInspection:
    """Registra una inspección completa o nada (la transacción es única)."""

    equipment = get_visible_equipment(ctx, equipment_id)
    _require_for_equipment(ctx, Capability.CREATE, equipment)

    existing = scoped_qs(ctx, EquipmentInspection).filter(pk=inspection_id).first()
    if existing is not None:
        if existing.equipment_id != equipment.pk or existing.operator_id != ctx.user_id:
            raise ValidationError("El identificador de la inspección ya está en uso.")
        return existing

    if equipment.status in (EquipmentStatus.RETIRED, EquipmentStatus.OUT_OF_SERVICE):
        raise ValidationError("El equipo no está disponible para operar.")
    if captured_at > timezone.now() + timedelta(minutes=5):
        raise ValidationError("La fecha de la inspección no puede estar en el futuro.")

    checklist = {item.pk: item for item in checklist_for(ctx, equipment.equipment_type_id)}
    if not checklist:
        raise ValidationError("La tipología del equipo no tiene lista de chequeo.")
    given = {answer.item_id for answer in answers}
    if len(given) != len(answers):
        raise ValidationError("Hay ítems respondidos más de una vez.")
    unknown = given - set(checklist)
    if unknown:
        raise ValidationError("Hay ítems que no pertenecen a la lista de chequeo vigente.")
    missing = set(checklist) - given
    if missing:
        raise ValidationError("Faltan ítems por responder en la lista de chequeo.")

    needs_meter = equipment.control_scheme == "meter"
    if needs_meter and (not meter_unit or meter_value is None):
        raise ValidationError("La lectura inicial del medidor es obligatoria.")

    snapshot: list[dict[str, Any]] = []
    for item in checklist.values():
        answer = next(a for a in answers if a.item_id == item.pk)
        note = answer.note.strip()
        if not answer.ok and not note:
            raise ValidationError(f"Describe la novedad del ítem «{item.label}».")
        snapshot.append(
            {
                "item_id": str(item.pk),
                "label": item.label,
                "critical": item.critical,
                "ok": answer.ok,
                "note": note,
            }
        )

    if needs_meter and meter_value is not None:
        record_meter_reading(
            ctx,
            equipment_id=equipment.pk,
            meter_unit=meter_unit,
            value=meter_value,
            read_at=captured_at,
            source="preoperational",
            notes="Preoperacional",
        )

    project_id = (
        scoped_qs(ctx, EquipmentProject)
        .filter(equipment=equipment, ended_on__isnull=True)
        .values_list("project_id", flat=True)
        .first()
    )
    faulty = [row for row in snapshot if not row["ok"]]
    inspection = EquipmentInspection(
        pk=inspection_id,
        org_id=ctx.org_id,
        equipment=equipment,
        operator_id=ctx.user_id,
        project_id=project_id,
        captured_at=captured_at,
        meter_unit=meter_unit if needs_meter else "",
        meter_value=meter_value if needs_meter else None,
        items=snapshot,
        notes=notes.strip(),
        has_faults=bool(faulty),
    )
    inspection.full_clean()
    inspection.save(force_insert=True)
    for row in faulty:
        fault = EquipmentFault(
            org_id=ctx.org_id,
            inspection=inspection,
            equipment=equipment,
            item_label=row["label"],
            critical=row["critical"],
            description=row["note"],
        )
        fault.full_clean()
        fault.save(force_insert=True)
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_INSPECTION_SUBMITTED,
        target=f"equipment-inspection:{inspection.pk}",
        metadata={
            "equipment": str(equipment.pk),
            "faults": len(faulty),
            "critical_faults": sum(1 for row in faulty if row["critical"]),
        },
    )
    return inspection


# ---------------------------------------------------------------------------
# Revisión y novedades
# ---------------------------------------------------------------------------


@transaction.atomic
def review_inspection(
    ctx: RequestContext, inspection_id: UUID, *, decision: str, comment: str = ""
) -> EquipmentInspection:
    if decision not in REVIEW_DECISIONS:
        raise ValidationError("Decisión de revisión desconocida.")
    status, capability, comment_required = REVIEW_DECISIONS[decision]
    try:
        inspection = visible_inspections(ctx).select_for_update().get(pk=inspection_id)
    except EquipmentInspection.DoesNotExist as exc:
        raise Http404 from exc
    _require_for_equipment(ctx, capability, inspection.equipment)
    if inspection.operator_id == ctx.user_id:
        raise PermissionDenied("No puedes revisar tu propia inspección.")
    if inspection.status != InspectionStatus.SUBMITTED:
        raise ValidationError("Esta inspección ya fue revisada.")
    if comment_required and not comment.strip():
        raise ValidationError("Escribe el motivo de la decisión.")
    inspection.status = status
    inspection.reviewed_by_id = ctx.user_id
    inspection.reviewed_at = timezone.now()
    inspection.review_comment = comment.strip()
    inspection.full_clean()
    inspection.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_comment"])
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_INSPECTION_REVIEWED,
        target=f"equipment-inspection:{inspection.pk}",
        metadata={"decision": decision, "equipment": str(inspection.equipment_id)},
    )
    return inspection


@transaction.atomic
def close_fault(ctx: RequestContext, fault_id: UUID, *, note: str) -> EquipmentFault:
    try:
        fault = visible_faults(ctx).select_for_update().get(pk=fault_id)
    except EquipmentFault.DoesNotExist as exc:
        raise Http404 from exc
    _require_for_equipment(ctx, Capability.UPDATE, fault.equipment)
    if is_operator(ctx):
        # El operador reporta; quien gestiona el equipo cierra (evita cerrar su propia novedad).
        raise PermissionDenied("Un operador no puede cerrar novedades.")
    if fault.status == FaultStatus.CLOSED:
        raise ValidationError("La novedad ya está cerrada.")
    if not note.strip():
        raise ValidationError("Describe cómo se resolvió la novedad.")
    fault.status = FaultStatus.CLOSED
    fault.closed_by_id = ctx.user_id
    fault.closed_at = timezone.now()
    fault.closing_note = note.strip()
    fault.save(update_fields=["status", "closed_by", "closed_at", "closing_note"])
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_FAULT_CLOSED,
        target=f"equipment-fault:{fault.pk}",
        metadata={"equipment": str(fault.equipment_id)},
    )
    return fault
