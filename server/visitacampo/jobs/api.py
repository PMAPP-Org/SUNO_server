"""Router de Ninja para `/v1/jobs/{id}` — la **única** superficie pública del Paso 19.

Todo lo demás de este paquete es el worker, que no habla HTTP con nadie. Aquí
solo hay traducción: resolver el `RequestContext` y devolver lo que
`services.job_status` autoriza a devolver (ADR-004).

El endpoint hereda el `bearerAuth` global de `config/api.py` (el contrato no le
pone `security: []`), y no encola nada: el contrato del MVP no declara ningún
`POST /v1/jobs`, porque los tres jobs que existen hoy son periódicos que se
encolan solos. Cuando llegue la exportación CSV del Paso 24, encolarla será
parte de *su* endpoint, no de este.
"""

from __future__ import annotations

import uuid

from ninja import Router

from visitacampo.core.context import get_request_context
from visitacampo.jobs import services
from visitacampo.jobs.schemas import JobSchema

__all__ = ["router"]

router = Router(tags=["jobs"])


@router.get(
    "/jobs/{id}",
    response={200: JobSchema},
    summary="Estado de un job asíncrono",
    operation_id="jobStatus",
)
def job_status(request, id: uuid.UUID):
    """`GET /v1/jobs/{id}`. Solo ADMIN; `404` (no `403`) para un job ajeno.

    El nombre del parámetro es `id` porque así lo declara la ruta del contrato.
    """
    return services.job_status(get_request_context(request), id)
