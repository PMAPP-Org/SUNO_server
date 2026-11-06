"""Carga de la matriz desde la plantilla Excel, en dos pasos.

1. `stage_plan`: lee el archivo, calcula las diferencias contra la matriz actual
   y guarda una `QuantityPlanImport` pendiente (vista previa). Descarta la
   pendiente anterior del proyecto.
2. `apply_plan`: con el proyecto bloqueado, vuelve a leer el archivo guardado y
   reemplaza la matriz. Un par ausente se borra, salvo que tenga ejecución
   (validada o pendiente de revisión): ese queda con planeado 0. Los pares de las
   tareas retiradas no se tocan (la plantilla las omite). `discard_plan` la descarta.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import DatabaseError, transaction
from django.db.models import Count, Max
from django.utils import timezone

from visitacampo.budgets.excel import ExcelRejected
from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import RequestContext
from visitacampo.core.models import (
    Budget,
    BudgetItem,
    BudgetItemVersion,
    BudgetVersion,
    BudgetVersionStatus,
    LocationLevel,
    Project,
    QuantityPlan,
    QuantityPlanImport,
    QuantityPlanImportStatus,
    ScheduleImport,
    ScheduleImportStatus,
    Task,
)
from visitacampo.core.scoping import scoped_qs

from .diff import BudgetedItem, code_key, item_totals, summarize_plan
from .locations import Tree, from_project, load_tree, node_key, project_levels
from .quantities import executed_by_pair, item_facts
from .template import (
    ItemRef,
    ParsedPlan,
    TaskRef,
    TemplateRejected,
    TemplateRow,
    build_template,
    check_file_name,
    exact_key,
    parse_plan,
    read_template,
)

BUSY = "Otra importación de este proyecto está en curso; intenta de nuevo en unos segundos."
STALE = (
    "Cambió el cronograma, un presupuesto o la matriz después de subir este archivo. "
    "Vuelve a subirlo para ver los cambios actualizados."
)

Pair = tuple[UUID, UUID]


class PlanRejected(Exception):
    """No se puede subir o aplicar; `errors` son mensajes para el usuario."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(errors[0])


@dataclass
class _Context:
    levels: list[LocationLevel]
    editable: bool
    tree: Tree
    tasks: dict[int, Task]
    budgets: list[Budget]
    #: Ítems de las versiones vigentes: id → (presupuesto, datos).
    budgeted: dict[UUID, tuple[Budget, BudgetedItem]]
    item_refs: dict[tuple[str, str], ItemRef]
    #: Ítems con plan que ya no están en su versión vigente: id → nombre de su presupuesto.
    retired_budgets: dict[UUID, str]
    #: El plan de las tareas vigentes (las retiradas no se tocan).
    current: dict[Pair, QuantityPlan]


def current_versions(ctx: RequestContext, project: Project) -> list[BudgetVersion]:
    return list(
        scoped_qs(ctx, BudgetVersion)
        .filter(budget__project=project, status=BudgetVersionStatus.APPLIED)
        .defer("source_file")
        .order_by("budget_id", "-number")
        .distinct("budget_id")
    )


def _load(ctx: RequestContext, project: Project) -> _Context:
    levels = project_levels(ctx, project)
    budgets = {b.pk: b for b in scoped_qs(ctx, Budget).filter(project=project).order_by("name")}
    versions = current_versions(ctx, project)
    budgeted: dict[UUID, tuple[Budget, BudgetedItem]] = {}
    refs: dict[tuple[str, str], ItemRef] = {}
    for row in (
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version__in=versions)
        .values(
            "item_id", "item__code", "kind", "description", "unit", "quantity", "version__budget_id"
        )
    ):
        budget = budgets[row["version__budget_id"]]
        refs[(exact_key(budget.name), row["item__code"])] = ItemRef(row["item_id"], row["kind"])
        if row["kind"] == "item":
            budgeted[row["item_id"]] = (
                budget,
                BudgetedItem(row["item__code"], row["description"], row["unit"], row["quantity"]),
            )
    # Sin las filas de las tareas retiradas: la plantilla las omite (no se pueden editar),
    # así que no estar en el archivo no significa quitarlas. Quedan como están.
    current = {
        (p.task_id, p.item_id): p
        for p in scoped_qs(ctx, QuantityPlan).filter(project=project, task__retired_in__isnull=True)
    }
    retired_budgets: dict[UUID, str] = {}
    retired_ids = {item_id for _, item_id in current if item_id not in budgeted}
    if retired_ids:
        for item_id, code, budget_id in (
            scoped_qs(ctx, BudgetItem)
            .filter(pk__in=retired_ids, budget__in=budgets)
            .values_list("pk", "code", "budget_id")
        ):
            retired_budgets[item_id] = budgets[budget_id].name
            refs.setdefault((exact_key(budgets[budget_id].name), code), ItemRef(item_id, "retired"))
    return _Context(
        levels=levels,
        editable=bool(levels) and not from_project(levels),
        tree=load_tree(ctx, project),
        tasks={t.uid: t for t in scoped_qs(ctx, Task).filter(project=project)},
        budgets=list(budgets.values()),
        budgeted=budgeted,
        item_refs=refs,
        retired_budgets=retired_budgets,
        current=current,
    )


