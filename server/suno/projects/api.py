"""Router de `/v1/projects/*` (avance de campo). Solo traducción."""

from __future__ import annotations

from uuid import UUID

from ninja import Query, Router

from suno.core.context import get_request_context

from . import field_tasks
from .schemas import FieldTaskList

__all__ = ["router"]

router = Router(tags=["projects"])


@router.get(
    "/projects/tasks",
    response={200: FieldTaskList},
    summary="Tareas del cronograma de las que quien llama es responsable",
    operation_id="projectsTasksList",
)
def list_field_tasks(request, version: str | None = Query(None, max_length=64)):
    """`GET /v1/projects/tasks?version=`: la lista y el catálogo de ítems de sus proyectos,
    o "sin cambios" si `version` coincide."""
    ctx = get_request_context(request)
    items = field_tasks.field_tasks(ctx)
    catalog = field_tasks.project_items(ctx, [UUID(i["project_id"]) for i in items])
    current = field_tasks.list_version(items, catalog)
    if version == current:
        return {"version": current, "changed": False, "items": [], "project_items": []}
    return {"version": current, "changed": True, "items": items, "project_items": catalog}
