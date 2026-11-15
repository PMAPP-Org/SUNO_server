"""Salidas, devoluciones y traslados (costos, pieza 6c, spec 2.4).

- Se registran sin aprobación y mueven el inventario al guardarse (`kardex.Stock`).
- Salida: sale al promedio de su momento; costo unitario y valor de cada línea quedan fijos.
  Mientras no esté en un corte ni tenga devoluciones se edita o se borra: sus movimientos se
  deshacen (decisión 8 del plan) y, al editar, se aplica de nuevo al promedio del momento.
- Devolución: por línea de salida, lo devuelto acumulado no pasa de lo que salió; entra al costo
  de la línea (la que completa se lleva el valor exacto que faltaba). Bodega y destino, los de
  la salida. No se edita ni se borra.
- Traslado: sale de una bodega al promedio y entra a otra del mismo proyecto con el mismo valor.
  No se edita ni se borra.
- Periodo cerrado: nada con fecha menor o igual al fin del último corte aprobado, ni futura.
- `client_id` (la futura app): si la salida ya existe en el proyecto, se devuelve esa.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from suno.budgets.totals import ZERO
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    ConsumptionCut,
    Material,
    MaterialIssue,
    MaterialIssueItem,
    MaterialIssueLine,
    MaterialReturn,
    MaterialReturnLine,
    MaterialStatus,
    MaterialTransfer,
    MaterialTransferLine,
    MovementKind,
    Project,
    StockMovement,
    Task,
    Warehouse,
)
from suno.core.policies import can_create_costs, can_delete_cost, can_update_cost
from suno.core.scoping import scoped_qs
from suno.core.templatetags.money_tags import qty
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.costs.services import Assignment

from .common import (
    FIRST_DAY,
    MAX_LINES,
    MAX_NOTE,
    MaterialRejected,
    lock_project,
    locked,
    next_number,
    quantity_ok,
)
from .destination import DestinationData, check_destination, destination_fields, link_items
from .kardex import Stock
from .stock import returned_value

MAX_RECEIVER = 120
IN_A_CUT = "La salida ya está en un corte: no se edita ni se borra."
HAS_RETURNS = "La salida tiene devoluciones: no se edita ni se borra."


@dataclass(frozen=True)
class IssueLineData:
    material_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class IssueData:
    warehouse_id: UUID | None
    issued_on: date
    destination: DestinationData
    lines: tuple[IssueLineData, ...]
    received_by: str = ""
    notes: str = ""
    client_id: UUID | None = None


@dataclass(frozen=True)
class ReturnLineData:
    issue_line_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class ReturnData:
    returned_on: date
    lines: tuple[ReturnLineData, ...]
    notes: str = ""


@dataclass(frozen=True)
class TransferData:
    source_id: UUID | None
    target_id: UUID | None
    moved_on: date
    lines: tuple[IssueLineData, ...]
    notes: str = ""


def closed_until(ctx: RequestContext, project: Project) -> date | None:
    """El fin del último corte aprobado: hasta ahí el periodo está cerrado."""
    return (
        scoped_qs(ctx, ConsumptionCut)
        .filter(project=project, status=MaterialStatus.APPROVED)
        .aggregate(end=Max("period_end"))["end"]
    )


def day_problem(
    ctx: RequestContext, project: Project, day: date, today: date, what: str = "La fecha"
) -> str:
    """Vacío si la fecha sirve para una salida, una devolución o un traslado."""
    if not FIRST_DAY <= day <= today:
        return f"{what} no puede ser futura ni anterior a 2000."
    closed = closed_until(ctx, project)
    if closed is not None and day <= closed:
        return (
            f"{what} cae en un periodo ya cortado (hasta el {closed:%d/%m/%Y}): "
            "usa una fecha posterior."
        )
    return ""


def active_warehouse(
    ctx: RequestContext, project: Project, warehouse_id: UUID | None
) -> Warehouse | None:
    if warehouse_id is None:
        return None
    return scoped_qs(ctx, Warehouse).filter(pk=warehouse_id, project=project, active=True).first()


def line_errors(
    ctx: RequestContext, lines: Sequence[IssueLineData], keep: frozenset[UUID] = frozenset()
) -> list[str]:
    """Materiales activos (o que ya tenía el documento) y cantidades válidas."""
    errors: list[str] = []
    if not lines:
        errors.append("Agrega al menos una línea.")
    elif len(lines) > MAX_LINES:
        errors.append(f"Un documento lleva hasta {MAX_LINES} líneas.")
    materials = {
        material.pk: material
        for material in scoped_qs(ctx, Material).filter(pk__in=[line.material_id for line in lines])
    }
    for n, line in enumerate(lines, start=1):
        material = materials.get(line.material_id)
        if material is None or not (material.active or material.pk in keep):
            errors.append(f"Línea {n}: Elige un material activo del catálogo.")
        if not quantity_ok(line.quantity):
            errors.append(f"Línea {n}: La cantidad es mayor que cero, con máximo 4 decimales.")
    return errors


def _issue_checked(
    ctx: RequestContext,
    project: Project,
    data: IssueData,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    keep: frozenset[UUID],
    today: date,
) -> tuple[Warehouse, Assignment]:
    """Con el proyecto bloqueado: bodega activa, fecha abierta, destino y líneas."""
    errors: list[str] = []
    warehouse = active_warehouse(ctx, project, data.warehouse_id)
    if warehouse is None:
        errors.append("Elige una bodega activa del proyecto.")
    problem = day_problem(ctx, project, data.issued_on, today)
    if problem:
        errors.append(problem)
    if len(data.received_by.strip()) > MAX_RECEIVER:
        errors.append(f"Quién recibe pasa de {MAX_RECEIVER} caracteres.")
    if len(data.notes.strip()) > MAX_NOTE:
        errors.append(f"Las notas pasan de {MAX_NOTE} caracteres.")
    found, assignment = check_destination(catalog, tasks, data.destination)
    errors += found
    errors += line_errors(ctx, data.lines, keep)
    if errors or warehouse is None or assignment is None:
        raise MaterialRejected(errors)
    return warehouse, assignment


def issue_metadata(issue: MaterialIssue) -> dict[str, Any]:
    return {
        "project": str(issue.project_id),
        "issue": str(issue.pk),
        "number": issue.number,
        "warehouse": str(issue.warehouse_id),
        "issued_on": issue.issued_on.isoformat(),
        "scope": issue.scope,
        "total": str(issue.total),
    }


def _take_lines(
    ctx: RequestContext, stock: Stock, issue: MaterialIssue, lines: Sequence[IssueLineData]
) -> Decimal:
    rows: list[MaterialIssueLine] = []
    total = ZERO
    for n, line in enumerate(lines, start=1):
        movement = stock.take(
            str(MovementKind.ISSUE),
            issue.warehouse_id,
            line.material_id,
            line.quantity,
            issue.issued_on,
            issue=issue,
        )
        rows.append(
            MaterialIssueLine(
                org_id=ctx.org_id,
                issue=issue,
                order=n,
                material_id=line.material_id,
                quantity=line.quantity,
                unit_cost=movement.unit_cost,
                amount=-movement.value,
            )
        )
        total += -movement.value
    scoped_qs(ctx, MaterialIssueLine).bulk_create(rows)
    return total


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


def create_issue(
    ctx: RequestContext,
    project: Project,
    data: IssueData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> MaterialIssue:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    catalog, tasks = _defaults(ctx, project, catalog, tasks)
    today = timezone.localdate()
    with transaction.atomic():
        lock_project(ctx, project)
        if data.client_id is not None:
            existing = scoped_qs(ctx, MaterialIssue).filter(client_id=data.client_id).first()
            if existing is not None:
                if existing.project_id != project.pk:
                    raise MaterialRejected(
                        ["Ese identificador de salida ya se usó en otro proyecto."]
                    )
                return existing
        warehouse, assignment = _issue_checked(
            ctx, project, data, catalog, tasks, frozenset(), today
        )
        issue = scoped_qs(ctx, MaterialIssue).create(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, MaterialIssue, project),
            warehouse=warehouse,
            issued_on=data.issued_on,
            received_by=data.received_by.strip(),
            notes=data.notes.strip(),
            client_id=data.client_id,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            **destination_fields(assignment),
        )
        link_items(ctx, MaterialIssueItem, "issue", issue, assignment.item_ids)
        stock = Stock(ctx, project, [(warehouse.pk, line.material_id) for line in data.lines])
        issue.total = _take_lines(ctx, stock, issue, data.lines)
        stock.save()
        issue.save(update_fields=["total"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_ISSUE_CREATED,
            target=f"material_issue:{issue.pk}",
            metadata=issue_metadata(issue),
        )
    return issue


def _require_changeable(
    ctx: RequestContext,
    project: Project,
    issue: MaterialIssue,
    today: date,
    policy: Callable[[RequestContext, Any], bool],
) -> None:
    if not policy(ctx, issue):
        raise PermissionDenied
    if issue.cut_id is not None:
        raise MaterialRejected([IN_A_CUT])
    if scoped_qs(ctx, MaterialReturn).filter(issue=issue).exists():
        raise MaterialRejected([HAS_RETURNS])
    problem = day_problem(ctx, project, issue.issued_on, today)
    if problem:
        raise MaterialRejected([problem])


def _pairs(movements: Iterable[StockMovement]) -> set[tuple[UUID, UUID]]:
    return {(movement.warehouse_id, movement.material_id) for movement in movements}


def update_issue(
    ctx: RequestContext,
    project: Project,
    issue_id: UUID,
    data: IssueData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> MaterialIssue:
    """Deshace los movimientos de la salida y la aplica de nuevo al promedio del momento."""
    catalog, tasks = _defaults(ctx, project, catalog, tasks)
    today = timezone.localdate()
    with transaction.atomic():
        lock_project(ctx, project)
        issue = locked(ctx, MaterialIssue, project, issue_id)
        _require_changeable(ctx, project, issue, today, can_update_cost)
        keep = frozenset(
            scoped_qs(ctx, MaterialIssueLine)
            .filter(issue=issue)
            .values_list("material_id", flat=True)
        )
        warehouse, assignment = _issue_checked(ctx, project, data, catalog, tasks, keep, today)
        old = list(scoped_qs(ctx, StockMovement).filter(issue=issue))
        stock = Stock(
            ctx,
            project,
            _pairs(old) | {(warehouse.pk, line.material_id) for line in data.lines},
        )
        stock.restore(old)
        scoped_qs(ctx, MaterialIssueLine).filter(issue=issue).delete()
        issue.warehouse = warehouse
        issue.issued_on = data.issued_on
        issue.received_by = data.received_by.strip()
        issue.notes = data.notes.strip()
        for field, value in destination_fields(assignment).items():
            setattr(issue, field, value)
        issue.updated_by_id = ctx.user_id
        link_items(ctx, MaterialIssueItem, "issue", issue, assignment.item_ids)
        issue.total = _take_lines(ctx, stock, issue, data.lines)
        stock.save()
        issue.save()
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_ISSUE_UPDATED,
            target=f"material_issue:{issue.pk}",
            metadata=issue_metadata(issue),
        )
    return issue


def delete_issue(ctx: RequestContext, project: Project, issue_id: UUID) -> None:
    today = timezone.localdate()
    with transaction.atomic():
        lock_project(ctx, project)
        issue = locked(ctx, MaterialIssue, project, issue_id)
        _require_changeable(ctx, project, issue, today, can_delete_cost)
        old = list(scoped_qs(ctx, StockMovement).filter(issue=issue))
        stock = Stock(ctx, project, _pairs(old))
        stock.restore(old)
        stock.save()
        metadata = issue_metadata(issue)
        target = f"material_issue:{issue.pk}"
        issue.delete()
        audit.record(
            ctx, action=AuditAction.MATERIAL_ISSUE_DELETED, target=target, metadata=metadata
        )


def returned_by_line(
    ctx: RequestContext, line_ids: Iterable[UUID]
) -> dict[UUID, tuple[Decimal, Decimal]]:
    """Lo devuelto hasta ahora por línea de salida: (cantidad, valor)."""
    return {
        line_id: (quantity, value)
        for line_id, quantity, value in scoped_qs(ctx, MaterialReturnLine)
        .filter(issue_line_id__in=list(line_ids))
        .values("issue_line_id")
        .annotate(quantity=Sum("quantity"), value=Sum("amount"))
        .values_list("issue_line_id", "quantity", "value")
    }


def create_return(
    ctx: RequestContext, project: Project, issue_id: UUID, data: ReturnData
) -> MaterialReturn:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    today = timezone.localdate()
    with transaction.atomic():
        lock_project(ctx, project)
        issue = locked(ctx, MaterialIssue, project, issue_id)
        lines = {
            line.pk: line
            for line in scoped_qs(ctx, MaterialIssueLine)
            .filter(issue=issue)
            .select_related("material")
        }
        errors: list[str] = []
        problem = day_problem(ctx, project, data.returned_on, today)
        if problem:
            errors.append(problem)
        if data.returned_on < issue.issued_on:
            errors.append("La devolución no puede ser anterior a la salida.")
        if len(data.notes.strip()) > MAX_NOTE:
            errors.append(f"Las notas pasan de {MAX_NOTE} caracteres.")
        wanted: dict[UUID, Decimal] = defaultdict(Decimal)
        for entry in data.lines:
            if entry.quantity:
                wanted[entry.issue_line_id] += entry.quantity
        if not wanted:
            errors.append("Escribe lo que vuelve en al menos una línea.")
        before = returned_by_line(ctx, lines)
        for line_id, amount in wanted.items():
            line = lines.get(line_id)
            if line is None:
                errors.append("Hay líneas que no son de esta salida.")
                continue
            if not quantity_ok(amount):
                errors.append(f"{line.material.name}: La cantidad es mayor que cero, con máximo "
                              "4 decimales.")  # fmt: skip
                continue
            total = before.get(line_id, (ZERO, ZERO))[0] + amount
            if total > line.quantity:
                errors.append(
                    f"{line.material.name}: con esta devolución vuelven {qty(total)} "
                    f"{line.material.unit} y salieron {qty(line.quantity)}."
                )
        if errors:
            raise MaterialRejected(list(dict.fromkeys(errors)))
        back = scoped_qs(ctx, MaterialReturn).create(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, MaterialReturn, project),
            issue=issue,
            returned_on=data.returned_on,
            notes=data.notes.strip(),
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
        stock = Stock(
            ctx, project, [(issue.warehouse_id, lines[line_id].material_id) for line_id in wanted]
        )
        rows: list[MaterialReturnLine] = []
        total_value = ZERO
        for line_id, amount in wanted.items():
            line = lines[line_id]
            done_quantity, done_value = before.get(line_id, (ZERO, ZERO))
            value = returned_value(
                line.quantity, line.amount, line.unit_cost, done_quantity, done_value, amount
            )
            stock.add(
                str(MovementKind.RETURN),
                issue.warehouse_id,
                line.material_id,
                amount,
                value,
                data.returned_on,
                material_return=back,
            )
            rows.append(
                MaterialReturnLine(
                    org_id=ctx.org_id,
                    material_return=back,
                    issue_line=line,
                    quantity=amount,
                    unit_cost=line.unit_cost,
                    amount=value,
                )
            )
            total_value += value
        scoped_qs(ctx, MaterialReturnLine).bulk_create(rows)
        stock.save()
        back.total = total_value
        back.save(update_fields=["total"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_RETURN_CREATED,
            target=f"material_return:{back.pk}",
            metadata={
                "project": str(project.pk),
                "return": str(back.pk),
                "issue": str(issue.pk),
                "number": back.number,
                "total": str(back.total),
            },
        )
    return back


def create_transfer(ctx: RequestContext, project: Project, data: TransferData) -> MaterialTransfer:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    today = timezone.localdate()
    with transaction.atomic():
        lock_project(ctx, project)
        source = active_warehouse(ctx, project, data.source_id)
        target = active_warehouse(ctx, project, data.target_id)
        errors: list[str] = []
        if source is None:
            errors.append("Elige la bodega de donde sale.")
        if target is None:
            errors.append("Elige la bodega a donde entra.")
        if source is not None and target is not None and source.pk == target.pk:
            errors.append("Un traslado va entre dos bodegas distintas.")
        problem = day_problem(ctx, project, data.moved_on, today)
        if problem:
            errors.append(problem)
        if len(data.notes.strip()) > MAX_NOTE:
            errors.append(f"Las notas pasan de {MAX_NOTE} caracteres.")
        errors += line_errors(ctx, data.lines)
        if errors or source is None or target is None:
            raise MaterialRejected(errors)
        transfer = scoped_qs(ctx, MaterialTransfer).create(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, MaterialTransfer, project),
            source=source,
            target=target,
            moved_on=data.moved_on,
            notes=data.notes.strip(),
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
        stock = Stock(
            ctx,
            project,
            [(w.pk, line.material_id) for line in data.lines for w in (source, target)],
        )
        rows: list[MaterialTransferLine] = []
        total = ZERO
        for n, line in enumerate(data.lines, start=1):
            out = stock.take(
                str(MovementKind.TRANSFER_OUT), source.pk, line.material_id, line.quantity,
                data.moved_on, transfer=transfer,
            )  # fmt: skip
            stock.add(
                str(MovementKind.TRANSFER_IN), target.pk, line.material_id, line.quantity,
                -out.value, data.moved_on, transfer=transfer,
            )  # fmt: skip
            rows.append(
                MaterialTransferLine(
                    org_id=ctx.org_id,
                    transfer=transfer,
                    order=n,
                    material_id=line.material_id,
                    quantity=line.quantity,
                    unit_cost=out.unit_cost,
                    amount=-out.value,
                )
            )
            total += -out.value
        scoped_qs(ctx, MaterialTransferLine).bulk_create(rows)
        stock.save()
        transfer.total = total
        transfer.save(update_fields=["total"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_TRANSFER_CREATED,
            target=f"material_transfer:{transfer.pk}",
            metadata={
                "project": str(project.pk),
                "transfer": str(transfer.pk),
                "number": transfer.number,
                "source": str(source.pk),
                "target": str(target.pk),
                "total": str(total),
            },
        )
    return transfer