def _parse(data: bytes, context: _Context) -> ParsedPlan:
    task_refs = {
        uid: TaskRef(
            id=t.pk,
            label=f"{t.outline_number} {t.name}",
            is_summary=t.is_summary,
            retired=t.retired_in_id is not None,
        )
        for uid, t in context.tasks.items()
    }
    try:
        return parse_plan(
            read_template(data),
            level_names=[level.name for level in context.levels],
            editable_locations=context.editable,
            tasks=task_refs,
            items=context.item_refs,
            budget_names=[b.name for b in context.budgets],
            current_pairs=set(context.current),
        )
    except TemplateRejected as exc:
        raise PlanRejected(exc.errors) from exc


def _base(ctx: RequestContext, project: Project) -> dict[str, Any]:
    """Lo vigente al preparar la vista previa; si cambia, hay que volver a subir.

    Incluye las ediciones desde la web: cuántas filas tiene el plan y la última que
    cambió (una alta o un cambio mueve la fecha; una baja, la cuenta), y una huella de
    la ubicación de las tareas."""
    schedule = scoped_qs(ctx, ScheduleImport).filter(
        project=project, status=ScheduleImportStatus.APPLIED
    )
    matrix = scoped_qs(ctx, QuantityPlanImport).filter(
        project=project, status=QuantityPlanImportStatus.APPLIED
    )
    plans = (
        scoped_qs(ctx, QuantityPlan)
        .filter(project=project)
        .aggregate(n=Count("pk"), last=Max("updated_at"))
    )
    locations = hashlib.sha256()
    for task_id, location_id in (
        scoped_qs(ctx, Task)
        .filter(project=project, location__isnull=False)
        .order_by("pk")
        .values_list("pk", "location_id")
    ):
        locations.update(f"{task_id}:{location_id};".encode())
    return {
        "schedule": schedule.aggregate(n=Max("number"))["n"],
        "matrix": matrix.aggregate(n=Max("number"))["n"],
        "budgets": {str(v.budget_id): v.number for v in current_versions(ctx, project)},
        "plan_rows": plans["n"],
        "plan_updated": plans["last"].isoformat() if plans["last"] else None,
        "locations": locations.hexdigest(),
    }


