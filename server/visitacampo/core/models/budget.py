"""Presupuestos de proyectos (módulo de costos, pieza 3).

Diseño: plans/2026-11-01-costos-pieza3-presupuesto-design.md.

- `Budget`: un presupuesto del proyecto (p. ej. por contrato o fuente de
  financiación), con el mapeo de columnas de su Excel y su versión contractual.
- `BudgetVersion`: cada Excel subido. Nace `pending` (vista previa); al
  confirmarse pasa a `applied` con su `number` consecutivo. La vigente es la
  última aplicada. Las descartadas quedan como rastro.
- `BudgetItem`: identidad estable de una fila (capítulo o ítem) por su código.
  **Nunca se borra**: lo que la pieza 4 cuelgue de aquí (homologación con
  tareas) sobrevive a todas las versiones.
- `BudgetItemVersion`: los valores de una fila en una versión.
- `BudgetCharge`: líneas de indirectos e impuestos de una versión (AIU, IVA…).

El dinero es `numeric` y `Decimal`; la aritmética vive en
`visitacampo.budgets.totals`.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models.functions import Lower

from .base import OrgScopedModel


class Budget(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="budgets", verbose_name="proyecto"
    )
    name = models.CharField(max_length=200, verbose_name="nombre")
    code = models.CharField(max_length=50, blank=True, verbose_name="código")
    description = models.TextField(blank=True, verbose_name="descripción")
    #: `{"sheet", "first_row", "columns"}`: ver `budgets.mapping.ColumnMapping`.
    #: Vacío hasta la primera lectura válida; se reutiliza en las versiones siguientes.
    column_mapping = models.JSONField(default=dict, blank=True, verbose_name="mapeo de columnas")
    #: Nula hasta la primera versión aplicada, que queda como contractual.
    contractual_version = models.ForeignKey(
        "core.BudgetVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="versión contractual",
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="creado por"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "budget"
        ordering = ["name"]
        verbose_name = "presupuesto"
        verbose_name_plural = "presupuestos"
        constraints = [
            models.UniqueConstraint(Lower("name"), "project", name="budget_project_name_ci_uq"),
        ]

    def __str__(self) -> str:
        return self.name


class BudgetVersionStatus(models.TextChoices):
    PENDING = "pending", "Pendiente de confirmar"
    APPLIED = "applied", "Aplicada"
    DISCARDED = "discarded", "Descartada"


class BudgetVersion(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    budget = models.ForeignKey(
        Budget, on_delete=models.PROTECT, related_name="versions", verbose_name="presupuesto"
    )
    #: Consecutivo por presupuesto; solo lo tienen las aplicadas.
    number = models.PositiveIntegerField(null=True, blank=True, verbose_name="versión")
    status = models.CharField(
        max_length=16,
        choices=BudgetVersionStatus.choices,
        default=BudgetVersionStatus.PENDING,
        verbose_name="estado",
    )
    label = models.CharField(max_length=200, blank=True, verbose_name="etiqueta")
    file_name = models.CharField(max_length=255, verbose_name="archivo")
    #: El `.xlsx` original (ya es un ZIP: no se comprime otra vez).
    source_file = models.BinaryField(verbose_name="Excel original")
    uploaded_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="subido por"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    #: Resumen de cambios, advertencias y el mapeo usado: ver
    #: `budgets.diff.summarize_changes` y `budgets.importer.prepare_version`.
    summary = models.JSONField(default=dict, blank=True)
    direct_cost = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, verbose_name="costo directo"
    )
    total = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "budget_version"
        ordering = ["-uploaded_at"]
        verbose_name = "versión de presupuesto"
        verbose_name_plural = "versiones de presupuesto"
        constraints = [
            models.UniqueConstraint(
                fields=["budget", "number"],
                condition=models.Q(number__isnull=False),
                name="budget_version_number_uq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.budget} v{self.number or '?'}"


class BudgetCharge(OrgScopedModel):
    """Una línea de cargos (indirectos o impuestos) de una versión."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(BudgetVersion, on_delete=models.CASCADE, related_name="charges")
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    name = models.CharField(max_length=100, verbose_name="nombre")
    pct = models.DecimalField(max_digits=7, decimal_places=4, verbose_name="porcentaje")
    #: `direct`, `subtotal` o `line:<orden>` (ver `budgets.totals`).
    base = models.CharField(max_length=20, verbose_name="base")
    #: Calculado al aplicar la versión; nulo mientras está pendiente.
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "budget_charge"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["version", "order"], name="budget_charge_order_uq"),
            models.CheckConstraint(condition=models.Q(pct__gte=0), name="budget_charge_pct_gte_0"),
        ]


class BudgetItem(OrgScopedModel):
    """Identidad estable de una fila del presupuesto, por su código."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    budget = models.ForeignKey(Budget, on_delete=models.PROTECT, related_name="items")
    code = models.CharField(max_length=50, verbose_name="código")

    class Meta:
        db_table = "budget_item"
        constraints = [
            models.UniqueConstraint(fields=["budget", "code"], name="budget_item_code_uq"),
        ]

    def __str__(self) -> str:
        return self.code


class BudgetRowKind(models.TextChoices):
    CHAPTER = "chapter", "Capítulo"
    ITEM = "item", "Ítem"


class BudgetItemVersion(OrgScopedModel):
    """Los valores de una fila (`BudgetItem`) en una versión."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(BudgetVersion, on_delete=models.CASCADE, related_name="rows")
    item = models.ForeignKey(BudgetItem, on_delete=models.PROTECT, related_name="values")
    kind = models.CharField(max_length=10, choices=BudgetRowKind.choices, verbose_name="tipo")
    #: Posición de la fila en el Excel de esta versión (0, 1, 2…).
    sort_order = models.IntegerField(verbose_name="orden")
    description = models.TextField(blank=True, verbose_name="descripción")
    unit = models.CharField(max_length=30, blank=True, verbose_name="unidad")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    #: Ítem: cantidad × valor unitario a centavos. Capítulo: suma de sus hijos.
    total = models.DecimalField(max_digits=18, decimal_places=2)
    #: Capítulo que contiene la fila en esta versión; nulo en el primer nivel.
    parent = models.ForeignKey(
        BudgetItem, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "budget_item_version"
        ordering = ["sort_order"]
        constraints = [
            models.UniqueConstraint(fields=["version", "item"], name="budget_item_version_uq"),
            models.CheckConstraint(
                condition=(
                    models.Q(kind="item", quantity__isnull=False, unit_price__isnull=False)
                    | models.Q(kind="chapter", quantity__isnull=True, unit_price__isnull=True)
                ),
                name="budget_item_version_kind_values",
            ),
        ]
        indexes = [
            models.Index(fields=["version", "sort_order"], name="budget_row_version_order_idx"),
        ]
