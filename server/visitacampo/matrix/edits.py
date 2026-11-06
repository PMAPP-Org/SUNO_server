"""Ediciones de la matriz desde la web, fila a fila, y la ubicación de una tarea.

Cada cambio va con su registro de auditoría en la misma transacción."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from visitacampo.budgets.excel import MAX_QUANTITY
from visitacampo.budgets.importer import current_version
from visitacampo.budgets.totals import parse_number_detail
from visitacampo.budgets.totals import quantity as quantity_of
from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import RequestContext
from visitacampo.core.models import Budget, BudgetItemVersion, Project, QuantityPlan, Task
from visitacampo.core.scoping import scoped_qs

from .locations import LevelError, from_project, load_tree, path_values, project_levels
from .quantities import executed_by_pair


class EditRejected(Exception):
    """El cambio no se puede hacer; el mensaje es para el usuario."""


def _check_leaf(task: Task) -> None:
    if task.is_summary:
        raise EditRejected("La matriz va en tareas de detalle, no en resúmenes.")
    if task.retired_in_id is not None:
        raise EditRejected("La tarea está retirada del cronograma.")


def set_plan(
    ctx: RequestContext, task: Task, *, budget_id: UUID, code: str, quantity: str
) -> tuple[QuantityPlan, bool]:
    """Crea o cambia la cantidad planeada de un ítem en la tarea. El segundo valor
    dice si la cantidad se leyó con la regla "punto + 3 dígitos" ("1.234" = 1234),
    para avisarle al usuario."""
    _check_leaf(task)
    budget = scoped_qs(ctx, Budget).filter(pk=budget_id, project_id=task.project_id).first()
    version = current_version(ctx, budget) if budget is not None else None
    row = (
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version=version, item__code=code.strip())
        .values("item_id", "kind")
        .first()
        if version is not None
        else None
    )
    if row is None:
        raise EditRejected(f"El ítem {code} no existe en el presupuesto vigente.")
    if row["kind"] != "item":
        raise EditRejected(f"{code} es un capítulo; la matriz va en ítems.")
    try:
        parsed, guessed = parse_number_detail(quantity)
    except ValueError:
        parsed, guessed = None, False
    if parsed is None:
        raise EditRejected("La cantidad no es un número.")
    if parsed < 0:
        raise EditRejected("La cantidad no puede ser negativa.")
    # Después de redondear: 99999999999999,99999 sube a 1e14 y no cabe en la columna.
    value = quantity_of(parsed)
    if value >= MAX_QUANTITY:
        raise EditRejected("La cantidad es demasiado grande.")
    with transaction.atomic():
        # El mismo bloqueo que la importación de la matriz: evita carreras con ella y
        # entre dos altas del mismo par.
        scoped_qs(ctx, Project).select_for_update().get(pk=task.project_id)
        plan = (
            scoped_qs(ctx, QuantityPlan)
            .select_for_update()
            .filter(task=task, item_id=row["item_id"])
            .first()
        )
        before = None if plan is None else str(plan.planned_quantity)
        if plan is None:
            plan = scoped_qs(ctx, QuantityPlan).create(
                org_id=ctx.org_id,
                project_id=task.project_id,
                task=task,
                item_id=row["item_id"],
                planned_quantity=value,
                updated_by_id=ctx.user_id,
                updated_at=timezone.now(),
            )
        else:
            plan.planned_quantity, plan.updated_by_id, plan.updated_at = (
                value,
                ctx.user_id,
                timezone.now(),
            )
            plan.save(update_fields=["planned_quantity", "updated_by", "updated_at"])
        audit.record(
            ctx,
            action=AuditAction.MATRIX_PLAN_CHANGED,
            target=f"task:{task.pk}",
            metadata={"item": str(row["item_id"]), "before": before, "after": str(value)},
        )
        return plan, guessed


def remove_plan(ctx: RequestContext, task: Task, item_id: UUID) -> bool:
    """Quita el ítem del plan de la tarea. Si ya tiene ejecución (validada o pendiente
    de revisión), lo deja en 0 y devuelve False (la ejecución no se pierde de vista)."""
    with transaction.atomic():
        scoped_qs(ctx, Project).select_for_update().get(pk=task.project_id)
        plan = (
            scoped_qs(ctx, QuantityPlan)
            .select_for_update()
            .filter(task=task, item_id=item_id)
            .first()
        )
        if plan is None:
            return True
        before = str(plan.planned_quantity)
        # Con visitas validadas o pendientes de revisión: una pendiente puede validarse.
        executed = (task.pk, item_id) in executed_by_pair(
            ctx, task_ids=[task.pk], include_pending=True
        )
        if executed:
            plan.planned_quantity, plan.updated_by_id, plan.updated_at = (
                quantity_of(Decimal(0)),
                ctx.user_id,
                timezone.now(),
            )
            plan.save(update_fields=["planned_quantity", "updated_by", "updated_at"])
        else:
            plan.delete()
        audit.record(
            ctx,
            action=AuditAction.MATRIX_PLAN_CHANGED,
            target=f"task:{task.pk}",
            metadata={
                "item": str(item_id),
                "before": before,
                "after": "0.0000" if executed else None,
            },
        )
        return not executed


def set_task_location(ctx: RequestContext, task: Task, path_text: str) -> None:
    """Ubica la tarea en la ruta "Tramo 2 / PR 12+300" (creando los nodos que falten);
    vacío la deja sin ubicación. Solo si la ubicación se asigna en SUNO."""
    _check_leaf(task)
    parts = path_text.split("/")
    with transaction.atomic():
        # El mismo bloqueo que la importación: evita carreras al crear nodos. Los
        # niveles y la ubicación actual se leen ya con el bloqueo, para no usar datos viejos.
        project = scoped_qs(ctx, Project).select_for_update().get(pk=task.project_id)
        levels = project_levels(ctx, project)
        if not levels:
            raise EditRejected("El proyecto no tiene niveles de ubicación.")
        if from_project(levels):
            raise EditRejected("La ubicación de este proyecto se lee de MS Project.")
        if path_text.strip() and len(parts) > len(levels):
            raise EditRejected(
                f"La ruta tiene {len(parts)} niveles y el proyecto tiene {len(levels)}."
            )
        path = path_values(parts if path_text.strip() else [])
        if path.incomplete:
            raise EditRejected("La ruta tiene un nivel vacío.")
        task.refresh_from_db(fields=["location"])
        before = task.location_id
        try:
            node_id = (
                load_tree(ctx, project).ensure(ctx, levels, path.names)[0].pk
                if path.names
                else None
            )
        except LevelError as exc:
            raise EditRejected(str(exc)) from exc
        if node_id == before:
            return
        task.location_id = node_id
        task.save(update_fields=["location"])
        audit.record(
            ctx,
            action=AuditAction.MATRIX_TASK_LOCATION_CHANGED,
            target=f"task:{task.pk}",
            metadata={
                "before": str(before) if before else None,
                "after": str(node_id) if node_id else None,
            },
        )
