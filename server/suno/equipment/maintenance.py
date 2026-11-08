"""Mantenimiento (M&E fase 3): planes preventivos, alertas, órdenes de trabajo e indicadores.

Reglas que este módulo garantiza:
- Un plan por medidor o por fecha calcula cuánto falta; cuando falta menos que su ventana
  de aviso el equipo entra al panel de alertas. La alerta solo desaparece al cerrar
  técnicamente la orden (que reinicia el plan), no al crearla ni al cancelarla.
- La orden recorre borrador → aprobada → en ejecución → cerrada técnicamente; solo se
  cancela antes de ejecutarse. Quien aprueba no es quien la creó.
- Al cerrar una correctiva o un overhaul se exige el modo de falla (parte y causa).
- Al cerrar se cierran las novedades que la originaron y el equipo vuelve a operar.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Max, QuerySet, Sum
from django.http import Http404
from django.utils import timezone

from suno.core import audit
from suno.core.access import Capability
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    OPEN_ORDER_STATUSES,
    Equipment,
    EquipmentFault,
    EquipmentMeterReading,
    EquipmentProject,
    EquipmentStatus,
    EquipmentUsage,
    EquipmentUsageStatus,
    FaultStatus,
    MaintenancePlan,
    Membership,
    PlanKind,
    WorkOrder,
    WorkOrderKind,
    WorkOrderLine,
    WorkOrderLineKind,
    WorkOrderMechanic,
    WorkOrderStatus,
)
from suno.core.scoping import scoped_qs
from suno.equipment.costing import DAILY_AVAILABLE_HOURS, pct_of, weekdays_between
from suno.equipment.inspections import is_operator
from suno.equipment.services import _require_for_equipment, get_visible_equipment, visible_equipment

__all__ = [
    "MaintenanceKpis",
    "PlanStatus",
    "add_line",
    "approve_order",
    "assign_mechanic",
    "cancel_order",
    "close_order",
    "create_plan",
    "create_work_order",
    "maintenance_alerts",
    "maintenance_kpis",
    "plan_status",
    "preventive_history",
    "remove_line",
    "retire_plan",
    "start_order",
    "visible_orders",
    "visible_plans",
]

_ORDER_LOCK_NAMESPACE = "work-order-number"


# ---------------------------------------------------------------------------
# Permisos y visibilidad
# ---------------------------------------------------------------------------


def _require_manager(ctx: RequestContext, capability: Capability, equipment: Equipment) -> None:
    """Gestiona mantenimiento quien administra el equipo; el operador solo reporta."""

    _require_for_equipment(ctx, capability, equipment)
    if is_operator(ctx):
        raise PermissionDenied("Un operador no gestiona el mantenimiento.")


def visible_plans(ctx: RequestContext) -> QuerySet[MaintenancePlan]:
    return scoped_qs(ctx, MaintenancePlan).filter(equipment__in=visible_equipment(ctx))


def visible_orders(ctx: RequestContext) -> QuerySet[WorkOrder]:
    return scoped_qs(ctx, WorkOrder).filter(equipment__in=visible_equipment(ctx))


def _get_order(ctx: RequestContext, order_id: UUID, *, lock: bool = False) -> WorkOrder:
    queryset = visible_orders(ctx).select_related("equipment", "plan")
    try:
        return (queryset.select_for_update(of=("self",)) if lock else queryset).get(pk=order_id)
    except WorkOrder.DoesNotExist as exc:
        raise Http404 from exc


# ---------------------------------------------------------------------------
# Medidores
# ---------------------------------------------------------------------------


def _current_meter(ctx: RequestContext, equipment: Equipment, unit: str) -> Decimal | None:
    row = (
        scoped_qs(ctx, EquipmentMeterReading)
        .filter(equipment=equipment, meter_unit=unit)
        .order_by("-read_at", "-created_at")
        .first()
    )
    return row.value if row else None


# ---------------------------------------------------------------------------
# Planes
# ---------------------------------------------------------------------------


@transaction.atomic
def create_plan(
    ctx: RequestContext,
    *,
    equipment_id: UUID,
    name: str,
    kind: str,
    meter_unit: str = "",
    interval_value: Decimal | None = None,
    interval_days: int | None = None,
    alert_window: Decimal = Decimal("0"),
    last_done_value: Decimal | None = None,
    last_done_on: date | None = None,
) -> MaintenancePlan:
    """Define una rutina preventiva. Sin punto de partida, cuenta desde hoy: el medidor
    actual (plan por medidor) o la fecha de hoy (plan por fecha)."""

    equipment = get_visible_equipment(ctx, equipment_id)
    _require_manager(ctx, Capability.UPDATE, equipment)
    if kind == PlanKind.METER and meter_unit not in (
        equipment.primary_meter_unit,
        equipment.secondary_meter_unit,
    ):
        raise ValidationError("El equipo no tiene configurado ese medidor.")
    if kind == PlanKind.METER and last_done_value is None:
        last_done_value = _current_meter(ctx, equipment, meter_unit) or Decimal("0")
    if kind == PlanKind.DATE and last_done_on is None:
        last_done_on = date.today()
    plan = MaintenancePlan(
        org_id=ctx.org_id,
        equipment=equipment,
        name=name.strip(),
        kind=kind,
        meter_unit=meter_unit if kind == PlanKind.METER else "",
        interval_value=interval_value if kind == PlanKind.METER else None,
        interval_days=interval_days if kind == PlanKind.DATE else None,
        alert_window=alert_window,
        last_done_value=last_done_value if kind == PlanKind.METER else None,
        last_done_on=last_done_on if kind == PlanKind.DATE else None,
    )
    plan.full_clean()
    plan.save()
    audit.record(
        ctx,
        action=AuditAction.MAINTENANCE_PLAN_SAVED,
        target=f"maintenance-plan:{plan.pk}",
        metadata={"equipment": str(equipment.pk), "name": plan.name, "kind": kind},
    )
    return plan


@transaction.atomic
def retire_plan(ctx: RequestContext, plan_id: UUID) -> MaintenancePlan:
    try:
        plan = (
            visible_plans(ctx)
            .select_related("equipment")
            .select_for_update(of=("self",))
            .get(pk=plan_id)
        )
    except MaintenancePlan.DoesNotExist as exc:
        raise Http404 from exc
    _require_manager(ctx, Capability.UPDATE, plan.equipment)
    if plan.work_orders.filter(status__in=OPEN_ORDER_STATUSES).exists():
        raise ValidationError("El plan tiene una orden abierta; ciérrala o cancélala primero.")
    plan.active = False
    plan.save(update_fields=["active"])
    audit.record(
        ctx,
        action=AuditAction.MAINTENANCE_PLAN_SAVED,
        target=f"maintenance-plan:{plan.pk}",
        metadata={"retired": plan.name},
    )
    return plan


@dataclass(frozen=True)
class PlanStatus:
    plan: MaintenancePlan
    #: Medidor actual (plan por medidor); `None` si aún no hay lecturas.
    current: Decimal | None
    #: Vencimiento: valor de medidor o fecha según el plan.
    due_value: Decimal | None
    due_on: date | None
    #: Lo que falta (medidor o días); negativo = vencido; `None` si no se puede calcular.
    remaining: Decimal | None
    state: str  # "ok" | "due_soon" | "overdue" | "unknown"
    open_order: WorkOrder | None

    @property
    def urgency(self) -> Decimal:
        """Más bajo = más urgente (proporción del intervalo que falta)."""

        if self.remaining is None:
            return Decimal("999")
        interval = self.plan.interval_value or Decimal(self.plan.interval_days or 1)
        return self.remaining / interval


def plan_status(
    ctx: RequestContext, plan: MaintenancePlan, *, today: date | None = None
) -> PlanStatus:
    today = today or date.today()
    open_order = (
        scoped_qs(ctx, WorkOrder)
        .filter(plan=plan, status__in=OPEN_ORDER_STATUSES)
        .order_by("-number")
        .first()
    )
    if plan.kind == PlanKind.METER:
        current = _current_meter(ctx, plan.equipment, plan.meter_unit)
        due_value = (plan.last_done_value or Decimal("0")) + (plan.interval_value or Decimal("0"))
        remaining = None if current is None else due_value - current
        return PlanStatus(
            plan,
            current,
            due_value,
            None,
            remaining,
            _state(remaining, plan.alert_window),
            open_order,
        )
    due_on = (plan.last_done_on or today) + timedelta(days=plan.interval_days or 0)
    remaining = Decimal((due_on - today).days)
    return PlanStatus(
        plan, None, None, due_on, remaining, _state(remaining, plan.alert_window), open_order
    )


def _state(remaining: Decimal | None, window: Decimal) -> str:
    if remaining is None:
        return "unknown"
    if remaining < 0:
        return "overdue"
    return "due_soon" if remaining <= window else "ok"


def maintenance_alerts(
    ctx: RequestContext, *, today: date | None = None, equipment_id: UUID | None = None
) -> list[PlanStatus]:
    """Panel de alertas preventivas: planes vigentes que están por vencer o vencidos."""

    plans = visible_plans(ctx).filter(active=True).select_related("equipment")
    if equipment_id is not None:
        plans = plans.filter(equipment_id=equipment_id)
    statuses = [plan_status(ctx, plan, today=today) for plan in plans]
    alerts = [status for status in statuses if status.state in ("due_soon", "overdue")]
    alerts.sort(key=lambda status: (status.urgency, status.plan.equipment.code))
    return alerts


# ---------------------------------------------------------------------------
# Órdenes de trabajo
# ---------------------------------------------------------------------------


def _next_number(ctx: RequestContext) -> int:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("La numeración requiere una transacción abierta.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"{_ORDER_LOCK_NAMESPACE}:{ctx.org_id}"],
        )
    last = scoped_qs(ctx, WorkOrder).aggregate(top=Max("number"))["top"]
    return (last or 0) + 1


def _mechanics(ctx: RequestContext, user_ids: Iterable[UUID]) -> list[Any]:
    ids = list(dict.fromkeys(user_ids))
    members = list(scoped_qs(ctx, Membership).filter(user_id__in=ids).select_related("user"))
    if len(members) != len(ids):
        raise ValidationError("Hay mecánicos que no pertenecen a la organización.")
    return [member.user for member in members]


@transaction.atomic
def create_work_order(
    ctx: RequestContext,
    *,
    equipment_id: UUID,
    kind: str,
    description: str,
    plan_id: UUID | None = None,
    fault_ids: Iterable[UUID] = (),
    mechanic_ids: Iterable[UUID] = (),
) -> WorkOrder:
    """Crea la orden en borrador, desde un plan (preventiva) y/o desde novedades."""

    equipment = get_visible_equipment(ctx, equipment_id)
    _require_manager(ctx, Capability.CREATE, equipment)
    if kind not in WorkOrderKind.values:
        raise ValidationError("Tipo de orden desconocido.")
    plan = None
    if plan_id is not None:
        try:
            plan = scoped_qs(ctx, MaintenancePlan).get(pk=plan_id, equipment=equipment, active=True)
        except MaintenancePlan.DoesNotExist as exc:
            raise ValidationError("El plan no existe, está retirado o es de otro equipo.") from exc
        if kind != WorkOrderKind.PREVENTIVE:
            raise ValidationError("Una orden que nace de un plan es preventiva.")
        if plan.work_orders.filter(status__in=OPEN_ORDER_STATUSES).exists():
            raise ValidationError("Ya hay una orden abierta para este plan.")
    faults = list(
        scoped_qs(ctx, EquipmentFault)
        .select_for_update()
        .filter(pk__in=list(fault_ids), equipment=equipment, status=FaultStatus.OPEN)
    )
    if len(faults) != len(set(fault_ids)):
        raise ValidationError(
            "Hay novedades que no existen, ya están cerradas o son de otro equipo."
        )
    if any(fault.work_order_id for fault in faults):
        raise ValidationError("Hay novedades que ya tienen una orden de trabajo.")
    if faults and kind == WorkOrderKind.PREVENTIVE:
        raise ValidationError("Las novedades se atienden con una orden correctiva u overhaul.")
    if not description.strip():
        raise ValidationError("Describe el trabajo a realizar.")
    users = _mechanics(ctx, mechanic_ids)

    order = WorkOrder(
        org_id=ctx.org_id,
        number=_next_number(ctx),
        equipment=equipment,
        kind=kind,
        plan=plan,
        description=description.strip(),
        created_by_id=ctx.user_id,
    )
    order.full_clean()
    order.save(force_insert=True)
    for fault in faults:
        fault.work_order = order
        fault.save(update_fields=["work_order"])
    for user in users:
        _link_mechanic(ctx, order, user)
    audit.record(
        ctx,
        action=AuditAction.WORK_ORDER_CREATED,
        target=f"work-order:{order.pk}",
        metadata={
            "number": order.number,
            "equipment": str(equipment.pk),
            "kind": kind,
            "faults": len(faults),
        },
    )
    return order


def _link_mechanic(ctx: RequestContext, order: WorkOrder, user: Any) -> WorkOrderMechanic:
    link = WorkOrderMechanic(org_id=ctx.org_id, order=order, mechanic=user)
    link.full_clean()
    link.save(force_insert=True)
    return link


def _require_open(order: WorkOrder) -> None:
    if order.status not in OPEN_ORDER_STATUSES:
        raise ValidationError("La orden ya está cerrada o cancelada.")


@transaction.atomic
def assign_mechanic(ctx: RequestContext, order_id: UUID, *, mechanic_id: UUID) -> WorkOrderMechanic:
    order = _get_order(ctx, order_id, lock=True)
    _require_manager(ctx, Capability.UPDATE, order.equipment)
    _require_open(order)
    (user,) = _mechanics(ctx, [mechanic_id])
    if order.mechanics.filter(mechanic=user).exists():
        raise ValidationError("El mecánico ya está asignado a la orden.")
    return _link_mechanic(ctx, order, user)


@transaction.atomic
def add_line(
    ctx: RequestContext,
    order_id: UUID,
    *,
    kind: str,
    description: str,
    quantity: Decimal,
    unit_cost: Decimal,
    supplier: str = "",
) -> WorkOrderLine:
    order = _get_order(ctx, order_id, lock=True)
    _require_manager(ctx, Capability.UPDATE, order.equipment)
    _require_open(order)
    if kind not in WorkOrderLineKind.values:
        raise ValidationError("Tipo de línea desconocido.")
    line = WorkOrderLine(
        org_id=ctx.org_id,
        order=order,
        kind=kind,
        description=description.strip(),
        supplier=supplier.strip(),
        quantity=quantity,
        unit_cost=unit_cost,
    )
    line.full_clean()
    line.save(force_insert=True)
    audit.record(
        ctx,
        action=AuditAction.WORK_ORDER_LINE_CHANGED,
        target=f"work-order:{order.pk}",
        metadata={"added": line.description, "kind": kind, "total": str(line.total)},
    )
    return line


@transaction.atomic
def remove_line(ctx: RequestContext, line_id: UUID) -> None:
    try:
        line = scoped_qs(ctx, WorkOrderLine).select_related("order__equipment").get(pk=line_id)
    except WorkOrderLine.DoesNotExist as exc:
        raise Http404 from exc
    order = _get_order(ctx, line.order_id, lock=True)
    _require_manager(ctx, Capability.UPDATE, order.equipment)
    _require_open(order)
    description = line.description
    line.delete()
    audit.record(
        ctx,
        action=AuditAction.WORK_ORDER_LINE_CHANGED,
        target=f"work-order:{order.pk}",
        metadata={"removed": description},
    )


@transaction.atomic
def approve_order(ctx: RequestContext, order_id: UUID) -> WorkOrder:
    order = _get_order(ctx, order_id, lock=True)
    _require_manager(ctx, Capability.APPROVE, order.equipment)
    if order.status != WorkOrderStatus.DRAFT:
        raise ValidationError("Solo se aprueba una orden en borrador.")
    if order.created_by_id == ctx.user_id:
        raise PermissionDenied("No puedes aprobar una orden que creaste.")
    order.status = WorkOrderStatus.APPROVED
    order.approved_by_id = ctx.user_id
    order.approved_at = timezone.now()
    order.save(update_fields=["status", "approved_by", "approved_at"])
    audit.record(
        ctx,
        action=AuditAction.WORK_ORDER_APPROVED,
        target=f"work-order:{order.pk}",
        metadata={"number": order.number},
    )
    return order


@transaction.atomic
def start_order(ctx: RequestContext, order_id: UUID) -> WorkOrder:
    order = _get_order(ctx, order_id, lock=True)
    _require_manager(ctx, Capability.UPDATE, order.equipment)
    if order.status != WorkOrderStatus.APPROVED:
        raise ValidationError("Solo se inicia una orden aprobada.")
    order.status = WorkOrderStatus.IN_EXECUTION
    order.started_at = timezone.now()
    order.save(update_fields=["status", "started_at"])
    equipment = order.equipment
    if equipment.status in (EquipmentStatus.AVAILABLE, EquipmentStatus.ASSIGNED):
        equipment.status = EquipmentStatus.MAINTENANCE
        equipment.updated_by_id = ctx.user_id
        equipment.save(update_fields=["status", "updated_by", "updated_at"])
    audit.record(
        ctx,
        action=AuditAction.WORK_ORDER_STARTED,
        target=f"work-order:{order.pk}",
        metadata={"number": order.number},
    )
    return order


@transaction.atomic
def cancel_order(ctx: RequestContext, order_id: UUID, *, reason: str) -> WorkOrder:
    order = _get_order(ctx, order_id, lock=True)
    _require_manager(ctx, Capability.UPDATE, order.equipment)
    if order.status not in (WorkOrderStatus.DRAFT, WorkOrderStatus.APPROVED):
        raise ValidationError("Solo se cancela una orden que aún no se ejecuta.")
    if not reason.strip():
        raise ValidationError("Escribe el motivo de la cancelación.")
    order.status = WorkOrderStatus.CANCELLED
    order.closing_notes = reason.strip()
    order.closed_by_id = ctx.user_id
    order.closed_at = timezone.now()
    order.save(update_fields=["status", "closing_notes", "closed_by", "closed_at"])
    # Las novedades vuelven a quedar libres para otra orden.
    scoped_qs(ctx, EquipmentFault).filter(work_order=order).update(work_order=None)
    audit.record(
        ctx,
        action=AuditAction.WORK_ORDER_CANCELLED,
        target=f"work-order:{order.pk}",
        metadata={"number": order.number},
    )
    return order


@transaction.atomic
def close_order(
    ctx: RequestContext,
    order_id: UUID,
    *,
    downtime_hours: Decimal | None,
    failed_part: str = "",
    failure_cause: str = "",
    notes: str = "",
    mechanic_ids: Iterable[UUID] = (),
) -> WorkOrder:
    """Cierre técnico: duración, mecánicos y, en correctivas, el modo de falla."""

    order = _get_order(ctx, order_id, lock=True)
    _require_manager(ctx, Capability.UPDATE, order.equipment)
    if order.status != WorkOrderStatus.IN_EXECUTION:
        raise ValidationError("Solo se cierra una orden en ejecución.")
    if downtime_hours is None or downtime_hours < 0:
        raise ValidationError("Registra la duración de la orden (horas de indisponibilidad).")
    if order.kind != WorkOrderKind.PREVENTIVE and not (
        failed_part.strip() and failure_cause.strip()
    ):
        raise ValidationError("Indica la parte afectada y la causa de la falla.")
    for user in _mechanics(ctx, mechanic_ids):
        if not order.mechanics.filter(mechanic=user).exists():
            _link_mechanic(ctx, order, user)
    if not order.mechanics.exists():
        raise ValidationError("Asigna al menos un mecánico antes de cerrar la orden.")

    equipment = order.equipment
    unit = equipment.primary_meter_unit
    meter = _current_meter(ctx, equipment, unit) if unit else None
    now = timezone.now()
    order.status = WorkOrderStatus.CLOSED_TECHNICAL
    order.closed_by_id = ctx.user_id
    order.closed_at = now
    order.downtime_hours = downtime_hours
    order.failed_part = failed_part.strip()
    order.failure_cause = failure_cause.strip()
    order.closing_notes = notes.strip()
    order.meter_unit_at_close = unit if meter is not None else ""
    order.meter_at_close = meter
    order.save(
        update_fields=[
            "status",
            "closed_by",
            "closed_at",
            "downtime_hours",
            "failed_part",
            "failure_cause",
            "closing_notes",
            "meter_unit_at_close",
            "meter_at_close",
        ]
    )

    plan = order.plan
    if plan is not None:
        # El plan vuelve a contar desde esta ejecución: la alerta desaparece del panel.
        if plan.kind == PlanKind.METER:
            current = _current_meter(ctx, equipment, plan.meter_unit)
            plan.last_done_value = current if current is not None else plan.last_done_value
        plan.last_done_on = now.date()
        plan.save(update_fields=["last_done_value", "last_done_on"])

    for fault in scoped_qs(ctx, EquipmentFault).filter(work_order=order, status=FaultStatus.OPEN):
        fault.status = FaultStatus.CLOSED
        fault.closed_by_id = ctx.user_id
        fault.closed_at = now
        fault.closing_note = f"Resuelta con la orden {order.code}."
        fault.save(update_fields=["status", "closed_by", "closed_at", "closing_note"])

    if (
        equipment.status == EquipmentStatus.MAINTENANCE
        and not scoped_qs(ctx, WorkOrder)
        .filter(equipment=equipment, status=WorkOrderStatus.IN_EXECUTION)
        .exclude(pk=order.pk)
        .exists()
    ):
        has_project = (
            scoped_qs(ctx, EquipmentProject)
            .filter(equipment=equipment, ended_on__isnull=True)
            .exists()
        )
        equipment.status = EquipmentStatus.ASSIGNED if has_project else EquipmentStatus.AVAILABLE
        equipment.updated_by_id = ctx.user_id
        equipment.save(update_fields=["status", "updated_by", "updated_at"])

    audit.record(
        ctx,
        action=AuditAction.WORK_ORDER_CLOSED,
        target=f"work-order:{order.pk}",
        metadata={
            "number": order.number,
            "downtime_hours": str(downtime_hours),
            "kind": order.kind,
        },
    )
    return order


# ---------------------------------------------------------------------------
# Indicadores e historial
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MaintenanceKpis:
    since: date
    until: date
    #: Horas disponibles del periodo (días hábiles × jornada estándar).
    scheduled_hours: Decimal
    operating_hours: Decimal
    downtime_hours: Decimal
    #: (horas disponibles − duración de las órdenes) / horas disponibles, en %.
    availability_pct: Decimal | None
    corrective_orders: int
    #: Horas de operación / órdenes correctivas; `None` sin fallas en el periodo.
    mtbf_hours: Decimal | None
    #: Días promedio entre crear la orden y aprobarla / entre aprobarla y empezarla.
    avg_days_to_approve: Decimal | None
    avg_days_to_start: Decimal | None


def _avg_days(pairs: list[tuple[datetime, datetime]]) -> Decimal | None:
    if not pairs:
        return None
    total = sum((end - start).total_seconds() for start, end in pairs)
    return (Decimal(str(total)) / Decimal(86400) / len(pairs)).quantize(Decimal("0.1"))


def maintenance_kpis(
    ctx: RequestContext, equipment: Equipment, *, since: date, until: date
) -> MaintenanceKpis:
    """Disponibilidad, MTBF y tiempos de trámite de un equipo en un periodo."""

    orders = visible_orders(ctx).filter(equipment=equipment)
    closed = orders.filter(
        status=WorkOrderStatus.CLOSED_TECHNICAL,
        closed_at__date__gte=since,
        closed_at__date__lte=until,
    )
    downtime = closed.aggregate(total=Sum("downtime_hours"))["total"] or Decimal("0")
    usage = scoped_qs(ctx, EquipmentUsage).filter(
        equipment=equipment,
        status=EquipmentUsageStatus.COMPLETED,
        usage_date__gte=since,
        usage_date__lte=until,
    ).aggregate(total=Sum("billable_hours"))["total"] or Decimal("0")
    scheduled = weekdays_between(since, until) * DAILY_AVAILABLE_HOURS
    availability = None
    if scheduled:
        availability = max(Decimal("0"), pct_of(scheduled - downtime, scheduled) or Decimal("0"))
    corrective = closed.filter(kind=WorkOrderKind.CORRECTIVE).count()
    mtbf = (usage / corrective).quantize(Decimal("0.01")) if corrective and usage else None
    approvals = [
        (row.created_at, row.approved_at)
        for row in orders.filter(approved_at__isnull=False, created_at__date__gte=since)
        if row.approved_at
    ]
    starts = [
        (row.approved_at, row.started_at)
        for row in orders.filter(started_at__isnull=False, created_at__date__gte=since)
        if row.approved_at and row.started_at
    ]
    return MaintenanceKpis(
        since=since,
        until=until,
        scheduled_hours=scheduled,
        operating_hours=usage,
        downtime_hours=downtime,
        availability_pct=availability,
        corrective_orders=corrective,
        mtbf_hours=mtbf,
        avg_days_to_approve=_avg_days(approvals),
        avg_days_to_start=_avg_days(starts),
    )


def preventive_history(
    ctx: RequestContext, equipment: Equipment, *, limit: int = 5
) -> list[WorkOrder]:
    """Últimos mantenimientos preventivos ejecutados (ciclo, fecha, orden y mecánicos)."""

    return list(
        visible_orders(ctx)
        .filter(
            equipment=equipment,
            kind=WorkOrderKind.PREVENTIVE,
            status=WorkOrderStatus.CLOSED_TECHNICAL,
        )
        .select_related("plan")
        .prefetch_related("mechanics__mechanic")
        .order_by("-closed_at")[:limit]
    )
