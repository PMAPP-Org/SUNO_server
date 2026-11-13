"""Lo que el control necesita de la BD, en un número fijo de queries (costos, piezas 5 y 6a).

Pieza 5: nueve queries sin importar el tamaño del proyecto (ocho si no hay presupuesto
aplicado): árbol de ubicaciones, tareas vigentes, presupuestos (costo directo contractual),
versiones vigentes, sus filas, códigos de los ítems, plan de la matriz, cantidades validadas
hasta el corte y el último % validado por tarea hasta el corte.

Pieza 6a, solo con `costs=True` (quien lee costos): siete más (seis sin presupuesto
aplicado): cargos de las versiones vigentes (línea de Administración); borradores e indirectos
hasta el corte en un solo agregado; movimientos de capítulo agregados por día, capítulo y tarea;
movimientos de ítem agregados por día, ítem y tarea; los ítems de cada selección; usos de
equipos propios agregados por día e ítem; y viajes de terceros agregados por día. Las
agregaciones llevan `.order_by()` para que el `ordering` del modelo no entre en el `GROUP BY`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from suno.budgets.labels import row_label, several_budgets, with_budget
from suno.budgets.mapping import fold
from suno.core.context import RequestContext
from suno.core.models import (
    Budget,
    BudgetCharge,
    BudgetItem,
    BudgetItemVersion,
    BudgetRowKind,
    BudgetVersion,
    CostEntry,
    CostEntryItem,
    CostScope,
    CostStatus,
    EquipmentOwnership,
    EquipmentUsage,
    EquipmentUsageStatus,
    Project,
    QuantityPlan,
    Record,
    Task,
    Trip,
)
from suno.core.scoping import scoped_qs
from suno.matrix.importer import current_versions
from suno.matrix.locations import load_tree
from suno.projects.field_progress import approved, validated_pct_by_task
from suno.projects.workdays import WorkCalendar

from .actual_cost import (
    CHAPTER,
    EQUIPMENT,
    INDIRECT,
    ITEM,
    LEDGER,
    SELECTION,
    TRANSPORT,
    UNASSIGNED,
    AdminLine,
    ChapterRef,
    CostInput,
    CostLine,
)
from .earned_value import (
    NO_CHAPTER,
    ControlInput,
    Execution,
    PercentTask,
    PlanPair,
    PricedItem,
    TaskWindow,
)

COUNTED_USAGE = (EquipmentUsageStatus.COMPLETED, EquipmentUsageStatus.IN_PROGRESS)
ADMINISTRATION = "administracion"


def load_control_input(
    ctx: RequestContext, project: Project, *, cut: date, kind: str, costs: bool = False
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
    budget_names = {pk: name for pk, name, _ in budgets}
    items = _priced_items(
        version_rows,
        budget_names,
        item_labels,
        several=several_budgets(version.budget_id for version in versions),
    )

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
        contractual_budgets=len(contractual),
        applied_budgets=len({version.budget_id for version in versions}),
        costs=_load_costs(ctx, project, cut, versions, version_rows, item_labels, budget_names)
        if costs
        else None,
    )


def _priced_items(
    rows: Sequence[tuple[Any, ...]],
    budget_names: dict[UUID, str],
    labels: dict[UUID, str],
    *,
    several: bool,
) -> dict[UUID, PricedItem]:
    """Los ítems con precio vigente. Completa `labels` con "código descripción" (y el
    presupuesto, si hay dos o más con versión aplicada: `budgets.labels`) para todo lo de las
    versiones vigentes."""
    parents: dict[UUID, UUID | None] = {}
    names: dict[UUID, str] = {}
    for item_id, code, _kind, description, _unit, _price, parent_id, budget_id in rows:
        names[item_id] = row_label(code, description, budget_names[budget_id], several=several)
        parents[item_id] = parent_id
    labels.update(names)
    items: dict[UUID, PricedItem] = {}
    for item_id, _code, kind, _description, unit, price, _parent, budget_id in rows:
        if kind == BudgetRowKind.ITEM and price is not None:
            chapter = _top_chapter(item_id, parents, names)
            if chapter == NO_CHAPTER:
                chapter = with_budget(NO_CHAPTER, budget_names[budget_id], several=several)
            items[item_id] = PricedItem(
                item_id=item_id,
                label=names[item_id],
                unit=unit,
                unit_price=price,
                chapter=chapter,
                budget_id=budget_id,
            )
    return items


def _top_chapter(item_id: UUID, parents: dict[UUID, UUID | None], names: dict[UUID, str]) -> str:
    """El capítulo de primer nivel que contiene al ítem, o `NO_CHAPTER`."""
    top, seen = item_id, {item_id}
    while (parent := parents.get(top)) is not None and parent not in seen:
        seen.add(parent)
        top = parent
    return NO_CHAPTER if top == item_id else names.get(top, NO_CHAPTER)


def _load_costs(
    ctx: RequestContext,
    project: Project,
    cut: date,
    versions: Sequence[BudgetVersion],
    rows: Sequence[tuple[Any, ...]],
    labels: dict[UUID, str],
    budget_names: dict[UUID, str],
) -> CostInput:
    """Movimientos aprobados, borradores y costos automáticos hasta el corte.

    El libro se lee agregado: borradores e indirectos en un total (no van a periodos, ítems ni
    tareas), capítulos e ítems por día, capítulo o ítem y tarea; solo las selecciones van por
    movimiento, porque necesitan sus ítems."""
    lines: list[CostLine] = []
    entries = scoped_qs(ctx, CostEntry).filter(project=project, incurred_on__lte=cut)
    approved = entries.filter(status=CostStatus.APPROVED)
    totals = entries.aggregate(
        pending_count=Count("id", filter=Q(status=CostStatus.DRAFT)),
        pending_amount=Sum("amount", filter=Q(status=CostStatus.DRAFT)),
        indirect=Sum("amount", filter=Q(status=CostStatus.APPROVED, scope=CostScope.INDIRECT)),
    )
    if totals["indirect"]:
        lines.append(CostLine(cut, totals["indirect"], INDIRECT, origin=LEDGER))
    for day, chapter_id, task_id, total in (
        approved.filter(scope=CostScope.CHAPTER)
        .order_by()
        .values("incurred_on", "chapter_id", "task_id")
        .annotate(total=Sum("amount"))
        .values_list("incurred_on", "chapter_id", "task_id", "total")
    ):
        lines.append(CostLine(day, total, CHAPTER, (), chapter_id, task_id, LEDGER))
    links = scoped_qs(ctx, CostEntryItem).filter(
        entry__project=project,
        entry__status=CostStatus.APPROVED,
        entry__incurred_on__lte=cut,
    )
    for day, item_id, chapter_id, task_id, total in (
        links.filter(entry__scope=CostScope.ITEM)
        .order_by()
        .values("entry__incurred_on", "item_id", "entry__chapter_id", "entry__task_id")
        .annotate(total=Sum("entry__amount"))
        .values_list(
            "entry__incurred_on", "item_id", "entry__chapter_id", "entry__task_id", "total"
        )
    ):
        lines.append(CostLine(day, total, ITEM, (item_id,), chapter_id, task_id, LEDGER))
    selections: dict[UUID, tuple[date, Decimal, UUID | None, UUID | None]] = {}
    members: dict[UUID, list[UUID]] = defaultdict(list)
    for entry_id, day, amount, chapter_id, task_id, item_id in (
        links.filter(entry__scope=CostScope.SELECTION)
        .order_by()
        .values_list(
            "entry_id",
            "entry__incurred_on",
            "entry__amount",
            "entry__chapter_id",
            "entry__task_id",
            "item_id",
        )
    ):
        selections[entry_id] = (day, amount, chapter_id, task_id)
        members[entry_id].append(item_id)
    for entry_id, (day, amount, chapter_id, task_id) in selections.items():
        items = tuple(sorted(members[entry_id]))
        lines.append(CostLine(day, amount, SELECTION, items, chapter_id, task_id, LEDGER))
    pending_count = totals["pending_count"] or 0
    pending_amount = totals["pending_amount"] or Decimal(0)

    unpriced_usages = 0
    for day, item_id, total, unpriced in (
        scoped_qs(ctx, EquipmentUsage)
        .filter(
            project=project,
            usage_date__lte=cut,
            status__in=COUNTED_USAGE,
            equipment__ownership=EquipmentOwnership.OWN,
        )
        .order_by()
        .values("usage_date", "budget_item_id")
        .annotate(total=Sum("cost"), unpriced=Count("id", filter=Q(cost__isnull=True)))
        .values_list("usage_date", "budget_item_id", "total", "unpriced")
    ):
        unpriced_usages += unpriced
        if total:
            if item_id is not None:
                lines.append(CostLine(day, total, ITEM, (item_id,), origin=EQUIPMENT))
            else:
                lines.append(CostLine(day, total, UNASSIGNED, origin=EQUIPMENT))

    unpriced_trips = 0
    for day, total, unpriced in (
        scoped_qs(ctx, Trip)
        .filter(project=project, vehicle__isnull=False, recorded_at__date__lte=cut)
        .annotate(day=TruncDate("recorded_at"))
        .order_by()
        .values("day")
        .annotate(total=Sum("amount"), unpriced=Count("id", filter=Q(amount__isnull=True)))
        .values_list("day", "total", "unpriced")
    ):
        unpriced_trips += unpriced
        if total:
            lines.append(CostLine(day, total, UNASSIGNED, origin=TRANSPORT))

    return CostInput(
        lines=lines,
        chapters=_chapters(rows, labels),
        admin=_admin_lines(ctx, versions, budget_names) if versions else [],
        pending_count=pending_count,
        pending_amount=pending_amount,
        unpriced_usages=unpriced_usages,
        unpriced_trips=unpriced_trips,
    )


def _chapters(rows: Sequence[tuple[Any, ...]], labels: dict[UUID, str]) -> dict[UUID, ChapterRef]:
    """Cada capítulo de las versiones vigentes: su capítulo de primer nivel, su profundidad y
    sus ítems con precio a cualquier profundidad. `labels` ya trae las etiquetas de
    `_priced_items` (con el presupuesto si hay varios)."""
    parents: dict[UUID, UUID | None] = {}
    children: dict[UUID, list[UUID]] = defaultdict(list)
    chapters: list[UUID] = []
    priced: set[UUID] = set()
    for item_id, _code, kind, _description, _unit, price, parent_id, _budget in rows:
        parents[item_id] = parent_id
        if parent_id is not None:
            children[parent_id].append(item_id)
        if kind == BudgetRowKind.CHAPTER:
            chapters.append(item_id)
        elif price is not None:
            priced.add(item_id)
    refs: dict[UUID, ChapterRef] = {}
    for chapter_id in chapters:
        top, depth, seen = chapter_id, 0, {chapter_id}
        while (parent := parents.get(top)) is not None and parent not in seen:
            seen.add(parent)
            top = parent
            depth += 1
        inside: set[UUID] = set()
        pending = list(children.get(chapter_id, ()))
        while pending:
            node = pending.pop()
            if node not in inside:
                inside.add(node)
                pending.extend(children.get(node, ()))
        refs[chapter_id] = ChapterRef(
            label=labels.get(chapter_id, NO_CHAPTER),
            top=labels.get(top, NO_CHAPTER),
            depth=depth,
            items=frozenset(inside & priced),
        )
    return refs


def _admin_lines(
    ctx: RequestContext, versions: Sequence[BudgetVersion], budget_names: dict[UUID, str]
) -> list[AdminLine]:
    """La primera línea de cada versión vigente cuyo nombre (sin mayúsculas ni tildes) empieza
    por "administracion", como razón de su monto sobre el costo directo."""
    amounts: dict[UUID, Decimal] = {}
    for version_id, name, amount in (
        scoped_qs(ctx, BudgetCharge)
        .filter(version__in=versions)
        .order_by("order")
        .values_list("version_id", "name", "amount")
    ):
        if (
            version_id not in amounts
            and amount is not None
            and fold(name).strip().startswith(ADMINISTRATION)
        ):
            amounts[version_id] = amount
    lines: list[AdminLine] = []
    for version in versions:
        amount = amounts.get(version.pk)
        direct = version.direct_cost
        ratio = amount / direct if amount is not None and direct else None
        lines.append(AdminLine(version.budget_id, budget_names.get(version.budget_id, ""), ratio))
    return lines
