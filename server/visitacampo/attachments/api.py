"""Router de Ninja para `/v1/attachments/*` (contrato `server/openapi/v1.yaml`).

ADR-004: la vista no decide nada. Aquí solo viven la validación de transporte
(el `pattern` del `sha256` de la ruta), la resolución del `RequestContext` y la
traducción a JSON; toda la lógica —autorización, verificación, registro— está
en `services.py`.

**Autenticación**: el contrato no le pone `security: []` a ninguno de los tres
endpoints, así que los tres heredan el `bearerAuth` global montado en
`config/api.py`. Un adjunto es dato de un tenant; no hay aquí ninguna
operación anónima.

**Rate limiting, dicho explícitamente**: el contrato anota "Endpoint
rate-limited (ADR-008)" en `presign`. El proyecto todavía no tiene ninguna
infraestructura de *throttling* —no la ha introducido ningún paso hasta ahora,
ni siquiera para `login`, que es donde ADR-008 la exige primero—, así que este
paso **no la inventa aquí**: montarla solo en este endpoint dejaría la del
login sin hacer y dispersaría la política en dos sitios. Queda anotado como
deuda del paso de endurecimiento que la aborde para toda la API.
"""

from __future__ import annotations

from django.utils import timezone
from ninja import Path, Router, Status

from visitacampo.attachments import services
from visitacampo.attachments.schemas import (
    SHA256_REGEX,
    BlobSchema,
    CommitRequest,
    DownloadUrlResponse,
    PresignRequest,
    PresignResponse,
)
from visitacampo.core.context import get_request_context

__all__ = ["router"]

router = Router(tags=["attachments"])


@router.post(
    "/attachments/presign",
    response={200: PresignResponse},
    summary="URL prefirmada de subida (o respuesta de deduplicación)",
    operation_id="attachmentsPresign",
)
def attachments_presign(request, payload: PresignRequest):
    """`POST /v1/attachments/presign`.

    Un `already_present: true` es el camino feliz más barato del sistema: el
    contenido ya está, el dispositivo no sube nada y el agente de campo no gasta
    datos móviles en una foto que otro compañero ya subió (ADR-007 punto 2).
    """
    result = services.presign(
        get_request_context(request),
        sha256=payload.sha256,
        size=payload.size,
        mime=payload.mime,
        visit_id=payload.visit_id,
    )
    return {
        "already_present": result.already_present,
        "upload_url": result.upload_url,
        "expires_in": result.expires_in,
        "required_headers": result.required_headers,
        "server_time": timezone.now(),
    }


@router.post(
    "/attachments/{sha256}/commit",
    response={201: BlobSchema},
    summary="Confirma y verifica el objeto subido",
    operation_id="attachmentsCommit",
)
def attachments_commit(
    request,
    payload: CommitRequest,
    sha256: str = Path(..., pattern=SHA256_REGEX),
):
    """`POST /v1/attachments/{sha256}/commit`.

    Responde `201` tanto si el blob se registró ahora como si ya estaba: el
    contrato solo declara `201` para el camino de éxito y, para el cliente, el
    resultado es el mismo —el contenido está confirmado— así que distinguirlos
    con un `200` le daría una diferencia sobre la que no puede actuar. La
    distinción sí existe internamente (`CommitResult.created`) y es lo que los
    tests de idempotencia y concurrencia comprueban.
    """
    result = services.commit(
        get_request_context(request),
        sha256=sha256,
        size=payload.size,
        mime=payload.mime,
    )
    # `Status(...)` y no la tupla `(201, ...)`: Django Ninja 1.6 marca la tupla
    # como obsoleta y avisa por cada respuesta.
    return Status(201, result.blob)


@router.get(
    "/attachments/{sha256}/url",
    response={200: DownloadUrlResponse},
    summary="URL prefirmada de descarga",
    operation_id="attachmentsDownloadUrl",
)
def attachments_download_url(request, sha256: str = Path(..., pattern=SHA256_REGEX)):
    """`GET /v1/attachments/{sha256}/url`.

    `404` —y nunca `403`— cuando el solicitante no puede ver ningún adjunto que
    referencie ese contenido: es lo que el contrato pide literalmente y lo que
    RD-11 exige para no convertir el endpoint en un oráculo de existencia.
    """
    ticket = services.download_url(get_request_context(request), sha256=sha256)
    return {
        "url": ticket.url,
        "expires_in": ticket.expires_in,
        "mime": ticket.mime,
        "size": ticket.size,
        "server_time": timezone.now(),
    }
