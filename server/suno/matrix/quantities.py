"""Lecturas de la matriz con BD: lo ejecutado validado, los datos de cada ítem y el
% SUNO combinado (por cantidades donde hay plan, % manual en el resto)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from django.db.models import QuerySet

from suno.core.context import RequestContext
from suno.core.models import (
    BudgetItemVersion,
    BudgetRowKind,
    BudgetVersionStatus,
    QuantityPlan,
    Record,
)
from suno.core.scoping import scoped_qs
from suno.projects.field_progress import approved, not_rejected, validated_pct_by_task

from .progress import PlanLine, QuantityProgress, task_progress

Pair = tuple[UUID, UUID]


def _ids(values: Iterable[UUID] | None) -> list[UUID] | None:
    return None if values is None else list(values)


def _by_scope(
    qs: QuerySet, task_ids: list[UUID] | None, project_ids: list[UUID] | None, *, task: str
) -> QuerySet:
    if task_ids is not None:
        qs = qs.filter(**{f"{task}_id__in": task_ids})
    if project_ids is not None:
        qs = qs.filter(**{f"{task}__project_id__in": project_ids})
    return qs


def executed_by_pair(
    ctx: RequestContext,
    *,
    task_ids: Iterable[UUID] | None = None,
    project_ids: Iterable[UUID] | None = None,
    include_pending: bool = False,
) -> dict[Pair, Decimal]:
    """(tarea, ítem) → suma de las cantidades de sus registros validados. Una query.

    `include_pending`: también los registros aún sin revisar (no los rechazados). Lo usa
    quien decide si un par "tiene ejecución" para no borrarlo del plan: un registro
    pendiente puede validarse después."""
    tasks, projects = _ids(task_ids), _ids(project_ids)
    if tasks == [] or projects == []:
        return {}
    records = (
        scoped_qs(ctx, Record)
        .filter(task__isnull=False, deleted_at__isnull=True)
        .exclude(quantities=[])
    )
    records = _by_scope(records, tasks, projects, task="task")
    counted = not_rejected(records) if include_pending else approved(records)
    totals: dict[Pair, Decimal] = defaultdict(Decimal)
    for task_id, rows in counted.values_list("task_id", "quantities"):
        for row in rows:
            totals[(task_id, UUID(row["item_id"]))] += Decimal(row["quantity"])
    return dict(totals)


@dataclass(frozen=True)
class QuantityRecord:
    """Una fila de cantidad de un registro validado (historial de la ficha de tarea)."""

    record: Record
    item_id: UUID
    #: `None`: la ubicación de la tarea.
    location_id: UUID | None
    quantity: Decimal
    note: str


def validated_records(
    ctx: RequestContext, task_id: UUID, *, max_records: int
) -> tuple[list[QuantityRecord], bool]:
    """Las cantidades de los `max_records` registros validados más recientes de la tarea,
    con su autor; y si quedaron registros por fuera. Una query."""
    records = list(
        approved(scoped_qs(ctx, Record).filter(task_id=task_id, deleted_at__isnull=True))
        .exclude(quantities=[])
        .select_related("created_by")
        .order_by("-captured_at", "-change_seq")[: max_records + 1]
    )
    history = [
        QuantityRecord(
            record=record,
            item_id=UUID(row["item_id"]),
            location_id=UUID(row["location_id"]) if row["location_id"] else None,
            quantity=Decimal(row["quantity"]),
            note=row["note"],
        )
        for record in records[:max_records]
        for row in record.quantities
    ]
    return history, len(records) > max_records


@dataclass(frozen=True)
class ItemFacts:
    code: str
    kind: str
    description: str
    unit: str
    unit_price: Decimal | None


def item_facts(ctx: RequestContext, item_ids: Iterable[UUID]) -> dict[UUID, ItemFacts]:
    """Datos de cada ítem en la última versión aplicada que lo contiene. Una query."""
    ids = list(set(item_ids))
    if not ids:
        return {}
    rows = (
        scoped_qs(ctx, BudgetItemVersion)
        .filter(item_id__in=ids, version__status=BudgetVersionStatus.APPLIED)
        .order_by("item_id", "-version__number")
        .distinct("item_id")
        .values_list("item_id", "item__code", "kind", "description", "unit", "unit_price")
    )
    return {
        item_id: ItemFacts(code, kind, description, unit, price)
        for item_id, code, kind, description, unit, price in rows
    }


def plan_lines_with_facts(
    ctx: RequestContext,
    *,
    task_ids: Iterable[UUID] | None = None,
    project_ids: Iterable[UUID] | None = None,
) -> tuple[list[PlanLine], dict[UUID, ItemFacts]]:
    """Las filas del plan que miden, con lo ejecutado validado y el precio, y los datos de
    sus ítems. Tres queries.

    Sin las filas cuyo ítem es hoy un capítulo: una reimportación puede volver capítulo
    un ítem planeado (el 2.1 se parte en 2.1.1 y 2.1.2) y la fila del plan sobrevive, pero
    un encabezado no mide la tarea. Filtrar aquí hace que la ficha de la tarea, las tareas
    de campo y el cronograma lean el mismo avance."""
    tasks, projects = _ids(task_ids), _ids(project_ids)
    if tasks == [] or projects == []:
        return [], {}
    plans = _by_scope(scoped_qs(ctx, QuantityPlan), tasks, projects, task="task")
    rows = list(plans.values_list("task_id", "item_id", "planned_quantity"))
    if not rows:
        return [], {}
    executed = executed_by_pair(ctx, task_ids=tasks, project_ids=projects)
    facts = item_facts(ctx, (item_id for _, item_id, _ in rows))
    lines = [
        PlanLine(
            task_id=task_id,
            item_id=item_id,
            planned=planned,
            executed=executed.get((task_id, item_id), Decimal(0)),
            unit_price=facts[item_id].unit_price if item_id in facts else None,
        )
        for task_id, item_id, planned in rows
        if item_id not in facts or facts[item_id].kind != BudgetRowKind.CHAPTER
    ]
    return lines, facts


def plan_lines(
    ctx: RequestContext,
    *,
    task_ids: Iterable[UUID] | None = None,
    project_ids: Iterable[UUID] | None = None,
) -> list[PlanLine]:
    """Las filas del plan que miden (sin capítulos), con lo ejecutado validado y el precio.
    Tres queries."""
    return plan_lines_with_facts(ctx, task_ids=task_ids, project_ids=project_ids)[0]


def measured_progress(
    ctx: RequestContext,
    *,
    task_ids: Iterable[UUID] | None = None,
    project_ids: Iterable[UUID] | None = None,
) -> dict[UUID, QuantityProgress]:
    return task_progress(plan_lines(ctx, task_ids=task_ids, project_ids=project_ids))


def suno_pct_by_task(
    ctx: RequestContext,
    *,
    task_ids: Iterable[UUID] | None = None,
    project_ids: Iterable[UUID] | None = None,
    measured: Mapping[UUID, QuantityProgress] | None = None,
) -> dict[UUID, Decimal]:
    """% SUNO: por cantidades en las tareas con plan; el último % validado en el resto.

    `measured`: el avance por cantidades ya calculado (para no repetir las queries)."""
    tasks, projects = _ids(task_ids), _ids(project_ids)
    if tasks == [] or projects == []:
        return {}
    manual = validated_pct_by_task(scoped_qs(ctx, Record), task_ids=tasks, project_ids=projects)
    if measured is None:
        measured = measured_progress(ctx, task_ids=tasks, project_ids=projects)
    return {**manual, **{task_id: p.pct for task_id, p in measured.items()}}
