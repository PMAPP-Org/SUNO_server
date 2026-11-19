"""Mano de obra por jornal: registro de trabajadores, tareo y nómina importada (costos,
pieza 6d).

Diseño: plans/2026-11-18-costos-pieza6d-tareo-design.md, sección 1.

- `Worker`: trabajador de la organización con datos personales mínimos (Ley 1581): documento,
  nombre, cargo, ingreso y retiro. Nada de dirección, teléfono, correo, cuenta, EPS ni fondo.
- `WorkerWage` y `BenefitFactor`: jornal básico por trabajador y factor prestacional de la
  organización, con vigencia ("desde"); el vigente un día es el de mayor "desde" ≤ ese día.
- `ProjectWorker`: asignación de un trabajador a un proyecto entre dos fechas.
- `Timesheet` (TA-001) y `TimesheetLine`: planilla de tareo por periodo; cada línea es la
  fracción de jornal de un trabajador un día en un destino de la 6a. Al aprobar se congelan
  jornal, factor y valor y se escribe un costo de Mano de obra por destino.
- `PayrollImport` y `PayrollImportLine`: la nómina real del periodo, solo para conciliar.
- `LaborImportToken`: nonce de un solo uso de las tres importaciones (ADR-0036).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django.db import models

from .base import OrgScopedModel
from .cost import CostScope

#: Fracción máxima de una línea y tope de un trabajador en un día (spec 1.2 y 2.2).
MAX_FRACTION = Decimal("1.5")
#: Un periodo de 31 días como mucho: el fin no pasa del inicio + 30.
MAX_PERIOD_DAYS = 31


class DocumentType(models.TextChoices):
    CC = "CC", "Cédula de ciudadanía"
    CE = "CE", "Cédula de extranjería"
    PPT = "PPT", "Permiso por protección temporal"
    PEP = "PEP", "Permiso especial de permanencia"
    PA = "PA", "Pasaporte"


class TimesheetStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    APPROVED = "approved", "Aprobada"
    REJECTED = "rejected", "Rechazada"
    REVERSED = "reversed", "Anulada"


class LaborImportKind(models.TextChoices):
    WORKERS = "workers", "Trabajadores"
    TIMESHEET = "timesheet", "Tareo"
    PAYROLL = "payroll", "Nómina"


def mask_document(number: str) -> str:
    """`***4567`: el documento en listas, planillas, PDF y Excel (spec 3.6)."""
    return f"***{number[-4:]}" if number else ""


class Worker(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_type = models.CharField(
        max_length=4, choices=DocumentType.choices, verbose_name="tipo de documento"
    )
    #: Limpio: sin puntos, espacios ni guiones, en mayúsculas (`labor.registry.clean_document`).
    document_number = models.CharField(max_length=20, verbose_name="número de documento")
    full_name = models.CharField(max_length=200, verbose_name="nombre")
    position = models.CharField(max_length=80, blank=True, verbose_name="cargo")
    hired_on = models.DateField(verbose_name="fecha de ingreso")
    retired_on = models.DateField(null=True, blank=True, verbose_name="fecha de retiro")
    is_active = models.BooleanField(default=True, verbose_name="activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "worker"
        ordering = ["full_name"]
        verbose_name = "trabajador"
        verbose_name_plural = "trabajadores"
        constraints = [
            models.UniqueConstraint(
                fields=["org", "document_type", "document_number"], name="worker_document_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(retired_on__isnull=True)
                | models.Q(retired_on__gte=models.F("hired_on")),
                name="worker_retired_after_hired",
            ),
            models.CheckConstraint(
                condition=models.Q(is_active=True, retired_on__isnull=True)
                | models.Q(is_active=False, retired_on__isnull=False),
                name="worker_active_retired",
            ),
        ]
        indexes = [models.Index(fields=["org", "position"], name="worker_position_idx")]

    @property
    def masked_document(self) -> str:
        return f"{self.document_type} {mask_document(self.document_number)}"

    @property
    def label(self) -> str:
        """ "Pedro Pérez · CC ***5678": como se muestra en listas, planillas, PDF y Excel."""
        return f"{self.full_name} · {self.masked_document}"

    def __str__(self) -> str:
        return self.label


class WorkerWage(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    worker = models.ForeignKey(
        Worker, on_delete=models.CASCADE, related_name="wages", verbose_name="trabajador"
    )
    valid_from = models.DateField(verbose_name="desde")
    daily_wage = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="jornal básico")
    created_by = models.ForeignKey(
        "core.AppUser", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "worker_wage"
        ordering = ["worker", "valid_from"]
        verbose_name = "jornal"
        verbose_name_plural = "jornales"
        constraints = [
            models.UniqueConstraint(fields=["worker", "valid_from"], name="worker_wage_day_uq"),
            models.CheckConstraint(
                condition=models.Q(daily_wage__gt=0), name="worker_wage_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.worker_id}:{self.valid_from}"


class BenefitFactor(OrgScopedModel):
    """Prestaciones sociales, seguridad social, parafiscales y dotación sobre el jornal."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    valid_from = models.DateField(verbose_name="desde")
    factor = models.DecimalField(max_digits=6, decimal_places=4, verbose_name="factor")
    note = models.CharField(max_length=500, blank=True, verbose_name="composición")
    created_by = models.ForeignKey(
        "core.AppUser", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "benefit_factor"
        ordering = ["valid_from"]
        verbose_name = "factor prestacional"
        verbose_name_plural = "factores prestacionales"
        constraints = [
            models.UniqueConstraint(fields=["org", "valid_from"], name="benefit_factor_day_uq"),
            models.CheckConstraint(
                condition=models.Q(factor__gte=1, factor__lte=3), name="benefit_factor_range"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.valid_from}:{self.factor}"


class ProjectWorker(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="labor_assignments",
        verbose_name="proyecto",
    )
    worker = models.ForeignKey(
        Worker, on_delete=models.PROTECT, related_name="assignments", verbose_name="trabajador"
    )
    starts_on = models.DateField(verbose_name="desde")
    ends_on = models.DateField(null=True, blank=True, verbose_name="hasta")
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="registró"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="modificó"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "project_worker"
        ordering = ["worker__full_name", "starts_on"]
        verbose_name = "asignación de trabajador"
        verbose_name_plural = "asignaciones de trabajadores"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_on__isnull=True)
                | models.Q(ends_on__gte=models.F("starts_on")),
                name="project_worker_period_order",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "worker", "starts_on"], name="project_worker_idx")
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.worker_id}:{self.starts_on}"


