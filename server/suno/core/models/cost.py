"""Libro de costos del proyecto (costos, pieza 6a).

Diseño: plans/2026-11-12-costos-pieza6a-costo-real-design.md, sección 1.

- `CostEntry`: un movimiento del libro. Nace en borrador; se aprueba o se rechaza (con
  motivo). Solo cuenta lo aprobado. Lo aprobado no se edita ni se borra: se reversa con otro
  movimiento, aprobado y de signo contrario, en la fecha del reverso.
- `CostEntryItem`: los ítems de un movimiento `item` (uno) o `selection` (dos o más del mismo
  capítulo), por su identidad estable (`BudgetItem`), no por versión.

El valor lleva IVA incluido, como los precios del presupuesto.
"""

from __future__ import annotations

import uuid

from django.db import models

from .base import OrgScopedModel


class CostKind(models.TextChoices):
    MATERIALS = "materials", "Materiales"
    LABOR = "labor", "Mano de obra"
    PAYROLL = "payroll", "Nómina"
    SUBCONTRACT = "subcontract", "Subcontrato"
    RENTED_EQUIPMENT = "rented_equipment", "Equipo alquilado"
    OTHER = "other", "Otros gastos"


class CostDocumentType(models.TextChoices):
    INVOICE = "invoice", "Factura"
    COLLECTION_ACCOUNT = "collection_account", "Cuenta de cobro"
    PAYROLL = "payroll", "Nómina"
    RECEIPT = "receipt", "Recibo"
    OTHER = "other", "Otro"


class CostScope(models.TextChoices):
    INDIRECT = "indirect", "Indirecto (proyecto)"
    ITEM = "item", "Un ítem"
    CHAPTER = "chapter", "Un capítulo completo"
    SELECTION = "selection", "Ítems de un capítulo"


class CostStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    APPROVED = "approved", "Aprobado"
    REJECTED = "rejected", "Rechazado"


class CostEntry(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="cost_entries",
        verbose_name="proyecto",
    )
    #: Fecha de causación: define el periodo del control.
    incurred_on = models.DateField(verbose_name="fecha de causación")
    kind = models.CharField(max_length=24, choices=CostKind.choices, verbose_name="tipo")
    description = models.CharField(max_length=300, verbose_name="descripción")
    #: Con IVA incluido. Negativo solo en un reverso.
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")
    third_party = models.CharField(max_length=200, blank=True, verbose_name="tercero")
    document_type = models.CharField(
        max_length=24, choices=CostDocumentType.choices, verbose_name="tipo de documento"
    )
    document_number = models.CharField(max_length=80, blank=True, verbose_name="número")
    scope = models.CharField(max_length=12, choices=CostScope.choices, verbose_name="asignación")
    #: `chapter`/`selection`: el capítulo asignado. `item`: el capítulo padre del ítem en la
    #: versión vigente al registrarlo (su capítulo de origen). `indirect`: nulo.
    chapter = models.ForeignKey(
        "core.BudgetItem",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="capítulo",
    )
    items = models.ManyToManyField(
        "core.BudgetItem",
        through="CostEntryItem",
        related_name="cost_entries",
        verbose_name="ítems",
    )
    task = models.ForeignKey(
        "core.Task",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cost_entries",
        verbose_name="tarea",
    )
    status = models.CharField(
        max_length=10,
        choices=CostStatus.choices,
        default=CostStatus.DRAFT,
        verbose_name="estado",
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="registró"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="modificó"
    )
    updated_at = models.DateTimeField(auto_now=True)
    decided_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="aprobó o rechazó",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    #: Motivo del rechazo o del reverso.
    decision_note = models.CharField(max_length=500, blank=True, verbose_name="motivo")
    #: El movimiento que este anula. Uno a uno: un movimiento se reversa una sola vez.
    reverses = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reversal",
        verbose_name="reverso de",
    )
    #: Soporte (módulo de adjuntos). Obligatorio para aprobar; un reverso no lleva.
    attachment = models.ForeignKey(
        "core.Blob",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cost_entries",
        verbose_name="soporte",
    )
    attachment_name = models.CharField(max_length=200, blank=True, verbose_name="archivo")

    class Meta:
        db_table = "cost_entry"
        ordering = ["-incurred_on", "-created_at"]
        verbose_name = "movimiento de costo"
        verbose_name_plural = "movimientos de costo"
        indexes = [
            models.Index(fields=["org", "project", "incurred_on"], name="cost_entry_day_idx"),
            models.Index(fields=["org", "project", "status"], name="cost_entry_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reverses__isnull=True, amount__gt=0)
                | models.Q(reverses__isnull=False, amount__lt=0),
                name="cost_entry_amount_sign",
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft", decided_by__isnull=True, decided_at__isnull=True)
                | models.Q(
                    status__in=["approved", "rejected"],
                    decided_by__isnull=False,
                    decided_at__isnull=False,
                ),
                name="cost_entry_decision_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="approved")
                | models.Q(reverses__isnull=False)
                | (
                    models.Q(attachment__isnull=False)
                    & ~models.Q(decided_by=models.F("created_by"))
                ),
                name="cost_entry_approval_ok",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="rejected") | ~models.Q(decision_note=""),
                name="cost_entry_rejection_note",
            ),
            models.CheckConstraint(
                condition=models.Q(reverses__isnull=True) | models.Q(status="approved"),
                name="cost_entry_reversal_approved",
            ),
            models.CheckConstraint(
                condition=models.Q(scope="indirect", chapter__isnull=True, task__isnull=True)
                | models.Q(scope="item")
                | models.Q(scope__in=["chapter", "selection"], chapter__isnull=False),
                name="cost_entry_scope_fields",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.incurred_on}:{self.description}"


class CostEntryItem(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey(CostEntry, on_delete=models.CASCADE, related_name="item_links")
    item = models.ForeignKey("core.BudgetItem", on_delete=models.PROTECT, related_name="cost_links")

    class Meta:
        db_table = "cost_entry_item"
        constraints = [
            models.UniqueConstraint(fields=["entry", "item"], name="cost_entry_item_uq"),
        ]
