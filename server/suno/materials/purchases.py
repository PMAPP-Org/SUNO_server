"""Compras de materiales (costos, pieza 6c, spec 2.2, 2.3 y 2.7).

- Una compra nace en borrador con su consecutivo (CM-001) y se aprueba una por una, con su
  soporte (la factura) y por otra persona. Adjuntar la factura no es editar; editar no la
  quita (decisión 13 del plan). Un rechazo lo corrige su autor y vuelve a borrador.
- Directa: al aprobarla, cada línea es un costo aprobado en el libro, al destino de la línea; no
  toca el inventario. Anularla escribe los reversos con fecha de hoy.
- A bodega: al aprobarla, cada línea entra a la bodega por su valor (`purchase`); no es costo
  (llega con las salidas). Se anula solo si el saldo alcanza para retirar lo comprado
  (`purchase_void`).
- Anular pide `reverse`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
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
from suno.budgets.totals import ZERO, item_total
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostStatus,
    Material,
    MaterialPurchase,
    MaterialPurchaseLine,
    MaterialPurchaseLineItem,
    MaterialStatus,
    MovementKind,
    Project,
    PurchaseKind,
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
    MAX_DOCUMENT_NUMBER,
    MAX_FILE_NAME,
    MAX_LINES,
    MAX_PRICE,
    MAX_THIRD_PARTY,
    MaterialRejected,
    clean_note,
    day_ok,
    lock_project,
    locked,
    next_number,
    price_ok,
    quantity_ok,
    require_decidable,
    require_editable,
    reverse_entries,
    stored_upload,
)
from .destination import DestinationData, check_destination
from .kardex import Stock

MAX_SUPPLIER = 200
TOO_BIG = "El valor de la línea pasa del máximo permitido: revisa la cantidad y el valor unitario."
TOO_BIG_TOTAL = "El total de la compra pasa del máximo permitido: divídela en varias compras."
MAX_TAX_ID = 40


@dataclass(frozen=True)
class PurchaseLineData:
    material_id: UUID
    quantity: Decimal
    unit_price: Decimal
    #: Solo en una compra directa.
    destination: DestinationData | None = None


@dataclass(frozen=True)
class PurchaseData:
    kind: str
    warehouse_id: UUID | None
    supplier: str
    supplier_tax_id: str
    document_type: str
    document_number: str
    purchased_on: date
    lines: tuple[PurchaseLineData, ...]


@dataclass(frozen=True)
class CheckedLine:
    order: int
    material: Material
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    assignment: Assignment | None


def _header_errors(
    ctx: RequestContext, project: Project, data: PurchaseData, today: date
) -> list[str]:
    errors: list[str] = []
    if data.kind not in PurchaseKind.values:
        errors.append("Elige el tipo de compra.")
    if data.kind == PurchaseKind.WAREHOUSE:
        found = data.warehouse_id is not None and (
            scoped_qs(ctx, Warehouse)
            .filter(pk=data.warehouse_id, project=project, active=True)
            .exists()
        )
        if not found:
            errors.append("Elige una bodega activa del proyecto.")
    elif data.warehouse_id is not None:
        errors.append("Una compra directa no lleva bodega.")
    supplier = data.supplier.strip()
    if not supplier:
        errors.append("Escribe el proveedor.")
    elif len(supplier) > MAX_SUPPLIER:
        errors.append(f"El proveedor pasa de {MAX_SUPPLIER} caracteres.")
    if len(data.supplier_tax_id.strip()) > MAX_TAX_ID:
        errors.append(f"El NIT pasa de {MAX_TAX_ID} caracteres.")
    if data.document_type not in CostDocumentType.values:
        errors.append("Elige el tipo de documento.")
    if len(data.document_number.strip()) > MAX_DOCUMENT_NUMBER:
        errors.append(f"El número de documento pasa de {MAX_DOCUMENT_NUMBER} caracteres.")
    if not day_ok(data.purchased_on, today):
        errors.append("La fecha de la compra no puede ser futura ni anterior a 2000.")
    if not data.lines:
        errors.append("Agrega al menos una línea.")
    elif len(data.lines) > MAX_LINES:
        errors.append(f"Una compra lleva hasta {MAX_LINES} líneas.")
    return errors


def purchase_errors(
    ctx: RequestContext,
    project: Project,
    data: PurchaseData,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    *,
    today: date,
    keep: frozenset[UUID] = frozenset(),
) -> tuple[list[str], list[CheckedLine]]:
    """Errores para el usuario y las líneas listas para guardar. `keep`: los materiales que ya
    tiene la compra (siguen aunque se hayan desactivado)."""
    errors = _header_errors(ctx, project, data, today)
    direct = data.kind == PurchaseKind.DIRECT
    materials = {
        material.pk: material
        for material in scoped_qs(ctx, Material).filter(
            pk__in=[line.material_id for line in data.lines]
        )
    }
    checked: list[CheckedLine] = []
    for n, line in enumerate(data.lines, start=1):
        material = materials.get(line.material_id)
        if material is None or not (material.active or material.pk in keep):
            errors.append(f"Línea {n}: Elige un material activo del catálogo.")
            continue
        problems: list[str] = []
        if not quantity_ok(line.quantity):
            problems.append("La cantidad es mayor que cero, con máximo 4 decimales.")
        if not price_ok(line.unit_price):
            problems.append("El valor unitario es mayor que cero, en pesos y centavos.")
        amount = ZERO if problems else item_total(line.quantity, line.unit_price)
        if not problems and amount <= 0:
            problems.append("El valor de la línea da cero: revisa la cantidad y el valor unitario.")
        elif not problems and amount > MAX_PRICE:
            problems.append(TOO_BIG)
        assignment: Assignment | None = None
        if direct:
            if line.destination is None:
                problems.append("Escribe el destino.")
            else:
                found, assignment = check_destination(catalog, tasks, line.destination)
                problems += found
        elif line.destination is not None:
            problems.append("Una compra a bodega no lleva destino: el costo llega con las salidas.")
        errors += [f"Línea {n}: {problem}" for problem in problems]
        if not problems:
            checked.append(
                CheckedLine(n, material, line.quantity, line.unit_price, amount, assignment)
            )
    if sum((line.amount for line in checked), ZERO) > MAX_PRICE:
        errors.append(TOO_BIG_TOTAL)
    return errors, checked


def purchase_metadata(purchase: MaterialPurchase) -> dict[str, Any]:
    return {
        "project": str(purchase.project_id),
        "purchase": str(purchase.pk),
        "number": purchase.number,
        "kind": purchase.kind,
        "total": str(purchase.total),
        "status": purchase.status,
    }


def _checked(
    ctx: RequestContext,
    project: Project,
    data: PurchaseData,
    catalog: Catalog | None,
    tasks: Mapping[UUID, Task] | None,
    keep: frozenset[UUID] = frozenset(),
) -> list[CheckedLine]:
    errors, lines = purchase_errors(
        ctx,
        project,
        data,
        catalog if catalog is not None else load_catalog(ctx, project),
        tasks if tasks is not None else assignable_tasks(ctx, project),
        today=timezone.localdate(),
        keep=keep,
    )
    if errors:
        raise MaterialRejected(errors)
    return lines


def _fields(data: PurchaseData) -> dict[str, Any]:
    return {
        "kind": data.kind,
        "warehouse_id": data.warehouse_id if data.kind == PurchaseKind.WAREHOUSE else None,
        "supplier": data.supplier.strip(),
        "supplier_tax_id": data.supplier_tax_id.strip(),
        "document_type": data.document_type,
        "document_number": data.document_number.strip(),
        "purchased_on": data.purchased_on,
    }


def _write_lines(
    ctx: RequestContext, purchase: MaterialPurchase, lines: Sequence[CheckedLine]
) -> None:
    scoped_qs(ctx, MaterialPurchaseLine).filter(purchase=purchase).delete()
    rows = scoped_qs(ctx, MaterialPurchaseLine).bulk_create(
        [
            MaterialPurchaseLine(
                org_id=ctx.org_id,
                purchase=purchase,
                order=line.order,
                material=line.material,
                quantity=line.quantity,
                unit_price=line.unit_price,
                amount=line.amount,
                material_label=material_label(line.material),
                scope=line.assignment.scope if line.assignment else "",
                chapter_id=line.assignment.chapter_id if line.assignment else None,
                task_id=line.assignment.task_id if line.assignment else None,
            )
            for line in lines
        ]
    )
    scoped_qs(ctx, MaterialPurchaseLineItem).bulk_create(
        [
            MaterialPurchaseLineItem(org_id=ctx.org_id, line=row, item_id=item_id)
            for row, line in zip(rows, lines, strict=True)
            if line.assignment is not None
            for item_id in line.assignment.item_ids
        ]
    )


def create_purchase(
    ctx: RequestContext,
    project: Project,
    data: PurchaseData,
    *,
    upload: Upload | None = None,
    name: str = "",
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> MaterialPurchase:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    lines = _checked(ctx, project, data, catalog, tasks)
    blob = stored_upload(upload)
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = scoped_qs(ctx, MaterialPurchase).create(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, MaterialPurchase, project),
            status=str(MaterialStatus.DRAFT),
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            attachment=blob,
            attachment_name=name[:MAX_FILE_NAME] if blob is not None else "",
            total=sum((line.amount for line in lines), ZERO),
            **_fields(data),
        )
        _write_lines(ctx, purchase, lines)
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_PURCHASE_CREATED,
            target=f"material_purchase:{purchase.pk}",
            metadata=purchase_metadata(purchase),
        )
    return purchase


def _current(ctx: RequestContext, project: Project, purchase_id: UUID) -> MaterialPurchase:
    current = scoped_qs(ctx, MaterialPurchase).filter(pk=purchase_id, project=project).first()
    if current is None:
        raise Http404
    return current


def update_purchase(
    ctx: RequestContext,
    project: Project,
    purchase_id: UUID,
    data: PurchaseData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> MaterialPurchase:
    """Guarda un borrador; un rechazado (solo su autor) vuelve a borrador. La factura queda."""
    current = _current(ctx, project, purchase_id)
    require_editable(ctx, current, can_update_cost)
    keep = frozenset(
        scoped_qs(ctx, MaterialPurchaseLine)
        .filter(purchase=current)
        .values_list("material_id", flat=True)
    )
    lines = _checked(ctx, project, data, catalog, tasks, keep)
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = locked(ctx, MaterialPurchase, project, purchase_id)
        require_editable(ctx, purchase, can_update_cost)
        resubmitted = purchase.status == MaterialStatus.REJECTED
        for field, value in _fields(data).items():
            setattr(purchase, field, value)
        purchase.total = sum((line.amount for line in lines), ZERO)
        purchase.status = str(MaterialStatus.DRAFT)
        purchase.decided_by = None
        purchase.decided_at = None
        purchase.decision_note = ""
        purchase.updated_by_id = ctx.user_id
        purchase.save()
        _write_lines(ctx, purchase, lines)
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_PURCHASE_UPDATED,
            target=f"material_purchase:{purchase.pk}",
            metadata={**purchase_metadata(purchase), "resubmitted": resubmitted},
        )
    return purchase


def delete_purchase(ctx: RequestContext, project: Project, purchase_id: UUID) -> None:
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = locked(ctx, MaterialPurchase, project, purchase_id)
        require_editable(ctx, purchase, can_delete_cost)
        metadata = {**purchase_metadata(purchase), "supplier": purchase.supplier}
        target = f"material_purchase:{purchase.pk}"
        purchase.delete()
        audit.record(
            ctx, action=AuditAction.MATERIAL_PURCHASE_DELETED, target=target, metadata=metadata
        )


def _require_attachable(ctx: RequestContext, purchase: MaterialPurchase) -> None:
    if purchase.status not in OPEN_STATES:
        raise MaterialRejected(["La factura se adjunta a una compra en borrador o rechazada."])
    if not can_update_cost(ctx, purchase):
        raise PermissionDenied


def attach_support(
    ctx: RequestContext, project: Project, purchase_id: UUID, upload: Upload, name: str
) -> MaterialPurchase:
    """Adjunta la factura. No es editar: `updated_by` no cambia (quien la escanea puede aprobar
    si no registró la compra). El archivo se sube antes del bloqueo."""
    _require_attachable(ctx, _current(ctx, project, purchase_id))
    blob = stored_upload(upload)
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = locked(ctx, MaterialPurchase, project, purchase_id)
        _require_attachable(ctx, purchase)
        purchase.attachment = blob
        purchase.attachment_name = name[:MAX_FILE_NAME]
        purchase.save(update_fields=["attachment", "attachment_name"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_PURCHASE_UPDATED,
            target=f"material_purchase:{purchase.pk}",
            metadata={**purchase_metadata(purchase), "support": True},
        )
    return purchase


def purchase_lines(ctx: RequestContext, purchase: MaterialPurchase) -> list[MaterialPurchaseLine]:
    return list(
        scoped_qs(ctx, MaterialPurchaseLine)
        .filter(purchase=purchase)
        .select_related("material", "task")
        .prefetch_related("item_links")
        .order_by("order")
    )


def material_label(material: Material) -> str:
    return f"{material.code} {material.name}"


def third_party(purchase: MaterialPurchase) -> str:
    tax = purchase.supplier_tax_id
    return (f"{purchase.supplier} (NIT {tax})" if tax else purchase.supplier)[:MAX_THIRD_PARTY]


def _post_direct(
    ctx: RequestContext,
    project: Project,
    purchase: MaterialPurchase,
    lines: Sequence[MaterialPurchaseLine],
) -> int:
    """Un costo aprobado por línea (spec 2.3): tipo Materiales, fecha de la compra, el
    proveedor, el documento y la factura; registró y modificó, los de la compra. El material,
    como quedó registrado en la compra (#88); una compra anterior usa el nombre vigente."""
    now = timezone.now()
    entries: list[CostEntry] = []
    links: list[CostEntryItem] = []
    for line in lines:
        entry = CostEntry(
            org_id=ctx.org_id,
            project=project,
            incurred_on=purchase.purchased_on,
            kind=CostKind.MATERIALS,
            description=(
                f"Compra {purchase.code} · {line.material_label or material_label(line.material)}"
            )[:MAX_DESCRIPTION],
            amount=line.amount,
            third_party=third_party(purchase),
            document_type=purchase.document_type,
            document_number=purchase.document_number[:MAX_DOCUMENT_NUMBER],
            scope=line.scope,
            chapter_id=line.chapter_id,
            task_id=line.task_id,
            status=CostStatus.APPROVED,
            created_by_id=purchase.created_by_id,
            updated_by_id=purchase.updated_by_id,
            decided_by_id=ctx.user_id,
            decided_at=now,
            attachment_id=purchase.attachment_id,
            attachment_name=purchase.attachment_name,
            material_purchase=purchase,
        )
        entries.append(entry)
        links += [
            CostEntryItem(org_id=ctx.org_id, entry=entry, item_id=link.item_id)
            for link in line.item_links.all()
        ]
    scoped_qs(ctx, CostEntry).bulk_create(entries)
    scoped_qs(ctx, CostEntryItem).bulk_create(links)
    return len(entries)


def approve_purchase(ctx: RequestContext, project: Project, purchase_id: UUID) -> MaterialPurchase:
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = locked(ctx, MaterialPurchase, project, purchase_id)
        if not can_approve_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, purchase)
        if purchase.attachment_id is None:
            raise MaterialRejected(["Adjunta la factura antes de aprobar."])
        lines = purchase_lines(ctx, purchase)
        entries = 0
        if purchase.kind == PurchaseKind.WAREHOUSE:
            warehouse_id = purchase.warehouse_id
            if not scoped_qs(ctx, Warehouse).filter(pk=warehouse_id, active=True).exists():
                raise MaterialRejected(
                    ["La bodega de la compra está inactiva: actívala o cambia la bodega."]
                )
            stock = Stock(ctx, project, [(warehouse_id, line.material_id) for line in lines])
            for line in lines:
                stock.add(
                    str(MovementKind.PURCHASE),
                    warehouse_id,
                    line.material_id,
                    line.quantity,
                    line.amount,
                    purchase.purchased_on,
                    purchase=purchase,
                )
            stock.save()
        else:
            entries = _post_direct(ctx, project, purchase, lines)
        purchase.status = str(MaterialStatus.APPROVED)
        purchase.decided_by_id = ctx.user_id
        purchase.decided_at = timezone.now()
        purchase.decision_note = ""
        purchase.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_PURCHASE_APPROVED,
            target=f"material_purchase:{purchase.pk}",
            metadata={**purchase_metadata(purchase), "entries": entries},
        )
    return purchase


