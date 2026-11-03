"""Lo que muestran las páginas de la matriz: por ítem y por ubicación.

Coste fijo en queries, sin importar el tamaño de la matriz."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from visitacampo.budgets.totals import HUNDRED, money
from visitacampo.core.context import RequestContext
from visitacampo.core.models import (
    Budget,
    BudgetItemVersion,
    Project,
    QuantityPlan,
    Task,
)
from visitacampo.core.scoping import scoped_qs

from .diff import code_key
from .importer import current_versions
from .locations import SEPARATOR, Tree, load_tree
from .progress import PCT_STEP
from .quantities import executed_by_pair, item_facts

_ZERO = Decimal(0)

STATUS_LABELS = {
    "over": "Con exceso",
    "under": "Sin asignar completo",
    "ok": "Cuadra",
    "retired": "Retirado del presupuesto",
}


@dataclass(frozen=True)
class TaskQuantity:
    task: Task
    location: str
    planned: Decimal
    executed: Decimal


@dataclass(frozen=True)
class ItemRow:
    item_id: UUID
    budget: Budget | None
    code: str
    description: str
    unit: str
    #: Cantidad en la versión vigente; None si el ítem se retiró del presupuesto.
    budgeted: Decimal | None
    planned: Decimal
    executed: Decimal
    #: "ok", "over", "under" o "retired" (sobre el total del ítem, sin filtros).
    status: str
    #: Capítulos que lo contienen (sus ids), para el filtro por capítulo.
    chapters: frozenset[UUID]
    tasks: tuple[TaskQuantity, ...]

    @property
    def pct_executed(self) -> Decimal | None:
        if self.planned <= 0:
            return None
        return (max(self.executed, _ZERO) * HUNDRED / self.planned).quantize(
            PCT_STEP, rounding=ROUND_HALF_UP
        )

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def unassigned(self) -> Decimal | None:
        if self.budgeted is None:
            return None
        return max(self.budgeted - self.planned, _ZERO)


def item_rows(ctx: RequestContext, project: Project) -> list[ItemRow]:
    budgets = {b.pk: b for b in scoped_qs(ctx, Budget).filter(project=project)}
    versions = current_versions(ctx, project)
    version_rows = list(
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version__in=versions)
        .values(
            "item_id",
            "item__code",
            "kind",
            "description",
            "unit",
            "quantity",
            "parent_id",
            "version__budget_id",
        )
    )
    parents = {row["item_id"]: row["parent_id"] for row in version_rows}

    def chapters_of(item_id: UUID) -> frozenset[UUID]:
        found: set[UUID] = set()
        parent = parents.get(item_id)
        while parent is not None and parent not in found:
            found.add(parent)
            parent = parents.get(parent)
        return frozenset(found)

    plans = list(
        scoped_qs(ctx, QuantityPlan)
        .filter(project=project)
        .select_related("task")
        .order_by("task__sort_order")
    )
    executed = executed_by_pair(ctx, project_ids=[project.pk])
    tree = load_tree(ctx, project)
    by_item: dict[UUID, list[TaskQuantity]] = defaultdict(list)
    for plan in plans:
        by_item[plan.item_id].append(
            TaskQuantity(
                task=plan.task,
                location=tree.label(plan.task.location_id),
                planned=plan.planned_quantity,
                executed=executed.get((plan.task_id, plan.item_id), _ZERO),
            )
        )

    def build(
        item_id: UUID,
        budget: Budget | None,
        code: str,
        description: str,
        unit: str,
        budgeted: Decimal | None,
    ) -> ItemRow:
        tasks = tuple(by_item.get(item_id, ()))
        planned = sum((t.planned for t in tasks), _ZERO)
        if budgeted is None:
            status = "retired"
        else:
            status = "over" if planned > budgeted else "under" if planned < budgeted else "ok"
        return ItemRow(
            item_id=item_id,
            budget=budget,
            code=code,
            description=description,
            unit=unit,
            budgeted=budgeted,
            planned=planned,
            executed=sum((t.executed for t in tasks), _ZERO),
            status=status,
            chapters=chapters_of(item_id),
            tasks=tasks,
        )

    rows = [
        build(
            row["item_id"],
            budgets[row["version__budget_id"]],
            row["item__code"],
            row["description"],
            row["unit"],
            row["quantity"],
        )
        for row in version_rows
        if row["kind"] == "item"
    ]
    current_ids = {row.item_id for row in rows}
    retired = [i for i in by_item if i not in current_ids]
    for item_id, facts in item_facts(ctx, retired).items():
        rows.append(build(item_id, None, facts.code, facts.description, facts.unit, None))
    rows.sort(key=lambda r: (r.budget.name.lower() if r.budget else "~", code_key(r.code)))
    return rows


def chapter_choices(ctx: RequestContext, project: Project) -> list[tuple[UUID, str]]:
    """Capítulos de las versiones vigentes: (id del ítem-capítulo, "código descripción")."""
    versions = current_versions(ctx, project)
    rows = (
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version__in=versions, kind="chapter")
        .values_list("item_id", "item__code", "description")
    )
    return sorted(
        ((item_id, f"{code} {description}") for item_id, code, description in rows),
        key=lambda choice: code_key(choice[1].split(" ", 1)[0]),
    )


def filter_item_rows(
    rows: Sequence[ItemRow],
    *,
    budget_id: UUID | None = None,
    chapter_id: UUID | None = None,
    chapter_code: str | None = None,
    location_id: UUID | None = None,
    tree: Tree | None = None,
    status: str | None = None,
) -> list[ItemRow]:
    """Filtra. Con ubicación, cada ítem queda con sus tareas de ese lugar y sus sumas."""
    result: list[ItemRow] = []
    for row in rows:
        if budget_id is not None and (row.budget is None or row.budget.pk != budget_id):
            continue
        if chapter_id is not None and chapter_id not in row.chapters:
            continue
        if chapter_code is not None and not row.code.startswith(f"{chapter_code}."):
            continue
        if status is not None and row.status != status:
            continue
        if location_id is not None and tree is not None:
            tasks = tuple(
                t
                for t in row.tasks
                if t.task.location_id is not None and tree.contains(location_id, t.task.location_id)
            )
            if not tasks:
                continue
            row = replace(
                row,
                tasks=tasks,
                planned=sum((t.planned for t in tasks), _ZERO),
                executed=sum((t.executed for t in tasks), _ZERO),
            )
        result.append(row)
    return result


@dataclass(frozen=True)
class NodeRow:
    label: str
    depth: int
    planned_value: Decimal
    executed_value: Decimal

    @property
    def pct(self) -> Decimal | None:
        if self.planned_value <= 0:
            return None
        value = min(HUNDRED, self.executed_value * HUNDRED / self.planned_value)
        return value.quantize(PCT_STEP, rounding=ROUND_HALF_UP)


def location_rows(ctx: RequestContext, project: Project) -> list[NodeRow]:
    """Valor planeado y ejecutado ($) por nodo, consolidado hacia arriba; al final las
    tareas sin ubicación y el total."""
    plans = list(
        scoped_qs(ctx, QuantityPlan)
        .filter(project=project)
        .values_list("task_id", "item_id", "planned_quantity", "task__location_id")
    )
    executed = executed_by_pair(ctx, project_ids=[project.pk])
    prices = {
        item_id: facts.unit_price or _ZERO
        for item_id, facts in item_facts(ctx, {item_id for _, item_id, _, _ in plans}).items()
    }
    tree = load_tree(ctx, project)
    planned: dict[UUID | None, Decimal] = defaultdict(Decimal)
    done: dict[UUID | None, Decimal] = defaultdict(Decimal)
    total_planned = total_done = _ZERO
    for task_id, item_id, quantity, location_id in plans:
        price = prices.get(item_id, _ZERO)
        p = quantity * price
        e = max(executed.get((task_id, item_id), _ZERO), _ZERO) * price
        total_planned += p
        total_done += e
        if location_id is None or location_id not in tree.nodes:
            planned[None] += p
            done[None] += e
            continue
        for part in tree.nodes[location_id].path.strip("/").split("/"):
            node_id = UUID(hex=part)
            planned[node_id] += p
            done[node_id] += e
    roots = [n.pk for n in tree.nodes.values() if n.parent_id is None]
    ordered: list[UUID] = []
    for root in sorted(roots, key=lambda pk: tree.label(pk).lower()):
        ordered.extend(tree.descendants(root))
    rows = [
        NodeRow(
            label=tree.label(node_id),
            depth=tree.nodes[node_id].level.depth,
            planned_value=money(planned[node_id]),
            executed_value=money(done[node_id]),
        )
        for node_id in ordered
        if planned[node_id] > 0 or done[node_id] > 0
    ]
    if planned[None] > 0 or done[None] > 0:
        rows.append(NodeRow("Sin ubicación", 1, money(planned[None]), money(done[None])))
    rows.append(NodeRow("Total", 0, money(total_planned), money(total_done)))
    return rows


__all__ = [
    "SEPARATOR",
    "STATUS_LABELS",
    "ItemRow",
    "NodeRow",
    "TaskQuantity",
    "chapter_choices",
    "filter_item_rows",
    "item_rows",
    "location_rows",
]
