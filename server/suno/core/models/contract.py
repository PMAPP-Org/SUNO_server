"""Subcontratos y destajos (costos, pieza 6b).

Diseño: plans/2026-11-14-costos-pieza6b-contratos-design.md, sección 1.

- `Contract`: el contrato (subcontrato o destajo) con sus líneas, cargos y descuentos. Nace en
  borrador; lo aprueba otra persona y desde ahí solo cambia por otrosíes. Termina liquidado (su
  acta de liquidación aprobada) o anulado (aprobado y sin actas aprobadas). Un rechazo lo deja
  en borrador con el motivo.
- `ContractLine`: un ítem del presupuesto (identidad estable) y las tareas que cubre (ninguna:
  todas las del ítem). `amendment`: el otrosí que la agregó; solo cuenta si está aprobado.
- `ContractAmendment` y `ContractAmendmentLine`: el otrosí y sus aumentos de cantidad.
- `ContractCertificate` y `CertificateLine`: el acta de avance (o de liquidación) por
  cantidades. Al aprobarse escribe sus movimientos en el libro (`CostEntry.certificate`).

El dinero es `numeric` y `Decimal`; la aritmética vive en `suno.contracts.calc`.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models.functions import Lower

from .base import OrgScopedModel

#: Estados abiertos de un otrosí o un acta: se pueden editar o borrar.
OPEN_STATES = ("draft", "rejected")


class ContractKind(models.TextChoices):
    SUBCONTRACT = "subcontract", "Subcontrato"
    PIECEWORK = "piecework", "Destajo"


class ContractStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    APPROVED = "approved", "Aprobado"
    SETTLED = "settled", "Liquidado"
    VOIDED = "voided", "Anulado"


class AmendmentStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    APPROVED = "approved", "Aprobado"
    REJECTED = "rejected", "Rechazado"


class CertificateStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    APPROVED = "approved", "Aprobada"
    REJECTED = "rejected", "Rechazada"
    VOIDED = "voided", "Anulada"


def _pct_between_0_and_100(field: str, name: str) -> models.CheckConstraint:
    return models.CheckConstraint(
        condition=models.Q(**{f"{field}__gte": 0, f"{field}__lte": 100}), name=name
    )


def _decided_by_someone_else() -> models.Q:
    return ~models.Q(decided_by=models.F("created_by")) & ~models.Q(
        decided_by=models.F("updated_by")
    )


class Contract(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="contracts", verbose_name="proyecto"
    )
    kind = models.CharField(max_length=12, choices=ContractKind.choices, verbose_name="tipo")
    number = models.CharField(max_length=30, verbose_name="número")
    purpose = models.CharField(max_length=300, verbose_name="objeto")
    contractor = models.CharField(max_length=200, verbose_name="contratista")
    starts_on = models.DateField(verbose_name="inicio")
    ends_on = models.DateField(verbose_name="fin")
    status = models.CharField(
        max_length=10,
        choices=ContractStatus.choices,
        default=ContractStatus.DRAFT,
        verbose_name="estado",
    )
    advance_pct = models.DecimalField(
        max_digits=7, decimal_places=4, default=0, verbose_name="anticipo (%)"
    )
    #: Valor vigente × % al aprobar el contrato; fijo después (un otrosí no lo cambia).
    advance_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=0, verbose_name="anticipo"
    )
    retention_pct = models.DecimalField(
        max_digits=7, decimal_places=4, default=0, verbose_name="retención de garantía (%)"
    )
    #: Bruto directo de las cantidades vigentes + cargos. Lo recalcula el servicio al guardar el
    #: borrador y al aprobar un otrosí.
    current_value = models.DecimalField(
        max_digits=18, decimal_places=2, default=0, verbose_name="valor vigente"
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="creó"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="editó"
    )
    updated_at = models.DateTimeField(auto_now=True)
    approved_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="aprobó",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    #: El último rechazo: el contrato sigue en borrador con el motivo; al editarlo se limpia.
    rejected_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="rechazó",
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_note = models.CharField(max_length=500, blank=True, verbose_name="motivo")
    #: Liquidación (quien aprobó el acta de liquidación) o anulación (con su motivo).
    closed_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="cerró",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    close_note = models.CharField(max_length=500, blank=True, verbose_name="motivo de anulación")

    class Meta:
        db_table = "contract"
        ordering = ["number"]
        verbose_name = "contrato"
        verbose_name_plural = "contratos"
        indexes = [
            models.Index(fields=["org", "project", "status"], name="contract_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                Lower("number"), "project", name="contract_project_number_ci_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(starts_on__lte=models.F("ends_on")), name="contract_dates_ok"
            ),
            _pct_between_0_and_100("advance_pct", "contract_advance_pct_ok"),
            _pct_between_0_and_100("retention_pct", "contract_retention_pct_ok"),
            models.CheckConstraint(
                condition=models.Q(advance_amount__gte=0, current_value__gte=0),
                name="contract_amounts_ok",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status="draft", approved_by__isnull=True, approved_at__isnull=True
                )
                | (
                    models.Q(
                        status__in=["approved", "settled", "voided"],
                        approved_by__isnull=False,
                        approved_at__isnull=False,
                    )
                    & ~models.Q(approved_by=models.F("created_by"))
                    & ~models.Q(approved_by=models.F("updated_by"))
                ),
                name="contract_approval_ok",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["settled", "voided"],
                    closed_by__isnull=False,
                    closed_at__isnull=False,
                )
                | models.Q(
                    status__in=["draft", "approved"],
                    closed_by__isnull=True,
                    closed_at__isnull=True,
                ),
                name="contract_closing_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="voided") | ~models.Q(close_note=""),
                name="contract_void_note",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    rejected_by__isnull=True, rejected_at__isnull=True, rejection_note=""
                )
                | (
                    models.Q(status="draft", rejected_by__isnull=False, rejected_at__isnull=False)
                    & ~models.Q(rejection_note="")
                ),
                name="contract_rejection_pair",
            ),
        ]

    def __str__(self) -> str:
        return self.number


class ContractLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="lines")
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    item = models.ForeignKey(
        "core.BudgetItem", on_delete=models.PROTECT, related_name="contract_lines"
    )
    tasks = models.ManyToManyField(
        "core.Task", through="ContractLineTask", related_name="contract_lines", blank=True
    )
    unit = models.CharField(max_length=30, blank=True, verbose_name="unidad")
    #: Con la que entró (en el contrato o en el otrosí que la agregó).
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    #: Contratada vigente: `quantity` + aumentos de los otrosíes aprobados.
    current_quantity = models.DecimalField(
        max_digits=18, decimal_places=4, verbose_name="cantidad vigente"
    )
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="precio")
    #: El otrosí que la agregó; nulo si viene del contrato. Cuenta si el otrosí está aprobado.
    amendment = models.ForeignKey(
        "core.ContractAmendment",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="new_lines",
    )

    class Meta:
        db_table = "contract_line"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["contract", "order"], name="contract_line_order_uq"),
            models.CheckConstraint(
                condition=models.Q(
                    quantity__gt=0,
                    current_quantity__gte=models.F("quantity"),
                    unit_price__gte=0,
                ),
                name="contract_line_values_ok",
            ),
        ]


class ContractLineTask(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line = models.ForeignKey(ContractLine, on_delete=models.CASCADE, related_name="task_links")
    task = models.ForeignKey("core.Task", on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "contract_line_task"
        constraints = [
            models.UniqueConstraint(fields=["line", "task"], name="contract_line_task_uq"),
        ]


class ContractCharge(OrgScopedModel):
    """Un cargo del contrato (AIU, IVA…), como `BudgetCharge`: base `direct`, `subtotal` o
    `line:<orden>`."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="charges")
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    name = models.CharField(max_length=100, verbose_name="nombre")
    pct = models.DecimalField(max_digits=7, decimal_places=4, verbose_name="porcentaje")
    base = models.CharField(max_length=20, verbose_name="base")

    class Meta:
        db_table = "contract_charge"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["contract", "order"], name="contract_charge_order_uq"),
            models.CheckConstraint(condition=models.Q(pct__gte=0), name="contract_charge_pct_ok"),
        ]