class Timesheet(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="timesheets", verbose_name="proyecto"
    )
    number = models.PositiveIntegerField(verbose_name="número")
    starts_on = models.DateField(verbose_name="desde")
    ends_on = models.DateField(verbose_name="hasta")
    status = models.CharField(
        max_length=10,
        choices=TimesheetStatus.choices,
        default=TimesheetStatus.DRAFT,
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
    decision_note = models.CharField(max_length=500, blank=True, verbose_name="motivo")
    reversed_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="anuló",
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    reverse_note = models.CharField(max_length=500, blank=True, verbose_name="motivo de anulación")
    #: La planilla firmada (maestro y residente). Obligatoria para aprobar.
    attachment = models.ForeignKey(
        "core.Blob",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="planilla firmada",
    )
    attachment_name = models.CharField(max_length=200, blank=True, verbose_name="archivo")
    #: `fingerprint` (lo que se compara al aprobar) y `groups` (el resumen por destino que
    #: imprime el PDF), guardados en cada edición (`suno.labor.calc.Summary.snapshot`).
    summary = models.JSONField(default=dict, blank=True, verbose_name="resumen")
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="costo estimado"
    )
    days = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0"), verbose_name="jornales"
    )
    line_count = models.PositiveIntegerField(default=0, verbose_name="líneas")
    #: Sube en cada edición (guardar, importar, recalcular): control de concurrencia optimista
    #: de la cuadrícula y la versión a la que se ata el soporte firmado (código en el PDF).
    version = models.PositiveIntegerField(default=1, verbose_name="versión")

    class Meta:
        db_table = "timesheet"
        ordering = ["-number"]
        verbose_name = "planilla de tareo"
        verbose_name_plural = "planillas de tareo"
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="timesheet_number_uq"),
            models.CheckConstraint(
                condition=models.Q(ends_on__gte=models.F("starts_on")),
                name="timesheet_period_order",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    ends_on__lte=models.F("starts_on") + timedelta(days=MAX_PERIOD_DAYS - 1)
                ),
                name="timesheet_period_max_31",
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft", decided_by__isnull=True, decided_at__isnull=True)
                | models.Q(
                    status__in=["approved", "rejected", "reversed"],
                    decided_by__isnull=False,
                    decided_at__isnull=False,
                ),
                name="timesheet_decision_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(status__in=["approved", "reversed"])
                | (
                    models.Q(attachment__isnull=False)
                    & ~models.Q(decided_by=models.F("created_by"))
                    & ~models.Q(decided_by=models.F("updated_by"))
                ),
                name="timesheet_approval_ok",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="rejected") | ~models.Q(decision_note=""),
                name="timesheet_rejection_note",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="reversed")
                | (
                    models.Q(reversed_by__isnull=False, reversed_at__isnull=False)
                    & ~models.Q(reverse_note="")
                ),
                name="timesheet_reverse_note",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "project", "status"], name="timesheet_status_idx"),
            models.Index(fields=["project", "starts_on", "ends_on"], name="timesheet_period_idx"),
        ]

    def __str__(self) -> str:
        return self.code

    @property
    def code(self) -> str:
        return f"TA-{self.number:03d}"


