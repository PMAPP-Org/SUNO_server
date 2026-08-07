"""Piezas compartidas por los tests del Paso 19.

Los tests **sí** pueden usar `Model.objects` (el test de arquitectura excluye
`tests/`), así que aquí se lee la tabla con el ORM aunque el código de
producción use SQL: leer con la otra herramienta es una comprobación gratis de
que las sentencias escriben lo que el modelo dice que hay.
"""

from __future__ import annotations

import uuid
from typing import Any

from visitacampo.core.models import Job, Organization

__all__ = ["make_org", "reload_job"]


def make_org(name: str = "Cola") -> Organization:
    return Organization.objects.create(name=f"{name}-{uuid.uuid4().hex[:8]}")


def reload_job(job_id: uuid.UUID) -> Job:
    return Job.objects.get(pk=job_id)


def job_row(job_id: uuid.UUID) -> dict[str, Any]:
    """La fila como `dict`, para aserciones legibles de un vistazo."""
    job = reload_job(job_id)
    return {
        "status": job.status,
        "attempts": job.attempts,
        "locked_by": job.locked_by,
        "locked_until": job.locked_until,
        "output": job.output,
        "error": job.error,
        "finished_at": job.finished_at,
    }