class ContractDeduction(OrgScopedModel):
    """Un descuento del acta (retenciones, estampillas…): base `direct`, `gross` o `line:<n>`
    (la línea `n` de los cargos)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="deductions")
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    name = models.CharField(max_length=100, verbose_name="nombre")
    pct = models.DecimalField(max_digits=7, decimal_places=4, verbose_name="porcentaje")
    base = models.CharField(max_length=20, verbose_name="base")

    class Meta:
        db_table = "contract_deduction"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "order"], name="contract_deduction_order_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(pct__gte=0), name="contract_deduction_pct_ok"
            ),
        ]


class ContractAmendment(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="amendments")
    #: El del contrato, repetido: alcance por proyecto y bandeja "Por aprobar".
    project = models.ForeignKey("core.Project", on_delete=models.PROTECT, related_name="+")
    number = models.PositiveIntegerField(verbose_name="número")
    signed_on = models.DateField(verbose_name="fecha")
    reason = models.CharField(max_length=500, verbose_name="motivo")
    new_ends_on = models.DateField(null=True, blank=True, verbose_name="nueva fecha de fin")
    status = models.CharField(
        max_length=10,
        choices=AmendmentStatus.choices,
        default=AmendmentStatus.DRAFT,
        verbose_name="estado",
    )
    #: Valor vigente del contrato después de aplicarlo (al aprobarlo).
    value_after = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="creó"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="editó"
    )
    updated_at = models.DateTimeField(auto_now=True)
    decided_by = models.ForeignKey(
        "core.AppUser", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True, verbose_name="motivo")

    class Meta:
        db_table = "contract_amendment"
        ordering = ["number"]
        verbose_name = "otrosí"
        verbose_name_plural = "otrosíes"
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "number"], name="contract_amendment_number_uq"
            ),
            models.UniqueConstraint(
                fields=["contract"],
                condition=models.Q(status__in=OPEN_STATES),
                name="contract_amendment_one_open",
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft", decided_by__isnull=True, decided_at__isnull=True)
                | models.Q(
                    status__in=["approved", "rejected"],
                    decided_by__isnull=False,
                    decided_at__isnull=False,
                ),
                name="contract_amendment_decision_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="approved") | _decided_by_someone_else(),
                name="contract_amendment_approval_ok",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="rejected") | ~models.Q(decision_note=""),
                name="contract_amendment_rejection_note",
            ),
        ]


class ContractAmendmentLine(OrgScopedModel):
    """Un aumento de la cantidad de una línea existente."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    amendment = models.ForeignKey(
        ContractAmendment, on_delete=models.CASCADE, related_name="increases"
    )
    line = models.ForeignKey(ContractLine, on_delete=models.PROTECT, related_name="+")
    added_quantity = models.DecimalField(max_digits=18, decimal_places=4)

    class Meta:
        db_table = "contract_amendment_line"
        constraints = [
            models.UniqueConstraint(
                fields=["amendment", "line"], name="contract_amendment_line_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(added_quantity__gt=0), name="contract_amendment_line_positive"
            ),
        ]


