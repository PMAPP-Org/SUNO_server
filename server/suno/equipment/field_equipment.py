"""Equipos para el móvil: proyección de solo lectura de lo que el usuario puede ver.

Reutiliza `visible_equipment` (organización, alcance por proyecto y, para operadores,
asignación vigente), así que el teléfono nunca recibe un equipo que la web no mostraría.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any
from uuid import UUID

from suno.core.context import RequestContext
from suno.core.models import (
    OPEN_ORDER_STATUSES,
    Equipment,
    EquipmentInspection,
    EquipmentProject,
    EquipmentStatus,
    EquipmentUsage,
    PreoperationalItem,
    Project,
    WorkOrder,
)
from suno.core.scoping import scoped_qs
from suno.equipment.datasheet import equipment_alerts, field_values_for, latest_meter_readings
from suno.equipment.inspections import recent_inspections
from suno.equipment.maintenance import (
    PlanStatus,
    plan_status,
    visible_orders,
    visible_plans,
)
from suno.equipment.qr import equipment_identifier
from suno.equipment.services import visible_equipment
from suno.equipment.usage_reports import recent_usage, usage_projects

__all__ = ["field_equipment", "list_version"]


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat()


def _plan(status: PlanStatus) -> dict[str, Any]:
    plan = status.plan
    return {
        "id": str(plan.pk),
        "name": plan.name,
        "kind": plan.kind,
        "meter_unit": plan.meter_unit or None,
        "state": status.state,
        "remaining": None if status.remaining is None else float(status.remaining),
        "due_value": None if status.due_value is None else float(status.due_value),
        "due_on": _iso(status.due_on),
        "has_open_order": status.open_order is not None,
    }


def _field_value(row: Any) -> str | float | None:
    if row.date_value is not None:
        return row.date_value.isoformat()
    if row.number_value is not None:
        return float(row.number_value)
    return row.text_value or None


def _item(
    ctx: RequestContext,
    equipment: Equipment,
    today: date,
    project_id: UUID | None,
    checklist: list[PreoperationalItem],
    inspections: list[EquipmentInspection],
    plans: list[PlanStatus],
    orders: list[WorkOrder],
    projects: list[Project],
    usages: list[EquipmentUsage],
) -> dict[str, Any]:
    department = equipment.department
    return {
        "id": str(equipment.pk),
        "code": equipment.code,
        "name": equipment.name,
        "qr_identifier": equipment_identifier(equipment.pk),
        "equipment_type": {
            "id": str(equipment.equipment_type_id),
            "code": equipment.equipment_type.code,
            "name": equipment.equipment_type.name,
            "preoperational_template_id": (
                str(equipment.equipment_type.preoperational_template_id)
                if equipment.equipment_type.preoperational_template_id
                else None
            ),
        },
        "status": equipment.status,
        "ownership": equipment.ownership,
        "license_plate": equipment.license_plate,
        "manufacturer": equipment.manufacturer,
        "model": equipment.model,
        "serial_number": equipment.serial_number,
        "manufacture_year": equipment.manufacture_year,
        "department": (
            {"code": department.cost_center_code, "name": department.name} if department else None
        ),
        "control_scheme": equipment.control_scheme,
        "work_source": equipment.work_source,
        "current_project_id": str(project_id) if project_id else None,
        "meters": [
            {
                "unit": snapshot.meter_unit,
                "label": snapshot.label,
                "is_primary": snapshot.meter_unit == equipment.primary_meter_unit,
                "value": float(snapshot.value),
                "read_at": _iso(snapshot.read_at),
            }
            for snapshot in latest_meter_readings(ctx, equipment)
        ],
        "meter_units": [
            unit for unit in (equipment.primary_meter_unit, equipment.secondary_meter_unit) if unit
        ],
        "documents": [
            {
                "id": str(document.pk),
                "type": document.document_type,
                "name": document.name,
                "number": document.number,
                "expires_on": _iso(document.expires_on),
            }
            for document in equipment.documents.all()
        ],
        "fields": [
            {
                "name": row.definition.name,
                "type": row.definition.data_type,
                "value": _field_value(row),
            }
            for row in field_values_for(ctx, equipment)
        ],
        "checklist": [
            {"id": str(item.pk), "label": item.label, "critical": item.critical}
            for item in checklist
        ],
        "recent_inspections": [
            {
                "id": str(inspection.pk),
                "captured_at": _iso(inspection.captured_at),
                "status": inspection.status,
                "has_faults": inspection.has_faults,
                "review_comment": inspection.review_comment,
            }
            for inspection in inspections
        ],
        "usage_projects": [{"id": str(project.pk), "name": project.name} for project in projects],
        "recent_usage": [
            {
                "id": str(usage.pk),
                "usage_date": usage.usage_date.isoformat(),
                "start_time": usage.start_time.strftime("%H:%M"),
                "end_time": usage.end_time.strftime("%H:%M"),
                "hours": float(usage.billable_hours) if usage.billable_hours is not None else None,
                "activity": usage.activity,
                "project_name": usage.project.name,
                "status": usage.status,
            }
            for usage in usages
        ],
        "maintenance_plans": [_plan(status) for status in plans],
        "open_work_orders": [
            {
                "id": str(order.pk),
                "code": order.code,
                "kind": order.kind,
                "status": order.status,
                "description": order.description,
            }
            for order in orders
        ],
        "alerts": [
            {
                "source": alert.source,
                "name": alert.name,
                "expires_on": alert.expires_on.isoformat(),
                "days_left": alert.days_left,
            }
            for alert in equipment_alerts(ctx, equipment, today=today)
        ],
    }


def field_equipment(ctx: RequestContext, *, today: date | None = None) -> list[dict[str, Any]]:
    """Equipos activos visibles para quien llama, ordenados por código."""

    today = today or date.today()
    equipment = list(
        visible_equipment(ctx)
        .exclude(status=EquipmentStatus.RETIRED)
        .select_related("equipment_type", "department")
        .prefetch_related("documents")
        .order_by("code")
    )
    open_links = dict(
        scoped_qs(ctx, EquipmentProject)
        .filter(equipment_id__in=[item.pk for item in equipment], ended_on__isnull=True)
        .values_list("equipment_id", "project_id")
    )
    checklists: dict[UUID, list[PreoperationalItem]] = {}
    for entry in scoped_qs(ctx, PreoperationalItem).filter(
        equipment_type_id__in={item.equipment_type_id for item in equipment}, active=True
    ):
        checklists.setdefault(entry.equipment_type_id, []).append(entry)
    inspections = recent_inspections(ctx, [item.pk for item in equipment])
    ids = [item.pk for item in equipment]
    plans: dict[UUID, list[PlanStatus]] = {}
    for plan in (
        visible_plans(ctx).filter(equipment_id__in=ids, active=True).select_related("equipment")
    ):
        plans.setdefault(plan.equipment_id, []).append(plan_status(ctx, plan, today=today))
    projects = usage_projects(ctx, ids, on=today)
    usages = recent_usage(ctx, ids)
    orders: dict[UUID, list[WorkOrder]] = {}
    for order in visible_orders(ctx).filter(equipment_id__in=ids, status__in=OPEN_ORDER_STATUSES):
        orders.setdefault(order.equipment_id, []).append(order)
    return [
        _item(
            ctx,
            item,
            today,
            open_links.get(item.pk),
            checklists.get(item.equipment_type_id, []),
            inspections.get(item.pk, []),
            plans.get(item.pk, []),
            sorted(orders.get(item.pk, []), key=lambda order: order.number),
            projects.get(item.pk, []),
            usages.get(item.pk, []),
        )
        for item in equipment
    ]


def list_version(items: list[dict[str, Any]]) -> str:
    """Huella SHA-256 de la lista: cambia si cambia cualquier dato entregado."""

    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
