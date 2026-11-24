"""Materiales: catálogo, bodegas, compras, salidas, devoluciones, traslados, ajustes y cortes
de consumos (costos, pieza 6c).

Diseño: plans/2026-11-15-costos-pieza6c-materiales-design.md, sección 1.

- `Material`: catálogo de la organización; el código es único sin distinguir mayúsculas. Un
  material no se borra: se desactiva.
- `Warehouse`: una bodega del proyecto.
- `MaterialPurchase` (CM-001): compra directa a un destino (costo en el libro al aprobarla) o a
  bodega (entra al inventario al aprobarla). Sus líneas llevan el destino solo si es directa.
- `MaterialIssue` (SA-001), `MaterialReturn` (DV-001) y `MaterialTransfer` (TR-001): mueven el
  inventario al guardarse, sin aprobación. Salidas y devoluciones llegan al libro por un corte.
- `InventoryAdjustment` (AJ-001): conteo físico; al aprobarlo, el faltante es costo.
- `ConsumptionCut`: el corte de consumos de un periodo; al aprobarlo escribe el libro.
- `StockMovement` (kárdex) y `StockBalance` (saldo por bodega y material): el saldo es la suma
  de los movimientos de su par y se actualiza en la misma transacción, con la fila bloqueada
  (`suno.materials.kardex`).

El promedio no se guarda: es valor / cantidad del saldo. Valores con IVA incluido. Cantidades
con 4 decimales, valores a centavos y costo unitario con 6 decimales.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.db import models
from django.db.models.functions import Lower

from .base import OrgScopedModel
from .cost import CostDocumentType, CostScope


class MaterialStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    APPROVED = "approved", "Aprobado"
    REJECTED = "rejected", "Rechazado"
    VOIDED = "voided", "Anulado"


class PurchaseKind(models.TextChoices):
    DIRECT = "direct", "Directa a un destino"
    WAREHOUSE = "warehouse", "A bodega"


class MovementKind(models.TextChoices):
    PURCHASE = "purchase", "Compra"
    ISSUE = "issue", "Salida"
    RETURN = "return", "Devolución"
    TRANSFER_OUT = "transfer_out", "Traslado que sale"
    TRANSFER_IN = "transfer_in", "Traslado que entra"
    ADJUSTMENT_IN = "adjustment_in", "Sobrante de ajuste"
    ADJUSTMENT_OUT = "adjustment_out", "Faltante de ajuste"
    PURCHASE_VOID = "purchase_void", "Anulación de compra"
    ADJUSTMENT_VOID = "adjustment_void", "Anulación de ajuste"


#: Tipos que solo entran o solo salen; la anulación de un ajuste va en los dos sentidos.
ENTRY_KINDS = ("purchase", "return", "transfer_in", "adjustment_in")
EXIT_KINDS = ("issue", "transfer_out", "adjustment_out", "purchase_void")
#: El campo del documento de origen de cada grupo de tipos.
ORIGINS = (
    (("purchase", "purchase_void"), "purchase"),
    (("issue",), "issue"),
    (("return",), "material_return"),
    (("transfer_out", "transfer_in"), "transfer"),
    (("adjustment_in", "adjustment_out", "adjustment_void"), "adjustment"),
)
MOVEMENT_ORIGINS = tuple(field for _, field in ORIGINS)


def _decided_by_someone_else() -> models.Q:
    return ~models.Q(decided_by=models.F("created_by")) & ~models.Q(
        decided_by=models.F("updated_by")
    )


def _document_constraints(prefix: str) -> list[models.BaseConstraint]:
    """Consecutivo, decisión, autoaprobación con soporte, motivo y anulación de una compra, un
    ajuste o un corte."""
    return [
        models.UniqueConstraint(fields=["project", "number"], name=f"{prefix}_number_uq"),
        models.CheckConstraint(
            condition=models.Q(status="draft", decided_by__isnull=True, decided_at__isnull=True)
            | models.Q(
                status__in=["approved", "rejected", "voided"],
                decided_by__isnull=False,
                decided_at__isnull=False,
            ),
            name=f"{prefix}_decision_pair",
        ),
        models.CheckConstraint(
            condition=~models.Q(status__in=["approved", "voided"])
            | (models.Q(attachment__isnull=False) & _decided_by_someone_else()),
            name=f"{prefix}_approval_ok",
        ),
        models.CheckConstraint(
            condition=~models.Q(status="rejected") | ~models.Q(decision_note=""),
            name=f"{prefix}_rejection_note",
        ),
        models.CheckConstraint(
            condition=~models.Q(status="voided")
            | (
                models.Q(voided_by__isnull=False, voided_at__isnull=False) & ~models.Q(void_note="")
            ),
            name=f"{prefix}_void_note",
        ),
    ]


def _destination_check(name: str, *, optional: bool = False) -> models.CheckConstraint:
    """La misma forma del destino de la 6a (`cost_entry_scope_fields`); `optional`: una línea de
    compra a bodega no lleva destino (`scope` vacío)."""
    condition = (
        models.Q(scope="indirect", chapter__isnull=True, task__isnull=True)
        | models.Q(scope="item")
        | models.Q(scope__in=["chapter", "selection"], chapter__isnull=False)
    )
    if optional:
        condition |= models.Q(scope="", chapter__isnull=True, task__isnull=True)
    return models.CheckConstraint(condition=condition, name=name)


def _one_origin() -> models.Q:
    """Exactamente un documento de origen, y el que corresponde al tipo del movimiento."""
    condition = models.Q()
    for kinds, field in ORIGINS:
        nulls = {f"{other}__isnull": other != field for other in MOVEMENT_ORIGINS}
        condition |= models.Q(kind__in=list(kinds), **nulls)
    return condition


class Authored(OrgScopedModel):
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="registró"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="modificó"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Decided(Authored):
    """Lo que se aprueba uno por uno: estado, decisión, anulación y soporte."""

    status = models.CharField(
        max_length=10,
        choices=MaterialStatus.choices,
        default=MaterialStatus.DRAFT,
        verbose_name="estado",
    )
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
    voided_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="anuló",
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_note = models.CharField(max_length=500, blank=True, verbose_name="motivo de anulación")
    attachment = models.ForeignKey(
        "core.Blob",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="soporte",
    )
    attachment_name = models.CharField(max_length=200, blank=True, verbose_name="archivo")

    class Meta:
        abstract = True


class Destined(models.Model):
    """El destino de la 6a: `scope`, capítulo, ítems (M2M en cada modelo) y tarea opcional."""

    scope = models.CharField(
        max_length=12, choices=CostScope.choices, blank=True, verbose_name="destino"
    )
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

    class Meta:
        abstract = True


class Material(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=40, verbose_name="código")
    name = models.CharField(max_length=200, verbose_name="nombre")
    unit = models.CharField(max_length=20, verbose_name="unidad")
    category = models.CharField(max_length=80, blank=True, verbose_name="categoría")
    active = models.BooleanField(default=True, verbose_name="activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "material"
        ordering = ["code"]
        verbose_name = "material"
        verbose_name_plural = "materiales"
        constraints = [
            models.UniqueConstraint(Lower("code"), "org", name="material_org_code_ci_uq"),
        ]
        indexes = [models.Index(fields=["org", "category"], name="material_category_idx")]

    @property
    def label(self) -> str:
        return f"{self.code} · {self.name}"

    def __str__(self) -> str:
        return self.label


class Warehouse(Authored):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="warehouses", verbose_name="proyecto"
    )
    name = models.CharField(max_length=120, verbose_name="nombre")
    active = models.BooleanField(default=True, verbose_name="activa")

    class Meta:
        db_table = "warehouse"
        ordering = ["name"]
        verbose_name = "bodega"
        verbose_name_plural = "bodegas"
        constraints = [
            models.UniqueConstraint(Lower("name"), "project", name="warehouse_project_name_ci_uq"),
        ]

    def __str__(self) -> str:
        return self.name


class MaterialPurchase(Decided):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="material_purchases",
        verbose_name="proyecto",
    )
    number = models.PositiveIntegerField(verbose_name="número")
    kind = models.CharField(max_length=10, choices=PurchaseKind.choices, verbose_name="tipo")
    warehouse = models.ForeignKey(
        Warehouse,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="purchases",
        verbose_name="bodega",
    )
    supplier = models.CharField(max_length=200, verbose_name="proveedor")
    supplier_tax_id = models.CharField(max_length=40, blank=True, verbose_name="NIT")
    document_type = models.CharField(
        max_length=24, choices=CostDocumentType.choices, verbose_name="tipo de documento"
    )
    document_number = models.CharField(max_length=80, blank=True, verbose_name="número")
    purchased_on = models.DateField(verbose_name="fecha")
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="total"
    )

    class Meta:
        db_table = "material_purchase"
        ordering = ["-number"]
        verbose_name = "compra de materiales"
        verbose_name_plural = "compras de materiales"
        constraints = [
            *_document_constraints("material_purchase"),
            models.CheckConstraint(
                condition=models.Q(kind="warehouse", warehouse__isnull=False)
                | models.Q(kind="direct", warehouse__isnull=True),
                name="material_purchase_kind_warehouse",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "project", "status"], name="material_purchase_status_idx")
        ]

    def __str__(self) -> str:
        return self.code

    @property
    def code(self) -> str:
        return f"CM-{self.number:03d}"


class MaterialPurchaseLine(OrgScopedModel, Destined):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase = models.ForeignKey(
        MaterialPurchase, on_delete=models.CASCADE, related_name="lines", verbose_name="compra"
    )
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    material = models.ForeignKey(
        Material, on_delete=models.PROTECT, related_name="purchase_lines", verbose_name="material"
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor unitario")
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")
    #: Código y nombre del material al registrar la compra: los copia el libro al aprobar,
    #: aunque el catálogo lo renombre después (#88). Vacío en compras anteriores.
    material_label = models.TextField(blank=True, verbose_name="material registrado")
    items = models.ManyToManyField(
        "core.BudgetItem",
        through="MaterialPurchaseLineItem",
        related_name="material_purchase_lines",
        verbose_name="ítems",
    )

    class Meta:
        db_table = "material_purchase_line"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["purchase", "order"], name="material_purchase_line_uq"),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0, unit_price__gt=0, amount__gt=0),
                name="material_purchase_line_values_ok",
            ),
            _destination_check("material_purchase_line_destination", optional=True),
        ]

    def __str__(self) -> str:
        return f"{self.purchase_id}:{self.order}"


class MaterialPurchaseLineItem(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line = models.ForeignKey(
        MaterialPurchaseLine, on_delete=models.CASCADE, related_name="item_links"
    )
    item = models.ForeignKey("core.BudgetItem", on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "material_purchase_line_item"
        constraints = [
            models.UniqueConstraint(fields=["line", "item"], name="material_purchase_line_item_uq"),
        ]


class ConsumptionCut(Decided):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="consumption_cuts",
        verbose_name="proyecto",
    )
    number = models.PositiveIntegerField(verbose_name="número")
    period_start = models.DateField(verbose_name="desde")
    period_end = models.DateField(verbose_name="hasta")
    #: `fingerprint` (lo que se compara al aprobar) y `groups` (el detalle que imprime el PDF),
    #: guardados al crear o recalcular el borrador (`suno.materials.cuts.Proposal.snapshot`).
    summary = models.JSONField(default=dict, blank=True, verbose_name="resumen")
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="total"
    )
    issue_count = models.PositiveIntegerField(default=0, verbose_name="salidas")
    return_count = models.PositiveIntegerField(default=0, verbose_name="devoluciones")

    class Meta:
        db_table = "consumption_cut"
        ordering = ["-number"]
        verbose_name = "corte de consumos"
        verbose_name_plural = "cortes de consumos"
        constraints = [
            *_document_constraints("consumption_cut"),
            models.CheckConstraint(
                condition=models.Q(period_end__gte=models.F("period_start")),
                name="consumption_cut_period_order",
            ),
            models.UniqueConstraint(
                fields=["project"],
                condition=models.Q(status__in=["draft", "rejected"]),
                name="consumption_cut_one_open_uq",
            ),
        ]

    def __str__(self) -> str:
        return self.code

    @property
    def code(self) -> str:
        return f"Corte {self.number}"


class MaterialIssue(Authored, Destined):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="material_issues",
        verbose_name="proyecto",
    )
    number = models.PositiveIntegerField(verbose_name="número")
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="issues", verbose_name="bodega"
    )
    issued_on = models.DateField(verbose_name="fecha")
    received_by = models.CharField(max_length=120, blank=True, verbose_name="recibe")
    notes = models.CharField(max_length=500, blank=True, verbose_name="notas")
    #: Generado por el cliente (la futura captura móvil): un reintento no duplica la salida.
    #: Único por proyecto (`material_issue_client_uq`).
    client_id = models.UUIDField(null=True, blank=True)
    cut = models.ForeignKey(
        ConsumptionCut,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="issues",
        verbose_name="corte",
    )
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="valor"
    )
    items = models.ManyToManyField(
        "core.BudgetItem",
        through="MaterialIssueItem",
        related_name="material_issues",
        verbose_name="ítems",
    )

    class Meta:
        db_table = "material_issue"
        ordering = ["-issued_on", "-number"]
        verbose_name = "salida de materiales"
        verbose_name_plural = "salidas de materiales"
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="material_issue_number_uq"),
            models.UniqueConstraint(
                fields=["project", "client_id"],
                condition=models.Q(client_id__isnull=False),
                name="material_issue_client_uq",
            ),
            _destination_check("material_issue_destination"),
        ]
        indexes = [
            models.Index(
                fields=["project", "issued_on"],
                condition=models.Q(cut__isnull=True),
                name="material_issue_uncut_idx",
            ),
            models.Index(fields=["warehouse", "issued_on"], name="material_issue_warehouse_idx"),
        ]

    def __str__(self) -> str:
        return self.code

    @property
    def code(self) -> str:
        return f"SA-{self.number:03d}"


class MaterialIssueItem(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey(MaterialIssue, on_delete=models.CASCADE, related_name="item_links")
    item = models.ForeignKey("core.BudgetItem", on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "material_issue_item"
        constraints = [
            models.UniqueConstraint(fields=["issue", "item"], name="material_issue_item_uq"),
        ]


class MaterialIssueLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey(MaterialIssue, on_delete=models.CASCADE, related_name="lines")
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    #: Fijos al registrar la salida (el promedio de ese momento).
    unit_cost = models.DecimalField(max_digits=22, decimal_places=6, verbose_name="costo unitario")
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")

    class Meta:
        db_table = "material_issue_line"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["issue", "order"], name="material_issue_line_uq"),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0, unit_cost__gte=0, amount__gte=0),
                name="material_issue_line_values_ok",
            ),
        ]


class MaterialReturn(Authored):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="material_returns",
        verbose_name="proyecto",
    )
    number = models.PositiveIntegerField(verbose_name="número")
    #: La salida de origen: de ella salen la bodega y el destino.
    issue = models.ForeignKey(
        MaterialIssue, on_delete=models.PROTECT, related_name="returns", verbose_name="salida"
    )
    returned_on = models.DateField(verbose_name="fecha")
    notes = models.CharField(max_length=500, blank=True, verbose_name="notas")
    cut = models.ForeignKey(
        ConsumptionCut,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="returns",
        verbose_name="corte",
    )
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="valor"
    )

    class Meta:
        db_table = "material_return"
        ordering = ["-returned_on", "-number"]
        verbose_name = "devolución de materiales"
        verbose_name_plural = "devoluciones de materiales"
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="material_return_number_uq"),
        ]
        indexes = [
            models.Index(
                fields=["project", "returned_on"],
                condition=models.Q(cut__isnull=True),
                name="material_return_uncut_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.code

    @property
    def code(self) -> str:
        return f"DV-{self.number:03d}"


class MaterialReturnLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    material_return = models.ForeignKey(
        MaterialReturn, on_delete=models.CASCADE, related_name="lines"
    )
    issue_line = models.ForeignKey(
        MaterialIssueLine, on_delete=models.PROTECT, related_name="return_lines"
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    unit_cost = models.DecimalField(max_digits=22, decimal_places=6, verbose_name="costo unitario")
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")

    class Meta:
        db_table = "material_return_line"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0, unit_cost__gte=0, amount__gte=0),
                name="material_return_line_values_ok",
            ),
        ]


class MaterialTransfer(Authored):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="material_transfers",
        verbose_name="proyecto",
    )
    number = models.PositiveIntegerField(verbose_name="número")
    source = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="+", verbose_name="sale de"
    )
    target = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="+", verbose_name="entra a"
    )
    moved_on = models.DateField(verbose_name="fecha")
    notes = models.CharField(max_length=500, blank=True, verbose_name="notas")
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="valor"
    )

    class Meta:
        db_table = "material_transfer"
        ordering = ["-moved_on", "-number"]
        verbose_name = "traslado de materiales"
        verbose_name_plural = "traslados de materiales"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "number"], name="material_transfer_number_uq"
            ),
            models.CheckConstraint(
                condition=~models.Q(source=models.F("target")),
                name="material_transfer_two_warehouses",
            ),
        ]

    def __str__(self) -> str:
        return self.code

    @property
    def code(self) -> str:
        return f"TR-{self.number:03d}"


class MaterialTransferLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transfer = models.ForeignKey(MaterialTransfer, on_delete=models.CASCADE, related_name="lines")
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    unit_cost = models.DecimalField(max_digits=22, decimal_places=6, verbose_name="costo unitario")
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")

    class Meta:
        db_table = "material_transfer_line"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["transfer", "order"], name="material_transfer_line_uq"),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0, unit_cost__gte=0, amount__gte=0),
                name="material_transfer_line_values_ok",
            ),
        ]


class InventoryAdjustment(Decided, Destined):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="inventory_adjustments",
        verbose_name="proyecto",
    )
    number = models.PositiveIntegerField(verbose_name="número")
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="adjustments", verbose_name="bodega"
    )
    counted_on = models.DateField(verbose_name="fecha del conteo")
    notes = models.CharField(max_length=500, blank=True, verbose_name="notas")
    #: Fijados al aprobar.
    shortage_total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="faltantes"
    )
    surplus_total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="sobrantes"
    )
    items = models.ManyToManyField(
        "core.BudgetItem",
        through="InventoryAdjustmentItem",
        related_name="inventory_adjustments",
        verbose_name="ítems",
    )

    class Meta:
        db_table = "inventory_adjustment"
        ordering = ["-number"]
        verbose_name = "ajuste de inventario"
        verbose_name_plural = "ajustes de inventario"
        constraints = [
            *_document_constraints("inventory_adjustment"),
            _destination_check("inventory_adjustment_destination"),
        ]

    def __str__(self) -> str:
        return self.code

    @property
    def code(self) -> str:
        return f"AJ-{self.number:03d}"


class InventoryAdjustmentItem(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    adjustment = models.ForeignKey(
        InventoryAdjustment, on_delete=models.CASCADE, related_name="item_links"
    )
    item = models.ForeignKey("core.BudgetItem", on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "inventory_adjustment_item"
        constraints = [
            models.UniqueConstraint(
                fields=["adjustment", "item"], name="inventory_adjustment_item_uq"
            ),
        ]


class InventoryAdjustmentLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    adjustment = models.ForeignKey(
        InventoryAdjustment, on_delete=models.CASCADE, related_name="lines"
    )
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="+")
    #: El saldo al guardar el borrador; al aprobar tiene que seguir igual.
    expected = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="esperado")
    counted = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="contado")
    difference = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="diferencia")
    #: El costo unitario que escribe el usuario para un sobrante sin saldo ni entradas previas.
    given_unit_cost = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, verbose_name="costo dado"
    )
    #: Fijados al aprobar.
    unit_cost = models.DecimalField(
        max_digits=22, decimal_places=6, null=True, blank=True, verbose_name="costo unitario"
    )
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, verbose_name="valor"
    )

    class Meta:
        db_table = "inventory_adjustment_line"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["adjustment", "order"], name="inventory_adjustment_line_uq"
            ),
            models.UniqueConstraint(
                fields=["adjustment", "material"], name="inventory_adjustment_material_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    expected__gte=0,
                    counted__gte=0,
                    difference=models.F("counted") - models.F("expected"),
                ),
                name="inventory_adjustment_line_difference",
            ),
            models.CheckConstraint(
                condition=models.Q(given_unit_cost__isnull=True) | models.Q(given_unit_cost__gt=0),
                name="inventory_adjustment_line_given_cost",
            ),
        ]


class StockBalance(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    #: Repetido a propósito (la bodega ya lo dice): el inventario del proyecto sin otra unión.
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="stock_balances"
    )
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="balances")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="balances")
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4, default=Decimal("0"), verbose_name="cantidad"
    )
    value = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="valor"
    )
    #: La secuencia del último movimiento del par: la del siguiente es esta + 1.
    last_sequence = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "stock_balance"
        verbose_name = "saldo de inventario"
        verbose_name_plural = "saldos de inventario"
        constraints = [
            models.UniqueConstraint(fields=["warehouse", "material"], name="stock_balance_uq"),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0, value__gte=0),
                name="stock_balance_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0) | models.Q(value=0),
                name="stock_balance_empty_has_no_value",
            ),
        ]
        indexes = [models.Index(fields=["project", "material"], name="stock_balance_project_idx")]


class StockMovement(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="stock_movements"
    )
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="movements")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="movements")
    moved_on = models.DateField(verbose_name="fecha")
    kind = models.CharField(max_length=16, choices=MovementKind.choices, verbose_name="tipo")
    #: Con signo: positiva si entra, negativa si sale.
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    unit_cost = models.DecimalField(max_digits=22, decimal_places=6, verbose_name="costo unitario")
    value = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")
    #: Creciente por bodega y material: da el orden del kárdex.
    sequence = models.PositiveIntegerField(verbose_name="secuencia")
    created_by = models.ForeignKey("core.AppUser", on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    purchase = models.ForeignKey(
        MaterialPurchase, null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )
    issue = models.ForeignKey(
        MaterialIssue, null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )
    material_return = models.ForeignKey(
        MaterialReturn, null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )
    transfer = models.ForeignKey(
        MaterialTransfer, null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )
    adjustment = models.ForeignKey(
        InventoryAdjustment,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements",
    )

    class Meta:
        db_table = "stock_movement"
        ordering = ["warehouse", "material", "sequence"]
        verbose_name = "movimiento de inventario"
        verbose_name_plural = "movimientos de inventario"
        constraints = [
            models.UniqueConstraint(
                fields=["warehouse", "material", "sequence"], name="stock_movement_sequence_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=[*ENTRY_KINDS, "adjustment_void"], quantity__gt=0, value__gte=0
                )
                | models.Q(kind__in=[*EXIT_KINDS, "adjustment_void"], quantity__lt=0, value__lte=0),
                name="stock_movement_sign",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_cost__gte=0), name="stock_movement_unit_cost_ok"
            ),
            models.CheckConstraint(condition=_one_origin(), name="stock_movement_one_origin"),
        ]
        indexes = [
            models.Index(
                fields=["warehouse", "material", "moved_on"], name="stock_movement_kardex_day_idx"
            ),
            models.Index(fields=["project", "moved_on"], name="stock_movement_project_day_idx"),
        ]


class MaterialImportToken(OrgScopedModel):
    """Una vista previa de importación del catálogo ya cargada (como `CostImportToken`): su
    `nonce` va firmado en la vista previa y es único."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nonce = models.CharField(max_length=64, unique=True)
    applied_by = models.ForeignKey("core.AppUser", on_delete=models.PROTECT, related_name="+")
    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "material_import_token"
        verbose_name = "importación de materiales aplicada"
        verbose_name_plural = "importaciones de materiales aplicadas"
