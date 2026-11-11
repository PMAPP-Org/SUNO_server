"""Lo que el control necesita de la BD, en un número fijo de queries (costos, pieza 5).

Nueve queries sin importar el tamaño del proyecto (ocho si no hay presupuesto aplicado):
árbol de ubicaciones, tareas vigentes, presupuestos (costo directo contractual), versiones
vigentes, sus filas, códigos de los ítems, plan de la matriz, cantidades validadas hasta el
corte y el último % validado por tarea hasta el corte.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from suno.core.context import RequestContext
from suno.core.models import (
    Budget,
    BudgetItem,
    BudgetItemVersion,
    BudgetRowKind,
    Project,
    QuantityPlan,
    Record,
    Task,
)
from suno.core.scoping import scoped_qs
from suno.matrix.importer import current_versions
from suno.matrix.locations import load_tree
from suno.projects.field_progress import approved, validated_pct_by_task
from suno.projects.workdays import WorkCalendar

from .earned_value import (
    NO_CHAPTER,
    ControlInput,
    Execution,
    PercentTask,
    PlanPair,
    PricedItem,
    TaskWindow,
)


def load_control_input(
    ctx: RequestContext, project: Project, *, cut: date, kind: str
) -> ControlInput:
    tree = load_tree(ctx, project)
    tasks: dict[UUID, TaskWindow] = {}
    leaves: list[tuple[UUID, str]] = []
    for (
        pk,
        outline,
        name,
        summary,
        milestone,
        baseline_start,
        baseline_finish,
        start,
        finish,
        location_id,
        order,
    ) in (
        scoped_qs(ctx, Task)
        .filter(project=project, retired_in__isnull=True)
        .order_by("sort_order")
        .values_list(
            "pk",
            "outline_number",
            "name",
            "is_summary",
            "is_milestone",
            "baseline_start",
            "baseline_finish",
            "start",
            "finish",
            "location_id",
            "sort_order",
        )
    ):
        label = f"{outline} {name}"
        from_baseline = baseline_start is not None and baseline_finish is not None
        tasks[pk] = TaskWindow(
            task_id=pk,
            label=label,
            location=tree.label(location_id),
            start=baseline_start if from_baseline else start,
            finish=baseline_finish if from_baseline else finish,
            from_baseline=from_baseline,
            milestone=milestone,
            order=order,
        )
        if not summary and not milestone:
            leaves.append((pk, label))

    budgets = list(
        scoped_qs(ctx, Budget)
        .filter(project=project)
        .values_list("pk", "name", "contractual_version__direct_cost")
    )
    versions = current_versions(ctx, project)
    version_rows = (
        list(
            scoped_qs(ctx, BudgetItemVersion)
            .filter(version__in=versions)
            .values_list(
                "item_id",
                "item__code",
                "kind",
                "description",
                "unit",
                "unit_price",
                "parent_id",
                "version__budget_id",
            )
        )
        if versions
        else []
    )
    item_labels: dict[UUID, str] = dict(
        scoped_qs(ctx, BudgetItem).filter(budget__project=project).values_list("pk", "code")
    )
    items = _priced_items(version_rows, {pk: name for pk, name, _ in budgets}, item_labels)

    plans = [
        PlanPair(task_id, item_id, planned)
        for task_id, item_id, planned in scoped_qs(ctx, QuantityPlan)
        .filter(project=project)
        .values_list("task_id", "item_id", "planned_quantity")
    ]
    records = approved(
        scoped_qs(ctx, Record)
        .filter(task__project=project, deleted_at__isnull=True, captured_at__date__lte=cut)
        .exclude(quantities=[])
    ).values_list("task_id", "captured_at", "quantities")
    executions = [
        Execution(
            task_id=task_id,
            item_id=UUID(row["item_id"]),
            day=timezone.localtime(captured_at).date(),
            quantity=Decimal(row["quantity"]),
        )
        for task_id, captured_at, rows in records
        for row in rows
    ]

    planned_tasks = {p.task_id for p in plans}
    validated = validated_pct_by_task(
        scoped_qs(ctx, Record).filter(captured_at__date__lte=cut), project_ids=[project.pk]
    )
    contractual = [direct for _, _, direct in budgets if direct is not None]
    return ControlInput(
        cut=cut,
        kind=kind,
        calendar=WorkCalendar.from_json(project.calendar),
        tasks=tasks,
        items=items,
        item_labels=item_labels,
        plans=plans,
        executions=executions,
        percent_tasks=[
            PercentTask(label, validated.get(pk)) for pk, label in leaves if pk not in planned_tasks
        ],
        has_budget=bool(versions),
        contractual_direct_cost=sum(contractual, Decimal(0)) if contractual else None,
    )


def _priced_items(
    rows: Sequence[tuple[Any, ...]], budget_names: dict[UUID, str], labels: dict[UUID, str]
) -> dict[UUID, PricedItem]:
    """Los ítems con precio vigente. Completa `labels` con "código descripción" (y el
    presupuesto, si el proyecto tiene más de uno) para todo lo de las versiones vigentes."""
    several = len(budget_names) > 1
    parents: dict[UUID, UUID | None] = {}
    names: dict[UUID, str] = {}
    for item_id, code, _kind, description, _unit, _price, parent_id, budget_id in rows:
        text = f"{code} {description}".strip()
        names[item_id] = f"{text} · {budget_names[budget_id]}" if several else text
        parents[item_id] = parent_id
    labels.update(names)
    items: dict[UUID, PricedItem] = {}
    for item_id, _code, kind, _description, unit, price, _parent, _budget in rows:
        if kind == BudgetRowKind.ITEM and price is not None:
            items[item_id] = PricedItem(
                item_id=item_id,
                label=names[item_id],
                unit=unit,
                unit_price=price,
                chapter=_top_chapter(item_id, parents, names),
            )
    return items


def _top_chapter(item_id: UUID, parents: dict[UUID, UUID | None], names: dict[UUID, str]) -> str:
    """El capítulo de primer nivel que contiene al ítem, o `NO_CHAPTER`."""
    top, seen = item_id, {item_id}
    while (parent := parents.get(top)) is not None and parent not in seen:
        seen.add(parent)
        top = parent
    return NO_CHAPTER if top == item_id else names.get(top, NO_CHAPTER)
