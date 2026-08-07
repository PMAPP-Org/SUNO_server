"""Lectura autorizada de un job (ADR-004: la lógica no vive en la vista).

Una sola función, y las dos comprobaciones en el orden que exige RD-11:

1. **Buscar dentro de la organización del solicitante** (`scoped_qs`), de modo
   que un job de otra organización sea indistinguible de uno inexistente. El
   filtro por `org_id` es el punto de partida, no un `.filter()` encadenado
   después que alguien pueda olvidar.
2. **Autorizar** con `check_read_job_output` (ADMIN, RD-12/H-17).

En ese orden y no al revés: si se autorizara primero sobre una fila leída sin
scopar, un `ADMIN` de la organización A que pidiera el `id` de un job de la
organización B recibiría un `403` — y un `403` confirma que ese job existe,
que es exactamente el oráculo de enumeración que RD-11 cierra. Buscando
primero dentro de la propia organización, la respuesta es `404` y no dice nada.
"""

from __future__ import annotations

import uuid

from visitacampo.core.context import RequestContext
from visitacampo.core.models import Job
from visitacampo.core.policies import CODE_NOT_FOUND, PolicyDenied, check_read_job_output
from visitacampo.core.scoping import scoped_qs

__all__ = ["JobNotFound", "job_status"]


class JobNotFound(PolicyDenied):
    """`404 not_found`: no existe, o no es de esta organización (RD-11)."""

    def __init__(self) -> None:
        super().__init__(404, CODE_NOT_FOUND, "Job not found.")


def job_status(ctx: RequestContext, job_id: uuid.UUID) -> Job:
    """La fila `job` de `job_id`, si el solicitante puede verla.

    Devuelve el modelo entero y no un DTO: el schema del contrato
    (`schemas.JobSchema`) es quien decide qué columnas salen, y ese recorte
    escrito en un solo sitio es más difícil de romper que un `dict` armado a
    mano en cada camino de salida.
    """
    job = scoped_qs(ctx, Job).filter(pk=job_id).first()
    if job is None:
        raise JobNotFound()
    check_read_job_output(ctx, job)
    return job