def _summary(
    ctx: RequestContext,
    project: Project,
    parsed: ParsedPlan,
    context: _Context,
    base: dict[str, Any],
) -> dict[str, Any]:
    executed = set(executed_by_pair(ctx, project_ids=[project.pk], include_pending=True))
    by_id = {t.pk: t for t in context.tasks.values()}
    codes = {item_id: info.code for item_id, (_, info) in context.budgeted.items()}
    missing_codes = {i for _, i in [*parsed.pairs, *context.current] if i not in codes}
    for item_id, facts in item_facts(ctx, missing_codes).items():
        codes[item_id] = facts.code

    def label(pair: Pair) -> str:
        task = by_id[pair[0]]
        return f"{task.outline_number} {task.name} × {codes.get(pair[1], '?')}"

    current = {pair: p.planned_quantity for pair, p in context.current.items()}
    summary = summarize_plan(current, parsed.pairs, executed=executed, label=label)

    planned: dict[UUID, Decimal] = defaultdict(Decimal)
    for (_, item_id), value in parsed.pairs.items():
        planned[item_id] += value
    retired = {
        item_id: BudgetedItem(f.code, f.description, f.unit, None)
        for item_id, f in item_facts(ctx, [i for i in planned if i not in context.budgeted]).items()
    }
    totals = item_totals(planned, {i: info for i, (_, info) in context.budgeted.items()}, retired)

    location_changes: list[str] = []
    new_paths: set[tuple[str, ...]] = set()
    if context.editable:
        for task_id, names in parsed.locations.items():
            location_id = by_id[task_id].location_id
            before = context.tree.label(location_id)
            after = " / ".join(names)
            before_keys = (
                [node_key(n) for n in context.tree.names(location_id)]
                if location_id in context.tree.nodes
                else []
            )
            if before_keys != [node_key(n) for n in names]:
                task = by_id[task_id]
                location_changes.append(
                    f"{task.outline_number} {task.name}: {before or 'sin ubicación'} → "
                    f"{after or 'sin ubicación'}"
                )
            for depth in range(1, len(names) + 1):
                prefix = tuple(node_key(n) for n in names[:depth])
                if context.tree.find(names[:depth]) is None:
                    new_paths.add(prefix)
    return {
        **summary,
        "item_counts": totals["counts"],
        "items": totals["items"],
        "location_changes": len(location_changes),
        "location_examples": location_changes[:20],
        "new_nodes": len(new_paths),
        "warnings": list(parsed.warnings),
        "pairs": len(parsed.pairs),
        "base": base,
    }


def stage_plan(
    ctx: RequestContext, project: Project, *, file_name: str, data: bytes
) -> QuantityPlanImport:
    try:
        check_file_name(file_name)
    except ExcelRejected as exc:
        raise PlanRejected([str(exc)]) from exc
    base = _base(ctx, project)
    context = _load(ctx, project)
    parsed = _parse(data, context)
    summary = _summary(ctx, project, parsed, context, base)
    with transaction.atomic():
        scoped_qs(ctx, QuantityPlanImport).filter(
            project=project, status=QuantityPlanImportStatus.PENDING
        ).update(status=QuantityPlanImportStatus.DISCARDED)
        return scoped_qs(ctx, QuantityPlanImport).create(
            org_id=ctx.org_id,
            project=project,
            file_name=file_name[:255],
            source_file=data,
            uploaded_by_id=ctx.user_id,
            summary=summary,
        )


def _locked_pending(ctx: RequestContext, import_id: UUID) -> tuple[QuantityPlanImport, Project]:
    imp = scoped_qs(ctx, QuantityPlanImport).defer("source_file").filter(pk=import_id).first()
    if imp is None:
        raise PlanRejected(["La importación no existe."])
    try:
        project = scoped_qs(ctx, Project).select_for_update(nowait=True).get(pk=imp.project_id)
    except DatabaseError as exc:
        raise PlanRejected([BUSY]) from exc
    imp = scoped_qs(ctx, QuantityPlanImport).select_for_update().get(pk=import_id)
    if imp.status != QuantityPlanImportStatus.PENDING:
        raise PlanRejected(["Esta importación ya fue aplicada o descartada."])
    return imp, project


