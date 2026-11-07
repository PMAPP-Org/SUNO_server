"""Catálogo, ficha técnica y asignaciones iniciales de equipos (Hito B)."""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

from .base import OrgScopedModel


class EquipmentStatus(models.TextChoices):
    AVAILABLE = "available", "Disponible"
    ASSIGNED = "assigned", "Asignado"
    MAINTENANCE = "maintenance", "En mantenimiento"
    OUT_OF_SERVICE = "out_of_service", "Fuera de servicio"
    RETIRED = "retired", "Retirado"


class EquipmentOwnership(models.TextChoices):
    OWN = "own", "Propio"
    RENTED = "rented", "Alquilado"
    CONTRACTOR = "contractor", "Contratista"


class EquipmentDocumentType(models.TextChoices):
    REGISTRATION = "registration", "Matrícula o registro"
    INSURANCE = "insurance", "Seguro"
    TECHNICAL_REVIEW = "technical_review", "Revisión técnica"
    OPERATION_CERTIFICATE = "operation_certificate", "Certificado de operación"
    MANUAL = "manual", "Manual"
    OTHER = "other", "Otro"


class EquipmentUsageStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Programado"
    IN_PROGRESS = "in_progress", "En curso"
    COMPLETED = "completed", "Completado"
    CANCELLED = "cancelled", "Cancelado"


class EquipmentMeterUnit(models.TextChoices):
    HOURS = "hours", "Horómetro"
    KILOMETERS = "kilometers", "Kilometraje"


