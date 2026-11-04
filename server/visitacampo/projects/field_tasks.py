"""Las tareas del cronograma que el agente puede vincular a una visita.

Tareas **vigentes de detalle** de las que quien llama es responsable efectivo
(directo o por la rama, `access.covers`), con su % SUNO, su ubicación y sus
ítems planeados (costos, pieza 4). Se entrega la lista completa con una huella
(`list_version`): el teléfono la reemplaza, y cualquier cambio que la altere
—importación, responsables, revisiones, la matriz— cambia la huella sin que
nadie tenga que mover un cursor.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from django.db.models import Q

from visitacampo.core.context import RequestContext
from visitacampo.core.models import Task
from visitacampo.core.policies import check_read_field_tasks
from visitacampo.core.scoping import scoped_qs
from visitacampo.matrix.diff import code_key
from visitacampo.matrix.locations import Tree, load_trees
from visitacampo.matrix.progress import task_progress
from visitacampo.matrix.quantities import item_facts, plan_lines, suno_pct_by_task

from .access import responsible_outlines


def field_tasks(ctx: RequestContext) -> list[dict[str, Any]]:
    """Contrato `FieldTask`, en el orden de proyecto y del Project. Diez queries como mucho:
    responsables, tareas, plan (tres), datos de los ítems, % manual y ubicaciones."""
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
    lines = plan_lines(ctx, task_ids=ids)
    measured = task_progress(lines)
    pct = suno_pct_by_task(ctx, task_ids=ids, measured=measured)
    facts = item_facts(ctx, {ln.item_id for ln in lines})
    planned_items: dict[UUID, list[dict[str, Any]]] = {}
    for ln in lines:
        info = facts.get(ln.item_id)
        if info is None:
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


def list_version(items: list[dict[str, Any]]) -> str:
    """Huella SHA-256 de la lista: cambia si cambia cualquier dato entregado."""
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