def apply_plan(ctx: RequestContext, import_id: UUID) -> QuantityPlanImport:
    with transaction.atomic():
        imp, project = _locked_pending(ctx, import_id)
        if imp.summary.get("base") != _base(ctx, project):
            raise PlanRejected([STALE])
        context = _load(ctx, project)
        parsed = _parse(bytes(imp.source_file), context)
        # Ejecución validada o pendiente de revisión: una visita pendiente puede validarse.
        executed = set(executed_by_pair(ctx, project_ids=[project.pk], include_pending=True))
        now = timezone.now()
        plans = scoped_qs(ctx, QuantityPlan)

        new: list[QuantityPlan] = []
        changed: list[QuantityPlan] = []
        for (task_id, item_id), value in parsed.pairs.items():
            current = context.current.get((task_id, item_id))
            if current is None:
                new.append(
                    QuantityPlan(
                        org_id=ctx.org_id,
                        project=project,
                        task_id=task_id,
                        item_id=item_id,
                        planned_quantity=value,
                        updated_by_id=ctx.user_id,
                        updated_at=now,
                    )
                )
            elif current.planned_quantity != value:
                current.planned_quantity, current.updated_by_id, current.updated_at = (
                    value,
                    ctx.user_id,
                    now,
                )
                changed.append(current)
        delete_ids: list[UUID] = []
        for pair, current in context.current.items():
            if pair in parsed.pairs:
                continue
            if pair in executed:
                if current.planned_quantity != 0:
                    current.planned_quantity = Decimal(0)
                    current.updated_by_id, current.updated_at = ctx.user_id, now
                    changed.append(current)
            else:
                delete_ids.append(current.pk)
        plans.filter(pk__in=delete_ids).delete()
        plans.bulk_create(new, batch_size=500)
        plans.bulk_update(changed, ["planned_quantity", "updated_by", "updated_at"], batch_size=500)

        if context.editable:
            by_id = {t.pk: t for t in context.tasks.values()}
            moved: list[Task] = []
            for task_id, names in parsed.locations.items():
                node_id = context.tree.ensure(ctx, context.levels, names)[0].pk if names else None
                task = by_id[task_id]
                if task.location_id != node_id:
                    task.location_id = node_id
                    moved.append(task)
            scoped_qs(ctx, Task).bulk_update(moved, ["location"], batch_size=500)

        applied = scoped_qs(ctx, QuantityPlanImport).filter(
            project=project, status=QuantityPlanImportStatus.APPLIED
        )
        imp.number = (applied.aggregate(n=Max("number"))["n"] or 0) + 1
        imp.status = QuantityPlanImportStatus.APPLIED
        imp.applied_at = now
        imp.save(update_fields=["number", "status", "applied_at"])
        audit.record(
            ctx,
            action=AuditAction.MATRIX_IMPORT_APPLIED,
            target=f"project:{project.pk}",
            metadata={
                "import": str(imp.pk),
                "number": imp.number,
                "added": len(new),
                "changed": len(changed),
                "removed": len(delete_ids),
            },
        )
        return imp


def discard_plan(ctx: RequestContext, import_id: UUID) -> QuantityPlanImport:
    with transaction.atomic():
        imp, project = _locked_pending(ctx, import_id)
        imp.status = QuantityPlanImportStatus.DISCARDED
        imp.save(update_fields=["status"])
        audit.record(
            ctx,
            action=AuditAction.MATRIX_IMPORT_DISCARDED,
            target=f"project:{project.pk}",
            metadata={"import": str(imp.pk)},
        )
        return imp


def template_bytes(ctx: RequestContext, project: Project) -> bytes:
    """La plantilla con la matriz y la ubicación actuales: una fila por par, y una por
    tarea hoja vigente sin plan."""
    context = _load(ctx, project)
    facts = item_facts(ctx, {item_id for _, item_id in context.current})
    budget_names = {item_id: budget.name for item_id, (budget, _) in context.budgeted.items()}
    budget_names.update(context.retired_budgets)
    by_task: dict[UUID, list[QuantityPlan]] = defaultdict(list)
    for (task_id, _), plan in context.current.items():
        by_task[task_id].append(plan)
    width = len(context.levels)
    rows: list[TemplateRow] = []
    leaves = sorted(
        (t for t in context.tasks.values() if not t.is_summary and t.retired_in_id is None),
        key=lambda t: t.sort_order,
    )
    for task in leaves:
        names = context.tree.names(task.location_id) if task.location_id else []
        location = tuple([*names, *[""] * width][:width])
        own = sorted(by_task.get(task.pk, []), key=lambda p: code_key(facts[p.item_id].code))
        if not own:
            rows.append(TemplateRow(task.uid, task.wbs, task.name, location, "", "", "", "", None))
        for plan in own:
            info = facts[plan.item_id]
            rows.append(
                TemplateRow(
                    task.uid,
                    task.wbs,
                    task.name,
                    location,
                    budget_names.get(plan.item_id, ""),
                    info.code,
                    info.description,
                    info.unit,
                    plan.planned_quantity,
                )
            )
    return build_template([level.name for level in context.levels], rows)
