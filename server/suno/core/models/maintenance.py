"""Mantenimiento de equipos (M&E fase 3): planes preventivos y órdenes de trabajo.

Un plan dice cada cuánto (medidor o días) se interviene un equipo; cuando falta
poco, el equipo entra al panel de alertas y se puede generar la orden. La orden
recorre un ciclo de estados y solo al cerrarla técnicamente se reinicia el plan.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from .base import OrgScopedModel


class PlanKind(models.TextChoices):
    METER = "meter", "Por medidor"
    DATE = "date", "Por fecha"


class WorkOrderKind(models.TextChoices):
    PREVENTIVE = "preventive", "Preventiva"
    CORRECTIVE = "corrective", "Correctiva"
    OVERHAUL = "overhaul", "Overhaul"


class WorkOrderStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    APPROVED = "approved", "Aprobada"
    IN_EXECUTION = "in_execution", "En ejecución"
    CLOSED_TECHNICAL = "closed_technical", "Cerrada técnicamente"
    CANCELLED = "cancelled", "Cancelada"


#: Estados desde los que la orden sigue "viva" (mantiene la alerta del plan).
OPEN_ORDER_STATUSES = ("draft", "approved", "in_execution")


class WorkOrderLineKind(models.TextChoices):
    PART = "part", "Repuesto o insumo"
    SERVICE = "service", "Servicio externo"
    LABOR = "labor", "Mano de obra"


class MaintenancePlan(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment = models.ForeignKey(
        "core.Equipment",
        on_delete=models.PROTECT,
        related_name="maintenance_plans",
        verbose_name="equipo",
    )
    name = models.CharField(max_length=120, verbose_name="rutina")
    kind = models.CharField(max_length=8, choices=PlanKind.choices, verbose_name="control")
    meter_unit = models.CharField(max_length=16, blank=True, verbose_name="medidor")
    interval_value = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="cada (medidor)"
    )
    interval_days = models.PositiveIntegerField(null=True, blank=True, verbose_name="cada (días)")
    # Ventana de aviso: horas/km (plan por medidor) o días (plan por fecha) antes del vencimiento.
    alert_window = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0"), verbose_name="aviso previo"
    )
    last_done_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="medidor en la última ejecución",
    )
    last_done_on = models.DateField(null=True, blank=True, verbose_name="última ejecución")
    active = models.BooleanField(default=True, verbose_name="vigente")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "maintenance_plan"
        ordering = ["equipment__code", "name"]
        verbose_name = "plan de mantenimiento"
        verbose_name_plural = "planes de mantenimiento"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(kind="meter", interval_value__gt=0, interval_days__isnull=True)
                    & ~models.Q(meter_unit="")
                )
                | (
                    models.Q(kind="date", interval_days__gt=0, interval_value__isnull=True)
                    & models.Q(meter_unit="")
                ),
                name="maintenance_plan_kind_consistent",
            ),
            models.CheckConstraint(
                condition=models.Q(alert_window__gte=0), name="maintenance_plan_window_ok"
            ),
        ]
        indexes = [models.Index(fields=["org", "equipment", "active"], name="mplan_equipment_idx")]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.equipment_id and self.org_id and self.equipment.org_id != self.org_id:
            errors["equipment"] = "El equipo debe pertenecer a la organización."
        if self.kind == PlanKind.METER:
            if not self.meter_unit:
                errors["meter_unit"] = "Indica el medidor del plan."
            if not self.interval_value or self.interval_value <= 0:
                errors["interval_value"] = "El intervalo debe ser mayor que cero."
            if self.interval_days is not None:
                errors["interval_days"] = "Un plan por medidor no lleva días."
        elif self.kind == PlanKind.DATE:
            if not self.interval_days:
                errors["interval_days"] = "El intervalo en días debe ser mayor que cero."
            if self.meter_unit or self.interval_value is not None:
                errors["meter_unit"] = "Un plan por fecha no lleva medidor."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.equipment_id}:{self.name}"


class WorkOrder(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.PositiveIntegerField(verbose_name="número")
    equipment = models.ForeignKey(
        "core.Equipment",
        on_delete=models.PROTECT,
        related_name="work_orders",
        verbose_name="equipo",
    )
    kind = models.CharField(max_length=10, choices=WorkOrderKind.choices, verbose_name="tipo")
    status = models.CharField(
        max_length=16,
        choices=WorkOrderStatus.choices,
        default=WorkOrderStatus.DRAFT,
        verbose_name="estado",
    )
    plan = models.ForeignKey(
        MaintenancePlan,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="work_orders",
        verbose_name="plan de origen",
    )
    description = models.TextField(verbose_name="trabajo a realizar")
    created_by = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="work_orders_created",
        verbose_name="creada por",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="work_orders_approved",
        verbose_name="aprobada por",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="inicio de trabajos")
    closed_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="work_orders_closed",
        verbose_name="cerrada por",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    downtime_hours = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="duración (indisponibilidad, h)",
    )
    failed_part = models.CharField(max_length=160, blank=True, verbose_name="parte afectada")
    failure_cause = models.CharField(max_length=200, blank=True, verbose_name="causa de la falla")
    closing_notes = models.TextField(blank=True, verbose_name="notas de cierre")
    meter_unit_at_close = models.CharField(max_length=16, blank=True)
    meter_at_close = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "work_order"
        ordering = ["-number"]
        verbose_name = "orden de trabajo"
        verbose_name_plural = "órdenes de trabajo"
        constraints = [
            models.UniqueConstraint(fields=["org", "number"], name="work_order_org_number_uq"),
            models.CheckConstraint(
                condition=models.Q(downtime_hours__isnull=True) | models.Q(downtime_hours__gte=0),
                name="work_order_downtime_ok",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "status", "-number"], name="work_order_status_idx"),
            models.Index(
                fields=["org", "equipment", "kind", "status"], name="work_order_equip_idx"
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        for field in ("equipment", "plan", "created_by", "approved_by", "closed_by"):
            value = getattr(self, field, None)
            if value is not None and self.org_id and value.org_id != self.org_id:
                errors[field] = "Debe pertenecer a la organización."
        if self.plan_id and self.equipment_id and self.plan.equipment_id != self.equipment_id:
            errors["plan"] = "El plan debe ser del mismo equipo."
        if errors:
            raise ValidationError(errors)

    @property
    def code(self) -> str:
        return f"OT-{self.number:04d}"

    def __str__(self) -> str:
        return self.code


class WorkOrderLine(OrgScopedModel):
    """Insumo, servicio externo o mano de obra de la orden, con su costo."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        WorkOrder, on_delete=models.PROTECT, related_name="lines", verbose_name="orden"
    )
    kind = models.CharField(max_length=8, choices=WorkOrderLineKind.choices, verbose_name="tipo")
    description = models.CharField(max_length=200, verbose_name="descripción")
    supplier = models.CharField(max_length=160, blank=True, verbose_name="proveedor")
    quantity = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="cantidad")
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="costo unitario")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "work_order_line"
        ordering = ["created_at"]
        verbose_name = "línea de orden de trabajo"
        verbose_name_plural = "líneas de órdenes de trabajo"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0, unit_cost__gte=0),
                name="work_order_line_amounts_ok",
            ),
        ]

    @property
    def total(self) -> Decimal:
        return (self.quantity * self.unit_cost).quantize(Decimal("0.01"))

    def clean(self) -> None:
        super().clean()
        if self.order_id and self.org_id and self.order.org_id != self.org_id:
            raise ValidationError({"order": "Debe pertenecer a la organización."})
        if self.kind == WorkOrderLineKind.SERVICE and not self.supplier.strip():
            raise ValidationError({"supplier": "Un servicio externo necesita proveedor."})


class WorkOrderMechanic(OrgScopedModel):
    """Mecánico asignado a la orden (al crearla o diferido hasta el cierre)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        WorkOrder, on_delete=models.PROTECT, related_name="mechanics", verbose_name="orden"
    )
    mechanic = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="work_order_assignments",
        verbose_name="mecánico",
    )

    class Meta:
        db_table = "work_order_mechanic"
        verbose_name = "mecánico de orden"
        verbose_name_plural = "mecánicos de órdenes"
        constraints = [
            models.UniqueConstraint(fields=["order", "mechanic"], name="work_order_mechanic_uq"),
        ]

    def clean(self) -> None:
        super().clean()
        for field in ("order", "mechanic"):
            value = getattr(self, field, None)
            if value is not None and self.org_id and value.org_id != self.org_id:
                raise ValidationError({field: "Debe pertenecer a la organización."})
