"""Preoperacionales de equipos (M&E fase 2): lista de chequeo, inspección y novedades.

La inspección es **evidencia**: una vez enviada no se edita. La revisión añade su
decisión (campos `review_*`) pero nunca reescribe lo capturado; una corrección es
una inspección nueva.
"""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

from .base import OrgScopedModel


class InspectionStatus(models.TextChoices):
    SUBMITTED = "submitted", "Pendiente de revisión"
    APPROVED = "approved", "Aprobada"
    REJECTED = "rejected", "Rechazada"
    CORRECTION_REQUESTED = "correction_requested", "Corrección solicitada"


class FaultStatus(models.TextChoices):
    OPEN = "open", "Abierta"
    CLOSED = "closed", "Cerrada"


class PreoperationalItem(OrgScopedModel):
    """Ítem de la lista de chequeo de una tipología de equipo."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment_type = models.ForeignKey(
        "core.EquipmentType",
        on_delete=models.PROTECT,
        related_name="preoperational_items",
        verbose_name="tipología",
    )
    label = models.CharField(max_length=200, verbose_name="ítem a revisar")
    critical = models.BooleanField(default=False, verbose_name="crítico")
    position = models.PositiveSmallIntegerField(default=0, verbose_name="orden")
    active = models.BooleanField(default=True, verbose_name="activo")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "preoperational_item"
        ordering = ["position", "label"]
        verbose_name = "ítem preoperacional"
        verbose_name_plural = "ítems preoperacionales"
        constraints = [
            models.UniqueConstraint(
                Lower("label"), "equipment_type", name="preoperational_item_type_label_uq"
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.equipment_type_id and self.org_id and self.equipment_type.org_id != self.org_id:
            raise ValidationError({"equipment_type": "La tipología debe ser de la organización."})

    def __str__(self) -> str:
        return self.label


class EquipmentInspection(OrgScopedModel):
    """Inspección preoperacional enviada. El `id` lo genera el teléfono (idempotencia)."""

    id = models.UUIDField(primary_key=True, editable=False)
    equipment = models.ForeignKey(
        "core.Equipment",
        on_delete=models.PROTECT,
        related_name="inspections",
        verbose_name="equipo",
    )
    operator = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="equipment_inspections",
        verbose_name="operador",
    )
    project = models.ForeignKey(
        "core.Project",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="equipment_inspections",
        verbose_name="proyecto",
    )
    captured_at = models.DateTimeField(verbose_name="capturada el")
    submitted_at = models.DateTimeField(auto_now_add=True, verbose_name="recibida el")
    meter_unit = models.CharField(max_length=16, blank=True, verbose_name="medidor")
    meter_value = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="lectura inicial"
    )
    # Instantánea de la lista tal como se respondió: [{item_id,label,critical,ok,note}].
    items = models.JSONField(default=list, verbose_name="respuestas")
    notes = models.TextField(blank=True, verbose_name="observaciones")
    has_faults = models.BooleanField(default=False, verbose_name="con novedades")
    status = models.CharField(
        max_length=24,
        choices=InspectionStatus.choices,
        default=InspectionStatus.SUBMITTED,
        verbose_name="estado",
    )
    reviewed_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="equipment_inspections_reviewed",
        verbose_name="revisada por",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="revisada el")
    review_comment = models.TextField(blank=True, verbose_name="comentario de revisión")

    class Meta:
        db_table = "equipment_inspection"
        ordering = ["-captured_at"]
        verbose_name = "inspección preoperacional"
        verbose_name_plural = "inspecciones preoperacionales"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status="submitted", reviewed_by__isnull=True)
                | (~models.Q(status="submitted") & models.Q(reviewed_by__isnull=False)),
                name="equipment_inspection_review_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "status", "-captured_at"], name="eq_inspection_status_idx"),
            models.Index(
                fields=["org", "equipment", "-captured_at"], name="eq_inspection_equip_idx"
            ),
            models.Index(fields=["org", "operator", "-captured_at"], name="eq_inspection_user_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        for field in ("equipment", "operator", "project", "reviewed_by"):
            value = getattr(self, field, None)
            if value is not None and self.org_id and value.org_id != self.org_id:
                errors[field] = "Debe pertenecer a la organización."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.equipment_id}:{self.captured_at:%Y-%m-%d %H:%M}"


class EquipmentFault(OrgScopedModel):
    """Novedad detectada en una inspección; se sigue hasta cerrarla."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection = models.ForeignKey(
        EquipmentInspection,
        on_delete=models.PROTECT,
        related_name="faults",
        verbose_name="inspección",
    )
    equipment = models.ForeignKey(
        "core.Equipment",
        on_delete=models.PROTECT,
        related_name="faults",
        verbose_name="equipo",
    )
    item_label = models.CharField(max_length=200, verbose_name="ítem")
    critical = models.BooleanField(default=False, verbose_name="crítico")
    description = models.TextField(blank=True, verbose_name="novedad reportada")
    status = models.CharField(
        max_length=8,
        choices=FaultStatus.choices,
        default=FaultStatus.OPEN,
        verbose_name="estado",
    )
    closed_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="equipment_faults_closed",
        verbose_name="cerrada por",
    )
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="cerrada el")
    closing_note = models.TextField(blank=True, verbose_name="nota de cierre")
    # Orden de trabajo que atiende la novedad; al cerrarla técnicamente se cierra también.
    work_order = models.ForeignKey(
        "core.WorkOrder",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="faults",
        verbose_name="orden de trabajo",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "equipment_fault"
        ordering = ["-created_at"]
        verbose_name = "novedad de equipo"
        verbose_name_plural = "novedades de equipos"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status="open", closed_by__isnull=True)
                | (models.Q(status="closed") & models.Q(closed_by__isnull=False)),
                name="equipment_fault_closure_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "status", "-created_at"], name="eq_fault_status_idx"),
            models.Index(fields=["org", "equipment", "status"], name="eq_fault_equipment_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.equipment_id}:{self.item_label}"
