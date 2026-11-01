"""Las tareas del cronograma que el agente puede vincular a una visita.

Tareas **vigentes de detalle** de las que quien llama es responsable efectivo
(directo o por la rama, `access.covers`), con su último % validado. Se entrega
la lista completa con una huella (`list_version`): el teléfono la reemplaza, y
cualquier cambio que la altere —importación, responsables, revisiones— cambia
la huella sin que nadie tenga que mover un cursor.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.db.models import Q

from visitacampo.core.context import RequestContext
from visitacampo.core.models import Task, Visit
from visitacampo.core.policies import check_read_field_tasks
from visitacampo.core.scoping import scoped_qs

from .access import responsible_outlines
from .field_progress import validated_pct_by_task


def field_tasks(ctx: RequestContext) -> list[dict[str, Any]]:
    """Contrato `FieldTask`, en el orden de proyecto y del Project. Tres queries."""
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
    pct = validated_pct_by_task(scoped_qs(ctx, Visit), task_ids=[t.pk for t in tasks])
    return [
        {
            "id": str(t.pk),
            "project_id": str(t.project_id),
            "project_name": t.project.name,
            "wbs": t.wbs,
            "outline_number": t.outline_number,
            "name": t.name,
            "last_validated_pct": float(pct[t.pk]) if t.pk in pct else None,
        }
        for t in tasks
    ]


def list_version(items: list[dict[str, Any]]) -> str:
    """Huella SHA-256 de la lista: cambia si cambia cualquier dato entregado."""
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