def reject_purchase(
    ctx: RequestContext, project: Project, purchase_id: UUID, note: str
) -> MaterialPurchase:
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = locked(ctx, MaterialPurchase, project, purchase_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, purchase)
        purchase.status = str(MaterialStatus.REJECTED)
        purchase.decided_by_id = ctx.user_id
        purchase.decided_at = timezone.now()
        purchase.decision_note = reason
        purchase.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_PURCHASE_REJECTED,
            target=f"material_purchase:{purchase.pk}",
            metadata={**purchase_metadata(purchase), "note": reason},
        )
    return purchase


def void_purchase(
    ctx: RequestContext, project: Project, purchase_id: UUID, note: str
) -> MaterialPurchase:
    reason = clean_note(note, "de la anulación")
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = locked(ctx, MaterialPurchase, project, purchase_id)
        if not can_reverse_costs(ctx, project):
            raise PermissionDenied
        if purchase.status != MaterialStatus.APPROVED:
            raise MaterialRejected(["Solo se anula una compra aprobada."])
        reversals = 0
        if purchase.kind == PurchaseKind.WAREHOUSE:
            warehouse_id = purchase.warehouse_id
            lines = purchase_lines(ctx, purchase)
            stock = Stock(ctx, project, [(warehouse_id, line.material_id) for line in lines])
            needed: dict[UUID, Decimal] = defaultdict(Decimal)
            materials: dict[UUID, Material] = {}
            for line in lines:
                needed[line.material_id] += line.quantity
                materials[line.material_id] = line.material
            errors = [
                f"No se puede anular: de {materials[m].name} quedan "
                f"{qty(stock.balance(warehouse_id, m).quantity) or '0'} {materials[m].unit} en la "
                f"bodega y la compra trajo {qty(amount)}."
                for m, amount in needed.items()
                if stock.balance(warehouse_id, m).quantity < amount
            ]
            if errors:
                raise MaterialRejected(errors)
            today = timezone.localdate()
            for line in lines:
                stock.withdraw(
                    str(MovementKind.PURCHASE_VOID),
                    warehouse_id,
                    line.material_id,
                    line.quantity,
                    line.amount,
                    today,
                    purchase=purchase,
                )
            stock.save()
        else:
            reversals = reverse_entries(ctx, project, "material_purchase", purchase, reason)
        purchase.status = str(MaterialStatus.VOIDED)
        purchase.voided_by_id = ctx.user_id
        purchase.voided_at = timezone.now()
        purchase.void_note = reason
        purchase.save(update_fields=["status", "voided_by", "voided_at", "void_note"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_PURCHASE_VOIDED,
            target=f"material_purchase:{purchase.pk}",
            metadata={**purchase_metadata(purchase), "note": reason, "reversals": reversals},
        )
    return purchase
