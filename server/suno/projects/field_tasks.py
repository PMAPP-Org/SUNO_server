"""Las tareas del cronograma que el agente puede vincular a un registro.

Tareas **vigentes de detalle** de las que quien llama es responsable efectivo
(directo o por la rama, `access.covers`), con su % SUNO, su ubicación y sus
ítems planeados (costos, pieza 4). Se entrega la lista completa con una huella
(`list_version`): el teléfono la reemplaza, y cualquier cambio que la altere
—importación, responsables, revisiones, la matriz, el presupuesto vigente— cambia
la huella sin que nadie tenga que mover un cursor.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from django.db.models import Q

from suno.core.context import RequestContext
from suno.core.models import (
    BudgetItemVersion,
    BudgetRowKind,
    BudgetVersion,
    BudgetVersionStatus,
    Task,
)
from suno.core.policies import check_read_field_tasks
from suno.core.scoping import scoped_qs
from suno.matrix.diff import code_key
from suno.matrix.locations import Tree, load_trees
from suno.matrix.progress import task_progress
from suno.matrix.quantities import plan_lines_with_facts, suno_pct_by_task

from .access import responsible_outlines


def field_tasks(ctx: RequestContext) -> list[dict[str, Any]]:
    """Contrato `FieldTask`, en el orden de proyecto y del Project. Siete queries como mucho:
    responsables, tareas, plan (tres, con los datos de los ítems), % manual y ubicaciones.

    `planned_items` son los ítems que miden la tarea (los mismos que deciden
    `measured_by`): sin capítulos (`plan_lines_with_facts`) ni filas sin cantidad planeada."""
    check_read_field_tasks(ctx, ctx.org_id)
    roots = responsible_outlines(ctx, user_id=ctx.user_id)
    if not roots:
        return []
    cover = Q()
    for project_id, outlines in roots.items():
        branch = Q()
        for outline in outlines:
            branch |= Q(outline_number=outline) | Q(outline_number__startswith=f"{outline}.")
        cover |= Q(project_id=project_id) & branch
    tasks = list(
        scoped_qs(ctx, Task)
        .filter(cover, retired_in__isnull=True, is_summary=False)
        .select_related("project")
        .order_by("project__name", "project_id", "sort_order")
    )
    ids = [t.pk for t in tasks]
    lines, facts = plan_lines_with_facts(ctx, task_ids=ids)
    measured = task_progress(lines)
    pct = suno_pct_by_task(ctx, task_ids=ids, measured=measured)
    planned_items: dict[UUID, list[dict[str, Any]]] = {}
    for ln in lines:
        info = facts.get(ln.item_id)
        if info is None or ln.planned <= 0:
            continue
        planned_items.setdefault(ln.task_id, []).append(
            {
                "item_id": str(ln.item_id),
                "code": info.code,
                "description": info.description,
                "unit": info.unit,
                "planned_quantity": float(ln.planned),
                "executed_quantity": float(ln.executed),
            }
        )
    trees = load_trees(ctx, {t.project_id for t in tasks if t.location_id is not None})

    def tree_of(t: Task) -> Tree | None:
        tree = trees.get(t.project_id)
        if t.location_id is None or tree is None or t.location_id not in tree.nodes:
            return None
        return tree

    def location(t: Task) -> dict[str, str] | None:
        tree = tree_of(t)
        if tree is None or t.location_id is None:
            return None
        return {"id": str(t.location_id), "label": tree.label(t.location_id)}

    def options(t: Task) -> list[dict[str, str]]:
        tree = tree_of(t)
        if tree is None or t.location_id is None:
            return []
        return [
            {"id": str(node_id), "label": tree.label(node_id)}
            for node_id in tree.descendants(t.location_id)
        ]

    return [
        {
            "id": str(t.pk),
            "project_id": str(t.project_id),
            "project_name": t.project.name,
            "wbs": t.wbs,
            "outline_number": t.outline_number,
            "name": t.name,
            "last_validated_pct": float(pct[t.pk]) if t.pk in pct else None,
            "measured_by": "quantities" if t.pk in measured else "pct",
            "location": location(t),
            "location_options": options(t),
            "planned_items": sorted(planned_items.get(t.pk, []), key=lambda i: code_key(i["code"])),
        }
        for t in tasks
    ]


def project_items(ctx: RequestContext, project_ids: Iterable[UUID]) -> list[dict[str, Any]]:
    """Contrato `FieldProjectItems` (pieza 4b): por proyecto, en el orden recibido, los ítems
    medibles (no capítulos) de la versión vigente de cada uno de sus presupuestos, sin
    precios ni montos. Lo usa la app para "Agregar ítem no planeado".

    Un proyecto sin versión vigente va con `items` vacía. Dos queries; ninguna sin
    proyectos."""
    ids = list(dict.fromkeys(project_ids))
    if not ids:
        return []
    current = list(
        scoped_qs(ctx, BudgetVersion)
        .filter(budget__project_id__in=ids, status=BudgetVersionStatus.APPLIED)
        .order_by("budget_id", "-number")
        .distinct("budget_id")
        .values_list("pk", flat=True)
    )
    by_project: dict[UUID, list[dict[str, Any]]] = {project_id: [] for project_id in ids}
    if current:
        rows = (
            scoped_qs(ctx, BudgetItemVersion)
            .filter(version_id__in=current, kind=BudgetRowKind.ITEM)
            .values_list(
                "version__budget__project_id", "item_id", "item__code", "description", "unit"
            )
        )
        for project_id, item_id, code, description, unit in rows:
            by_project[project_id].append(
                {"item_id": str(item_id), "code": code, "description": description, "unit": unit}
            )
    return [
        {
            "project_id": str(project_id),
            "items": sorted(items, key=lambda i: (code_key(i["code"]), i["item_id"])),
        }
        for project_id, items in by_project.items()
    ]


def list_version(items: list[dict[str, Any]], catalog: list[dict[str, Any]] | None = None) -> str:
    """Huella SHA-256 de la lista y del catálogo de ítems (`project_items`, pieza 4b):
    cambia si cambia cualquier dato entregado."""
    payload = {"items": items, "project_items": catalog or []}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