class EquipmentType(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=40, verbose_name="código")
    name = models.CharField(max_length=120, verbose_name="nombre")
    description = models.TextField(blank=True, verbose_name="descripción")
    preoperational_template = models.ForeignKey(
        "core.FormTemplate",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="equipment_types",
        verbose_name="formulario preoperacional",
    )
    active = models.BooleanField(default=True, verbose_name="activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "equipment_type"
        ordering = ["name"]
        verbose_name = "tipo de equipo"
        verbose_name_plural = "tipos de equipo"
        constraints = [
            models.UniqueConstraint(Lower("code"), "org", name="equipment_type_org_code_ci_uq"),
            models.UniqueConstraint(Lower("name"), "org", name="equipment_type_org_name_ci_uq"),
        ]

    def clean(self) -> None:
        super().clean()
        if (
            self.preoperational_template_id
            and self.org_id
            and self.preoperational_template.org_id != self.org_id
        ):
            raise ValidationError(
                {"preoperational_template": "El formulario debe pertenecer a la organización."}
            )

    def __str__(self) -> str:
        return self.name


class Equipment(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, verbose_name="código interno")
    name = models.CharField(max_length=160, verbose_name="nombre")
    equipment_type = models.ForeignKey(
        EquipmentType,
        on_delete=models.PROTECT,
        related_name="equipment",
        verbose_name="tipo",
    )
    manufacturer = models.CharField(max_length=120, blank=True, verbose_name="fabricante")
    model = models.CharField(max_length=120, blank=True, verbose_name="modelo")
    serial_number = models.CharField(max_length=120, blank=True, verbose_name="serie")
    license_plate = models.CharField(max_length=40, blank=True, verbose_name="placa")
    manufacture_year = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="año de fabricación"
    )
    ownership = models.CharField(
        max_length=16,
        choices=EquipmentOwnership.choices,
        default=EquipmentOwnership.OWN,
        verbose_name="propiedad",
    )
    owner_name = models.CharField(max_length=160, blank=True, verbose_name="propietario")
    status = models.CharField(
        max_length=20,
        choices=EquipmentStatus.choices,
        default=EquipmentStatus.AVAILABLE,
        verbose_name="estado",
    )
    specifications = models.JSONField(default=dict, blank=True, verbose_name="especificaciones")
    notes = models.TextField(blank=True, verbose_name="observaciones")
    created_by = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="equipment_created",
        verbose_name="creado por",
    )
    updated_by = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="equipment_updated",
        verbose_name="actualizado por",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "equipment"
        ordering = ["code"]
        verbose_name = "equipo"
        verbose_name_plural = "equipos"
        constraints = [
            models.UniqueConstraint(Lower("code"), "org", name="equipment_org_code_ci_uq"),
            models.UniqueConstraint(
                Lower("serial_number"),
                "org",
                condition=~models.Q(serial_number=""),
                name="equipment_org_serial_ci_uq",
            ),
            models.UniqueConstraint(
                Lower("license_plate"),
                "org",
                condition=~models.Q(license_plate=""),
                name="equipment_org_plate_ci_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "status"], name="equipment_org_status_idx"),
            models.Index(fields=["org", "equipment_type"], name="equipment_org_type_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.equipment_type_id and self.org_id and self.equipment_type.org_id != self.org_id:
            errors["equipment_type"] = "El tipo debe pertenecer a la organización."
        for field in ("created_by", "updated_by"):
            user = getattr(self, field, None)
            if user is not None and self.org_id and user.org_id != self.org_id:
                errors[field] = "El usuario debe pertenecer a la organización."
        if self.ownership != EquipmentOwnership.OWN and not self.owner_name.strip():
            errors["owner_name"] = "Indica el propietario del equipo no propio."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.code} · {self.name}"


class EquipmentDocument(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.PROTECT,
        related_name="documents",
        verbose_name="equipo",
    )
    document_type = models.CharField(
        max_length=32, choices=EquipmentDocumentType.choices, verbose_name="tipo"
    )
    name = models.CharField(max_length=160, verbose_name="nombre")
    number = models.CharField(max_length=80, blank=True, verbose_name="número")
    issued_on = models.DateField(null=True, blank=True, verbose_name="emisión")
    expires_on = models.DateField(null=True, blank=True, verbose_name="vencimiento")
    blob = models.ForeignKey(
        "core.Blob",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="equipment_documents",
        verbose_name="archivo",
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="equipment_documents_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "equipment_document"
        ordering = ["equipment__code", "document_type", "name"]
        verbose_name = "documento de equipo"
        verbose_name_plural = "documentos de equipos"
        indexes = [
            models.Index(fields=["org", "expires_on"], name="equipment_doc_expiry_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(issued_on__isnull=True)
                | models.Q(expires_on__isnull=True)
                | models.Q(expires_on__gte=models.F("issued_on")),
                name="equipment_doc_dates_ordered",
            )
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.equipment_id and self.org_id and self.equipment.org_id != self.org_id:
            errors["equipment"] = "El equipo debe pertenecer a la organización."
        if self.created_by_id and self.org_id and self.created_by.org_id != self.org_id:
            errors["created_by"] = "El usuario debe pertenecer a la organización."
        if self.issued_on and self.expires_on and self.expires_on < self.issued_on:
            errors["expires_on"] = "El vencimiento no puede ser anterior a la emisión."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.equipment.code}:{self.name}"


class EquipmentProject(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment = models.ForeignKey(
        Equipment, on_delete=models.PROTECT, related_name="project_links", verbose_name="equipo"
    )
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="equipment_links",
        verbose_name="proyecto",
    )
    started_on = models.DateField(verbose_name="inicio")
    ended_on = models.DateField(null=True, blank=True, verbose_name="fin")
    assigned_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="equipment_projects_assigned"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "equipment_project"
        ordering = ["-started_on"]
        verbose_name = "proyecto de equipo"
        verbose_name_plural = "proyectos de equipos"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ended_on__isnull=True)
                | models.Q(ended_on__gte=models.F("started_on")),
                name="equipment_project_dates_ordered",
            ),
            models.UniqueConstraint(
                fields=["equipment"],
                condition=models.Q(ended_on__isnull=True),
                name="equipment_project_active_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "project", "ended_on"], name="equipment_project_scope_idx")
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        for field in ("equipment", "project", "assigned_by"):
            value = getattr(self, field, None)
            if value is not None and self.org_id and value.org_id != self.org_id:
                errors[field] = "Debe pertenecer a la organización."
        if self.ended_on and self.ended_on < self.started_on:
            errors["ended_on"] = "La fecha final no puede ser anterior al inicio."
        if errors:
            raise ValidationError(errors)


class EquipmentOperatorAssignment(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.PROTECT,
        related_name="operator_assignments",
        verbose_name="equipo",
    )
    operator = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="equipment_operator_assignments",
        verbose_name="operador",
    )
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="equipment_operator_assignments",
        verbose_name="proyecto",
    )
    valid_from = models.DateField(verbose_name="vigente desde")
    valid_until = models.DateField(null=True, blank=True, verbose_name="vigente hasta")
    assigned_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="equipment_operators_assigned"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "equipment_operator_assignment"
        ordering = ["-valid_from"]
        verbose_name = "asignación de operador"
        verbose_name_plural = "asignaciones de operadores"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valid_until__isnull=True)
                | models.Q(valid_until__gte=models.F("valid_from")),
                name="equipment_operator_dates_ordered",
            ),
            models.UniqueConstraint(
                fields=["equipment"],
                condition=models.Q(valid_until__isnull=True),
                name="equipment_operator_active_uq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["org", "operator", "valid_until"],
                name="equipment_operator_user_idx",
            ),
            models.Index(
                fields=["org", "project", "valid_until"],
                name="equipment_operator_project_idx",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        for field in ("equipment", "operator", "project", "assigned_by"):
            value = getattr(self, field, None)
            if value is not None and self.org_id and value.org_id != self.org_id:
                errors[field] = "Debe pertenecer a la organización."
        if self.valid_until and self.valid_until < self.valid_from:
            errors["valid_until"] = "La fecha final no puede ser anterior al inicio."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.operator_id}:{self.equipment_id}"