class ContractCertificate(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="certificates")
    #: El del contrato, repetido: alcance por proyecto y bandeja "Por aprobar".
    project = models.ForeignKey("core.Project", on_delete=models.PROTECT, related_name="+")
    number = models.PositiveIntegerField(verbose_name="número")
    period_start = models.DateField(verbose_name="desde")
    period_end = models.DateField(verbose_name="hasta")
    is_settlement = models.BooleanField(default=False, verbose_name="liquidación")
    status = models.CharField(
        max_length=10,
        choices=CertificateStatus.choices,
        default=CertificateStatus.DRAFT,
        verbose_name="estado",
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="preparó"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="editó"
    )
    updated_at = models.DateTimeField(auto_now=True)
    decided_by = models.ForeignKey(
        "core.AppUser", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True, verbose_name="motivo")
    voided_by = models.ForeignKey(
        "core.AppUser", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_note = models.CharField(max_length=500, blank=True, verbose_name="motivo de anulación")
    #: El acta firmada (módulo de adjuntos). Obligatoria para aprobar.
    attachment = models.ForeignKey(
        "core.Blob",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="contract_certificates",
        verbose_name="acta firmada",
    )
    attachment_name = models.CharField(max_length=200, blank=True, verbose_name="archivo")
    direct = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    charges = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    #: Bruto = directo + cargos: el costo que va al libro.
    gross = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    amortization = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    retention = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    retention_returned = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    #: Puede ser negativo en una liquidación.
    net = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    #: Cargos y descuentos línea por línea (`calc.CertificateTotals.breakdown`).
    breakdown = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "contract_certificate"
        ordering = ["number"]
        verbose_name = "acta"
        verbose_name_plural = "actas"
        indexes = [
            models.Index(fields=["org", "project", "status"], name="contract_cert_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "number"], name="contract_certificate_number_uq"
            ),
            models.UniqueConstraint(
                fields=["contract"],
                condition=models.Q(status__in=OPEN_STATES),
                name="contract_certificate_one_open",
            ),
            models.CheckConstraint(
                condition=models.Q(period_start__lte=models.F("period_end")),
                name="contract_certificate_period_ok",
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft", decided_by__isnull=True, decided_at__isnull=True)
                | models.Q(
                    status__in=["approved", "rejected", "voided"],
                    decided_by__isnull=False,
                    decided_at__isnull=False,
                ),
                name="contract_certificate_decision_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(status__in=["approved", "voided"])
                | (models.Q(attachment__isnull=False) & _decided_by_someone_else()),
                name="contract_certificate_approval_ok",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="rejected") | ~models.Q(decision_note=""),
                name="contract_certificate_rejection_note",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="voided", voided_by__isnull=False, voided_at__isnull=False)
                    & ~models.Q(void_note="")
                )
                | (
                    ~models.Q(status="voided")
                    & models.Q(voided_by__isnull=True, voided_at__isnull=True)
                ),
                name="contract_certificate_void_pair",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    direct__gte=0,
                    charges__gte=0,
                    gross__gte=0,
                    amortization__gte=0,
                    retention__gte=0,
                    deductions__gte=0,
                    retention_returned__gte=0,
                ),
                name="contract_certificate_amounts_ok",
            ),
        ]

    def __str__(self) -> str:
        return f"Acta {self.number}"


class CertificateLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    certificate = models.ForeignKey(
        ContractCertificate, on_delete=models.CASCADE, related_name="lines"
    )
    line = models.ForeignKey(
        ContractLine, on_delete=models.PROTECT, related_name="certificate_lines"
    )
    #: Lo validado en campo sin pagar (lo calcula el servidor; ver `contracts.proposal`).
    proposed_quantity = models.DecimalField(max_digits=18, decimal_places=4)
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    #: Obligatoria si la cantidad difiere de la propuesta.
    justification = models.CharField(max_length=300, blank=True, verbose_name="justificación")
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")
    #: El ítem como lo nombra el acta al guardarse: lo imprime el PDF y lo copia el libro al
    #: aprobar, aunque el presupuesto lo renombre después (#88). Vacío en actas anteriores.
    label = models.TextField(blank=True, verbose_name="ítem impreso")

    class Meta:
        db_table = "certificate_line"
        constraints = [
            models.UniqueConstraint(fields=["certificate", "line"], name="certificate_line_uq"),
            models.CheckConstraint(
                condition=models.Q(proposed_quantity__gte=0, quantity__gte=0, amount__gte=0),
                name="certificate_line_values_ok",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity=models.F("proposed_quantity"))
                | ~models.Q(justification=""),
                name="certificate_line_justified",
            ),
        ]
