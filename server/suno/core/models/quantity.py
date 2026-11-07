"""Matriz planeada (costos, pieza 4): ítem del presupuesto × tarea → cantidad.

Cuelga de `Task` y `BudgetItem`, que son estables entre versiones, así que
sobrevive a nuevas importaciones del cronograma y del presupuesto.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from .base import OrgScopedModel


class QuantityPlan(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("core.Project", on_delete=models.PROTECT, related_name="+")
    task = models.ForeignKey("core.Task", on_delete=models.PROTECT, related_name="quantity_plans")
    item = models.ForeignKey(
        "core.BudgetItem", on_delete=models.PROTECT, related_name="quantity_plans"
    )
    planned_quantity = models.DecimalField(
        max_digits=18, decimal_places=4, verbose_name="cantidad planeada"
    )
    updated_by = models.ForeignKey("core.AppUser", on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "quantity_plan"
        constraints = [
            models.UniqueConstraint(fields=["task", "item"], name="quantity_plan_pair_uq"),
            models.CheckConstraint(
                condition=models.Q(planned_quantity__gte=0),
                name="quantity_plan_not_negative",
            ),
        ]


class QuantityPlanImportStatus(models.TextChoices):
    PENDING = "pending", "Pendiente"
    APPLIED = "applied", "Aplicada"
    DISCARDED = "discarded", "Descartada"


class QuantityPlanImport(OrgScopedModel):
    """Una carga de la plantilla de la matriz, en dos pasos (como el presupuesto)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="quantity_plan_imports"
    )
    #: Consecutivo por proyecto; solo las aplicadas.
    number = models.PositiveIntegerField(null=True, blank=True, verbose_name="versión")
    status = models.CharField(
        max_length=10,
        choices=QuantityPlanImportStatus.choices,
        default=QuantityPlanImportStatus.PENDING,
    )
    file_name = models.CharField(max_length=255, verbose_name="archivo")
    source_file = models.BinaryField(verbose_name="archivo original")
    #: Diferencias, totales por ítem, avisos y la base sobre la que se calculó.
    summary = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey("core.AppUser", on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "quantity_plan_import"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(status="applied", number__isnull=False)
                    | (~models.Q(status="applied") & models.Q(number__isnull=True))
                ),
                name="quantity_plan_import_number_iff_applied",
            ),
            models.UniqueConstraint(
                fields=["project", "number"], name="quantity_plan_import_number_uq"
            ),
        ]
