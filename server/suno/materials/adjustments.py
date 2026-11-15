"""Ajustes de inventario por conteo físico (costos, pieza 6c, spec 2.5 y 2.7).

- Borrador: por material, lo esperado es el saldo al guardar y la diferencia es contado −
  esperado. Una línea por material; un conteo sin diferencias no se guarda. Editar recalcula lo
  esperado y quita el acta de conteo firmada (ya no corresponde); adjuntarla no es editar.
- Al aprobar (otra persona, con el acta): se vuelven a leer los saldos; si alguno cambió desde
  el borrador, se rechaza. El faltante sale al promedio (`adjustment_out`) y es un costo
  aprobado al destino del ajuste por el total de los faltantes, con la fecha del conteo. El
  sobrante entra al promedio vigente; con saldo en cero, al costo unitario de la última entrada
  del material en la bodega; sin ninguna, al que escribió el usuario (`adjustment_in`).
- Anular (`reverse`): el faltante vuelve a la bodega por el valor que salió y el sobrante se
  retira si el saldo alcanza (los dos `adjustment_void`); el costo se reversa con fecha de hoy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from suno.attachments.web import Upload
from suno.budgets.totals import ZERO, item_total, quantity
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostStatus,
    InventoryAdjustment,
    InventoryAdjustmentItem,
    InventoryAdjustmentLine,
    Material,
    MaterialStatus,
    MovementKind,
    Project,
    StockBalance,
    StockMovement,
    Task,
    Warehouse,
)
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_reject_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.core.templatetags.money_tags import qty
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.costs.services import Assignment

from .common import (
    MAX_DESCRIPTION,
    MAX_FILE_NAME,
    MAX_LINES,
    MAX_NOTE,
    MAX_QUANTITY,
    MaterialRejected,
    clean_note,
    day_ok,
    lock_project,
    locked,
    next_number,
    price_ok,
    require_decidable,
    require_editable,
    reverse_entries,
    stored_upload,
)
from .destination import DestinationData, check_destination, destination_fields, link_items
from .kardex import Stock

CHANGED_COUNT = "El inventario cambió desde el conteo; recalcula el ajuste."
NOTHING_TO_ADJUST = "El conteo coincide con el inventario: no hay nada que ajustar."


@dataclass(frozen=True)
class AdjustmentLineData:
    material_id: UUID
    counted: Decimal
    #: Solo para un sobrante sin saldo ni entradas previas del material en la bodega.
    unit_cost: Decimal | None = None


@dataclass(frozen=True)
class AdjustmentData:
    warehouse_id: UUID | None
    counted_on: date
    destination: DestinationData
    lines: tuple[AdjustmentLineData, ...]
    notes: str = ""


@dataclass(frozen=True)
class CountLine:
    """Una línea calculada sin guardar: para el formulario ("Recalcular") y el servicio."""

    order: int
    material: Material
    expected: Decimal
    counted: Decimal
    given_unit_cost: Decimal | None
    #: Sobrante sin saldo ni entradas: hace falta el costo unitario que escriba el usuario.
    needs_unit_cost: bool

    @property
    def difference(self) -> Decimal:
        return self.counted - self.expected


def expected_quantities(
    ctx: RequestContext, warehouse_id: UUID, material_ids: Iterable[UUID]
) -> dict[UUID, Decimal]:
    return dict(
        scoped_qs(ctx, StockBalance)
        .filter(warehouse_id=warehouse_id, material_id__in=list(material_ids))
        .values_list("material_id", "quantity")
    )


def last_entry_costs(
    ctx: RequestContext, warehouse_id: UUID, material_ids: Iterable[UUID]
) -> dict[UUID, Decimal]:
    """El costo unitario del último movimiento de entrada de cada material en la bodega."""
    return dict(
        scoped_qs(ctx, StockMovement)
        .filter(warehouse_id=warehouse_id, material_id__in=list(material_ids), quantity__gt=0)
        .order_by("material_id", "-sequence")
        .distinct("material_id")
        .values_list("material_id", "unit_cost")
    )


def count_lines(
    ctx: RequestContext,
    warehouse: Warehouse,
    lines: Iterable[AdjustmentLineData],
    *,
    keep: frozenset[UUID] = frozenset(),
) -> tuple[list[str], list[CountLine]]:
    """Lo esperado (el saldo de hoy) y la diferencia de cada línea, y los errores."""
    given = list(lines)
    errors: list[str] = []
    if not given:
        errors.append("Agrega al menos un material contado.")
    elif len(given) > MAX_LINES:
        errors.append(f"Un ajuste lleva hasta {MAX_LINES} materiales.")
    ids = [line.material_id for line in given]
    materials = {m.pk: m for m in scoped_qs(ctx, Material).filter(pk__in=ids)}
    expected = expected_quantities(ctx, warehouse.pk, ids)
    last = last_entry_costs(ctx, warehouse.pk, ids)
    seen: set[UUID] = set()
    counted: list[CountLine] = []
    for n, line in enumerate(given, start=1):
        material = materials.get(line.material_id)
        if material is None or not (material.active or material.pk in keep):
            errors.append(f"Línea {n}: Elige un material activo del catálogo.")
            continue
        if material.pk in seen:
            errors.append(f"{material.name}: va una sola vez en el ajuste.")
            continue
        seen.add(material.pk)
        if not (
            line.counted.is_finite()
            and 0 <= line.counted <= MAX_QUANTITY
            and quantity(line.counted) == line.counted
        ):
            errors.append(f"{material.name}: lo contado es cero o más, con máximo 4 decimales.")
            continue
        if line.unit_cost is not None and not price_ok(line.unit_cost):
            errors.append(f"{material.name}: el costo unitario va en pesos y centavos.")
            continue
        before = expected.get(material.pk, Decimal("0"))
        needs = line.counted > before and before == 0 and material.pk not in last
        if needs and line.unit_cost is None:
            errors.append(
                f"{material.name}: no hay saldo ni entradas de este material en la bodega; "
                "escribe el costo unitario del sobrante."
            )
        counted.append(CountLine(n, material, before, line.counted, line.unit_cost, needs))
    if not errors and counted and all(line.difference == 0 for line in counted):
        errors.append(NOTHING_TO_ADJUST)
    return errors, counted


def adjustment_metadata(adjustment: InventoryAdjustment) -> dict[str, Any]:
    return {
        "project": str(adjustment.project_id),
        "adjustment": str(adjustment.pk),
        "number": adjustment.number,
        "warehouse": str(adjustment.warehouse_id),
        "shortage": str(adjustment.shortage_total),
        "surplus": str(adjustment.surplus_total),
        "status": adjustment.status,
    }


def _checked(
    ctx: RequestContext,
    project: Project,
    data: AdjustmentData,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    keep: frozenset[UUID] = frozenset(),
) -> tuple[Warehouse, Assignment, list[CountLine]]:
    errors: list[str] = []
    warehouse = (
        scoped_qs(ctx, Warehouse).filter(pk=data.warehouse_id, project=project, active=True).first()
        if data.warehouse_id is not None
        else None
    )
    if warehouse is None:
        errors.append("Elige una bodega activa del proyecto.")
    if not day_ok(data.counted_on, timezone.localdate()):
        errors.append("La fecha del conteo no puede ser futura ni anterior a 2000.")
    if len(data.notes.strip()) > MAX_NOTE:
        errors.append(f"Las notas pasan de {MAX_NOTE} caracteres.")
    found, assignment = check_destination(catalog, tasks, data.destination)
    errors += found
    lines: list[CountLine] = []
    if warehouse is not None:
        more, lines = count_lines(ctx, warehouse, data.lines, keep=keep)
        errors += more
    if errors or warehouse is None or assignment is None:
        raise MaterialRejected(errors)
    return warehouse, assignment, lines


def _write(
    ctx: RequestContext,
    adjustment: InventoryAdjustment,
    assignment: Assignment,
    lines: list[CountLine],
) -> None:
    link_items(ctx, InventoryAdjustmentItem, "adjustment", adjustment, assignment.item_ids)
    scoped_qs(ctx, InventoryAdjustmentLine).filter(adjustment=adjustment).delete()
    scoped_qs(ctx, InventoryAdjustmentLine).bulk_create(
        [
            InventoryAdjustmentLine(
                org_id=ctx.org_id,
                adjustment=adjustment,
                order=line.order,
                material=line.material,
                expected=line.expected,
                counted=line.counted,
                difference=line.difference,
                given_unit_cost=line.given_unit_cost,
            )
            for line in lines
        ]
    )


def _defaults(
    ctx: RequestContext,
    project: Project,
    catalog: Catalog | None,
    tasks: Mapping[UUID, Task] | None,
) -> tuple[Catalog, Mapping[UUID, Task]]:
    return (
        catalog if catalog is not None else load_catalog(ctx, project),
        tasks if tasks is not None else assignable_tasks(ctx, project),
    )


def create_adjustment(
    ctx: RequestContext,
    project: Project,
    data: AdjustmentData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> InventoryAdjustment:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    catalog, tasks = _defaults(ctx, project, catalog, tasks)
    with transaction.atomic():
        lock_project(ctx, project)
        warehouse, assignment, lines = _checked(ctx, project, data, catalog, tasks)
        adjustment = scoped_qs(ctx, InventoryAdjustment).create(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, InventoryAdjustment, project),
            warehouse=warehouse,
            counted_on=data.counted_on,
            notes=data.notes.strip(),
            status=str(MaterialStatus.DRAFT),
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            **destination_fields(assignment),
        )
        _write(ctx, adjustment, assignment, lines)
        audit.record(
            ctx,
            action=AuditAction.INVENTORY_ADJUSTMENT_CREATED,
            target=f"inventory_adjustment:{adjustment.pk}",
            metadata=adjustment_metadata(adjustment),
        )
    return adjustment


def update_adjustment(
    ctx: RequestContext,
    project: Project,
    adjustment_id: UUID,
    data: AdjustmentData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> InventoryAdjustment:
    """Recalcula lo esperado con los saldos de hoy y quita el acta firmada; un rechazado (solo
    su autor) vuelve a borrador."""
    catalog, tasks = _defaults(ctx, project, catalog, tasks)
    with transaction.atomic():
        lock_project(ctx, project)
        adjustment = locked(ctx, InventoryAdjustment, project, adjustment_id)
        require_editable(ctx, adjustment, can_update_cost)
        keep = frozenset(
            scoped_qs(ctx, InventoryAdjustmentLine)
            .filter(adjustment=adjustment)
            .values_list("material_id", flat=True)
        )
        warehouse, assignment, lines = _checked(ctx, project, data, catalog, tasks, keep)
        resubmitted = adjustment.status == MaterialStatus.REJECTED
        signed_removed = adjustment.attachment_id is not None
        adjustment.warehouse = warehouse
        adjustment.counted_on = data.counted_on
        adjustment.notes = data.notes.strip()
        for field, value in destination_fields(assignment).items():
            setattr(adjustment, field, value)
        adjustment.status = str(MaterialStatus.DRAFT)
        adjustment.decided_by = None
        adjustment.decided_at = None
        adjustment.decision_note = ""
        adjustment.attachment = None
        adjustment.attachment_name = ""
        adjustment.updated_by_id = ctx.user_id
        adjustment.save()
        _write(ctx, adjustment, assignment, lines)
        audit.record(
            ctx,
            action=AuditAction.INVENTORY_ADJUSTMENT_UPDATED,
            target=f"inventory_adjustment:{adjustment.pk}",
            metadata={
                **adjustment_metadata(adjustment),
                "resubmitted": resubmitted,
                "signed_removed": signed_removed,
            },
        )
    return adjustment


def delete_adjustment(ctx: RequestContext, project: Project, adjustment_id: UUID) -> None:
    with transaction.atomic():
        lock_project(ctx, project)
        adjustment = locked(ctx, InventoryAdjustment, project, adjustment_id)
        require_editable(ctx, adjustment, can_delete_cost)
        metadata = adjustment_metadata(adjustment)
        target = f"inventory_adjustment:{adjustment.pk}"
        adjustment.delete()
        audit.record(
            ctx, action=AuditAction.INVENTORY_ADJUSTMENT_DELETED, target=target, metadata=metadata
        )


def _require_signable(ctx: RequestContext, adjustment: InventoryAdjustment) -> None:
    if adjustment.status not in OPEN_STATES:
        raise MaterialRejected(["El acta de conteo se adjunta a un ajuste en borrador."])
    if not can_update_cost(ctx, adjustment):
        raise PermissionDenied


def attach_count(
    ctx: RequestContext, project: Project, adjustment_id: UUID, upload: Upload, name: str
) -> InventoryAdjustment:
    """Adjunta el acta de conteo firmada. No es editar (`updated_by` no cambia)."""
    current = scoped_qs(ctx, InventoryAdjustment).filter(pk=adjustment_id, project=project).first()
    if current is None:
        raise Http404
    _require_signable(ctx, current)
    blob = stored_upload(upload)
    with transaction.atomic():
        lock_project(ctx, project)
        adjustment = locked(ctx, InventoryAdjustment, project, adjustment_id)
        _require_signable(ctx, adjustment)
        adjustment.attachment = blob
        adjustment.attachment_name = name[:MAX_FILE_NAME]
        adjustment.save(update_fields=["attachment", "attachment_name"])
        audit.record(
            ctx,
            action=AuditAction.INVENTORY_ADJUSTMENT_UPDATED,
            target=f"inventory_adjustment:{adjustment.pk}",
            metadata={**adjustment_metadata(adjustment), "signed": True},
        )
    return adjustment


def adjustment_lines(
    ctx: RequestContext, adjustment: InventoryAdjustment
) -> list[InventoryAdjustmentLine]:
    return list(
        scoped_qs(ctx, InventoryAdjustmentLine)
        .filter(adjustment=adjustment)
        .select_related("material")
        .order_by("order")
    )


def _post_shortage(
    ctx: RequestContext, project: Project, adjustment: InventoryAdjustment, amount: Decimal
) -> None:
    """Un costo aprobado por el total de los faltantes, al destino del ajuste (spec 2.5)."""
    entry = scoped_qs(ctx, CostEntry).create(
        org_id=ctx.org_id,
        project=project,
        incurred_on=adjustment.counted_on,
        kind=CostKind.MATERIALS,
        description=(
            f"Ajuste {adjustment.code} · faltantes de inventario en {adjustment.warehouse.name}"
        )[:MAX_DESCRIPTION],
        amount=amount,
        third_party="",
        document_type=CostDocumentType.INVENTORY_COUNT,
        document_number=adjustment.code,
        scope=adjustment.scope,
        chapter_id=adjustment.chapter_id,
        task_id=adjustment.task_id,
        status=CostStatus.APPROVED,
        created_by_id=adjustment.created_by_id,
        updated_by_id=adjustment.updated_by_id,
        decided_by_id=ctx.user_id,
        decided_at=timezone.now(),
        attachment_id=adjustment.attachment_id,
        attachment_name=adjustment.attachment_name,
        inventory_adjustment=adjustment,
    )
    scoped_qs(ctx, CostEntryItem).bulk_create(
        [
            CostEntryItem(org_id=ctx.org_id, entry=entry, item_id=item_id)
            for item_id in scoped_qs(ctx, InventoryAdjustmentItem)
            .filter(adjustment=adjustment)
            .values_list("item_id", flat=True)
        ]
    )


def approve_adjustment(
    ctx: RequestContext, project: Project, adjustment_id: UUID
) -> InventoryAdjustment:
    with transaction.atomic():
        lock_project(ctx, project)
        adjustment = locked(ctx, InventoryAdjustment, project, adjustment_id)
        if not can_approve_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, adjustment)
        if adjustment.attachment_id is None:
            raise MaterialRejected(["Adjunta el acta de conteo firmada antes de aprobar."])
        lines = adjustment_lines(ctx, adjustment)
        warehouse_id = adjustment.warehouse_id
        stock = Stock(ctx, project, [(warehouse_id, line.material_id) for line in lines])
        if any(
            stock.balance(warehouse_id, line.material_id).quantity != line.expected
            for line in lines
        ):
            raise MaterialRejected([CHANGED_COUNT])
        last = last_entry_costs(ctx, warehouse_id, [line.material_id for line in lines])
        shortage = surplus = ZERO
        day = adjustment.counted_on
        for line in lines:
            balance = stock.balance(warehouse_id, line.material_id)
            if line.difference < 0:
                out = stock.take(
                    str(MovementKind.ADJUSTMENT_OUT), warehouse_id, line.material_id,
                    -line.difference, day, adjustment=adjustment,
                )  # fmt: skip
                line.unit_cost, line.amount = out.unit_cost, -out.value
                shortage += line.amount
                continue
            if line.difference == 0:
                line.unit_cost, line.amount = balance.average or ZERO, ZERO
                continue
            unit = balance.average or last.get(line.material_id) or line.given_unit_cost
            if unit is None:
                raise MaterialRejected(
                    [f"{line.material.name}: escribe el costo unitario del sobrante."]
                )
            line.unit_cost = unit
            line.amount = item_total(line.difference, unit)
            stock.add(
                str(MovementKind.ADJUSTMENT_IN), warehouse_id, line.material_id,
                line.difference, line.amount, day, adjustment=adjustment,
            )  # fmt: skip
            surplus += line.amount
        scoped_qs(ctx, InventoryAdjustmentLine).bulk_update(lines, ["unit_cost", "amount"])
        stock.save()
        if shortage > 0:
            _post_shortage(ctx, project, adjustment, shortage)
        adjustment.shortage_total, adjustment.surplus_total = shortage, surplus
        adjustment.status = str(MaterialStatus.APPROVED)
        adjustment.decided_by_id = ctx.user_id
        adjustment.decided_at = timezone.now()
        adjustment.save(
            update_fields=["shortage_total", "surplus_total", "status", "decided_by", "decided_at"]
        )
        audit.record(
            ctx,
            action=AuditAction.INVENTORY_ADJUSTMENT_APPROVED,
            target=f"inventory_adjustment:{adjustment.pk}",
            metadata=adjustment_metadata(adjustment),
        )
    return adjustment


def reject_adjustment(
    ctx: RequestContext, project: Project, adjustment_id: UUID, note: str
) -> InventoryAdjustment:
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        lock_project(ctx, project)
        adjustment = locked(ctx, InventoryAdjustment, project, adjustment_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, adjustment)
        adjustment.status = str(MaterialStatus.REJECTED)
        adjustment.decided_by_id = ctx.user_id
        adjustment.decided_at = timezone.now()
        adjustment.decision_note = reason
        adjustment.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.INVENTORY_ADJUSTMENT_REJECTED,
            target=f"inventory_adjustment:{adjustment.pk}",
            metadata={**adjustment_metadata(adjustment), "note": reason},
        )
    return adjustment


def void_adjustment(
    ctx: RequestContext, project: Project, adjustment_id: UUID, note: str
) -> InventoryAdjustment:
    reason = clean_note(note, "de la anulación")
    with transaction.atomic():
        lock_project(ctx, project)
        adjustment = locked(ctx, InventoryAdjustment, project, adjustment_id)
        if not can_reverse_costs(ctx, project):
            raise PermissionDenied
        if adjustment.status != MaterialStatus.APPROVED:
            raise MaterialRejected(["Solo se anula un ajuste aprobado."])
        lines = adjustment_lines(ctx, adjustment)
        warehouse_id = adjustment.warehouse_id
        stock = Stock(ctx, project, [(warehouse_id, line.material_id) for line in lines])
        errors = [
            f"No se puede anular: de {line.material.name} quedan "
            f"{qty(stock.balance(warehouse_id, line.material_id).quantity) or '0'} "
            f"{line.material.unit} en la bodega y el sobrante fue {qty(line.difference)}."
            for line in lines
            if line.difference > 0
            and stock.balance(warehouse_id, line.material_id).quantity < line.difference
        ]
        if errors:
            raise MaterialRejected(errors)
        today = timezone.localdate()
        kind = str(MovementKind.ADJUSTMENT_VOID)
        for line in lines:
            value = line.amount or ZERO
            if line.difference > 0:
                stock.withdraw(kind, warehouse_id, line.material_id, line.difference, value,
                               today, adjustment=adjustment)  # fmt: skip
            elif line.difference < 0:
                stock.add(kind, warehouse_id, line.material_id, -line.difference, value, today,
                          adjustment=adjustment)  # fmt: skip
        stock.save()
        reversals = reverse_entries(ctx, project, "inventory_adjustment", adjustment, reason)
        adjustment.status = str(MaterialStatus.VOIDED)
        adjustment.voided_by_id = ctx.user_id
        adjustment.voided_at = timezone.now()
        adjustment.void_note = reason
        adjustment.save(update_fields=["status", "voided_by", "voided_at", "void_note"])
        audit.record(
            ctx,
            action=AuditAction.INVENTORY_ADJUSTMENT_VOIDED,
            target=f"inventory_adjustment:{adjustment.pk}",
            metadata={**adjustment_metadata(adjustment), "note": reason, "reversals": reversals},
        )
    return adjustment