class EquipmentUsage(OrgScopedModel):
    """Uso programado o ejecutado de un equipo durante una jornada."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.PROTECT,
        related_name="usage_records",
        verbose_name="equipo",
    )
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="equipment_usage_records",
        verbose_name="proyecto",
    )
    operator = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="equipment_usage_records",
        verbose_name="operador",
    )
    usage_date = models.DateField(verbose_name="fecha")
    start_time = models.TimeField(verbose_name="hora de inicio")
    end_time = models.TimeField(verbose_name="hora de fin")
    activity = models.CharField(max_length=200, verbose_name="actividad")
    location = models.CharField(max_length=200, blank=True, verbose_name="ubicación o frente")
    status = models.CharField(
        max_length=16,
        choices=EquipmentUsageStatus.choices,
        default=EquipmentUsageStatus.SCHEDULED,
        verbose_name="estado",
    )
    meter_unit = models.CharField(
        max_length=16,
        choices=EquipmentMeterUnit.choices,
        default=EquipmentMeterUnit.HOURS,
        verbose_name="tipo de medidor",
    )
    start_meter = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="lectura inicial",
    )
    end_meter = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="lectura final",
    )
    notes = models.TextField(blank=True, verbose_name="observaciones")
    created_by = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="equipment_usage_created",
        verbose_name="creado por",
    )
    updated_by = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="equipment_usage_updated",
        verbose_name="actualizado por",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "equipment_usage"
        ordering = ["-usage_date", "-start_time"]
        verbose_name = "registro de uso de equipo"
        verbose_name_plural = "registros de uso de equipos"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="equipment_usage_time_ordered",
            ),
            models.CheckConstraint(
                condition=models.Q(start_meter__isnull=True)
                | models.Q(end_meter__isnull=True)
                | models.Q(end_meter__gte=models.F("start_meter")),
                name="equipment_usage_meter_ordered",
            ),
        ]
        indexes = [
            models.Index(
                fields=["org", "usage_date", "status"],
                name="equipment_usage_day_idx",
            ),
            models.Index(
                fields=["org", "equipment", "usage_date"],
                name="equipment_usage_equip_idx",
            ),
            models.Index(
                fields=["org", "operator", "usage_date"],
                name="equipment_usage_user_idx",
            ),
            models.Index(
                fields=["org", "project", "usage_date"],
                name="equipment_usage_proj_idx",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        for field in (
            "equipment",
            "project",
            "operator",
            "created_by",
            "updated_by",
        ):
            value = getattr(self, field, None)
            if value is not None and self.org_id and value.org_id != self.org_id:
                errors[field] = "Debe pertenecer a la organización."
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            errors["end_time"] = "La hora final debe ser posterior a la hora de inicio."
        if (
            self.start_meter is not None
            and self.end_meter is not None
            and self.end_meter < self.start_meter
        ):
            errors["end_meter"] = "La lectura final no puede ser menor que la inicial."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.usage_date}:{self.equipment.code}:{self.start_time}"