class TimesheetLine(OrgScopedModel):
    """La fracción de jornal de un trabajador un día en un destino (el de la 6a)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timesheet = models.ForeignKey(
        Timesheet, on_delete=models.CASCADE, related_name="lines", verbose_name="planilla"
    )
    worker = models.ForeignKey(
        Worker, on_delete=models.PROTECT, related_name="timesheet_lines", verbose_name="trabajador"
    )
    worked_on = models.DateField(verbose_name="día")
    fraction = models.DecimalField(max_digits=6, decimal_places=4, verbose_name="fracción")
    scope = models.CharField(max_length=12, choices=CostScope.choices, verbose_name="destino")
    #: `chapter`/`selection`: el capítulo. `item`: el capítulo padre del ítem (su origen).
    chapter = models.ForeignKey(
        "core.BudgetItem",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="capítulo",
    )
    task = models.ForeignKey(
        "core.Task",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="tarea",
    )
    items = models.ManyToManyField(
        "core.BudgetItem",
        through="TimesheetLineItem",
        related_name="timesheet_lines",
        verbose_name="ítems",
    )
    #: SHA-256 de `materials.destination.key_text`: identifica el destino en la unicidad.
    destination_key = models.CharField(max_length=64, verbose_name="clave del destino")
    #: Congelados al aprobar; nulos en un borrador (decisión 15 del plan).
    daily_wage = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="jornal"
    )
    factor = models.DecimalField(
        max_digits=6, decimal_places=4, null=True, blank=True, verbose_name="factor"
    )
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, verbose_name="valor"
    )

    class Meta:
        db_table = "timesheet_line"
        ordering = ["worked_on"]
        verbose_name = "línea de tareo"
        verbose_name_plural = "líneas de tareo"
        constraints = [
            models.UniqueConstraint(
                fields=["timesheet", "worker", "worked_on", "destination_key"],
                name="timesheet_line_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(fraction__gt=0, fraction__lte=MAX_FRACTION),
                name="timesheet_line_fraction",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    daily_wage__isnull=True, factor__isnull=True, amount__isnull=True
                )
                | models.Q(
                    daily_wage__isnull=False,
                    factor__isnull=False,
                    amount__isnull=False,
                    daily_wage__gt=0,
                    factor__gt=0,
                    amount__gt=0,
                ),
                name="timesheet_line_frozen",
            ),
            models.CheckConstraint(
                condition=models.Q(scope="indirect", chapter__isnull=True, task__isnull=True)
                | models.Q(scope="item")
                | models.Q(scope__in=["chapter", "selection"], chapter__isnull=False),
                name="timesheet_line_destination",
            ),
        ]
        indexes = [
            models.Index(fields=["worker", "worked_on"], name="timesheet_line_worker_day_idx"),
            models.Index(fields=["timesheet", "worked_on"], name="timesheet_line_day_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.timesheet_id}:{self.worker_id}:{self.worked_on}"


class TimesheetLineItem(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line = models.ForeignKey(TimesheetLine, on_delete=models.CASCADE, related_name="item_links")
    item = models.ForeignKey("core.BudgetItem", on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "timesheet_line_item"
        constraints = [
            models.UniqueConstraint(fields=["line", "item"], name="timesheet_line_item_uq"),
        ]


class PayrollImport(OrgScopedModel):
    """La nómina real de un periodo, importada para conciliar. No toca el libro. Del archivo
    se guardan nombre, tamaño y huella, no el archivo (decisión 21 del plan)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    starts_on = models.DateField(verbose_name="desde")
    ends_on = models.DateField(verbose_name="hasta")
    file_name = models.CharField(max_length=200, verbose_name="archivo")
    file_size = models.PositiveIntegerField(verbose_name="tamaño")
    file_sha256 = models.CharField(max_length=64, verbose_name="huella del archivo")
    line_count = models.PositiveIntegerField(default=0, verbose_name="líneas")
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="total pagado"
    )
    imported_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="importó"
    )
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payroll_import"
        ordering = ["-starts_on", "-imported_at"]
        verbose_name = "nómina importada"
        verbose_name_plural = "nóminas importadas"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_on__gte=models.F("starts_on")),
                name="payroll_import_period_order",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.starts_on}:{self.ends_on}"


class PayrollImportLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payroll = models.ForeignKey(
        PayrollImport, on_delete=models.CASCADE, related_name="lines", verbose_name="nómina"
    )
    #: Fila del Excel (el encabezado es la 1).
    row = models.PositiveIntegerField(verbose_name="fila")
    document_type = models.CharField(max_length=4, verbose_name="tipo de documento")
    document_number = models.CharField(max_length=20, verbose_name="número de documento")
    #: El trabajador del registro con ese documento, si lo hay.
    worker = models.ForeignKey(
        Worker,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payroll_lines",
        verbose_name="trabajador",
    )
    #: Costo total del trabajador en el periodo según la nómina (devengado, aportes y
    #: provisiones).
    total_paid = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="total pagado")

    class Meta:
        db_table = "payroll_import_line"
        ordering = ["row"]
        constraints = [
            models.UniqueConstraint(
                fields=["payroll", "document_type", "document_number"],
                name="payroll_import_line_document_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(total_paid__gt=0), name="payroll_import_line_positive"
            ),
        ]


class LaborImportToken(OrgScopedModel):
    """Una vista previa de importación ya cargada (como `MaterialImportToken`): su `nonce` va
    firmado en la vista previa y es único."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nonce = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=10, choices=LaborImportKind.choices)
    applied_by = models.ForeignKey("core.AppUser", on_delete=models.PROTECT, related_name="+")
    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "labor_import_token"
        verbose_name = "importación de mano de obra aplicada"
        verbose_name_plural = "importaciones de mano de obra aplicadas"
