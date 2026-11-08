"""Router de `/v1/projects/*` (avance de campo). Solo traducción."""

from __future__ import annotations

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
    """`GET /v1/projects/tasks?version=`: la lista, o "sin cambios" si `version` coincide."""
    items = field_tasks.field_tasks(get_request_context(request))
    current = field_tasks.list_version(items)
    if version == current:
        return {"version": current, "changed": False, "items": []}
    return {"version": current, "changed": True, "items": items}
