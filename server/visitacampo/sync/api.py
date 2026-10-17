"""Router de Ninja para `/v1/sync/*` (contrato `server/openapi/v1.yaml`).

ADR-004: la vista no decide nada. Aquí solo viven los **límites de
transporte** (tamaño de lote del push, rango de `limit` del pull) y la
traducción JSON; la lógica de negocio vive en `services.py` (push) y
`pull.py` (bootstrap/pull, Paso 9).

**Autenticación**: el contrato no le pone `security: []` a ninguno de los
tres endpoints, así que los tres heredan el `bearerAuth` global montado en
`config/api.py` (`JwtAuth`, Paso 7). Con él, `RequestContextAuth` deja el
`RequestContext` resuelto —una sola query de roles— **antes** de que corra el
cuerpo de la vista, que es el requisito literal de ADR-013.
"""

from __future__ import annotations

from django.utils import timezone
from ninja import Query, Router
from ninja.errors import HttpError

from visitacampo.core.context import get_request_context
from visitacampo.serverinfo import capabilities as caps
from visitacampo.sync import pull, services
from visitacampo.sync.cursor import InvalidCursor, decode_cursor
from visitacampo.sync.schemas import BootstrapResponse, PullResponse, PushRequest, PushResponse

__all__ = ["CursorInvalid", "PayloadTooLarge", "SchemaVersionUnsupported", "router"]

router = Router(tags=["sync"])


class PayloadTooLarge(HttpError):
    """413 con el `code` uniforme del contrato (`components/responses/PayloadTooLarge`).

    El límite **no** se declara como `max_length` en el esquema Pydantic a
    propósito (ver `schemas.py`): allí Ninja respondería `422` y el cliente no
    recibiría el `payload_too_large` que le dice que **parta el lote**, que es
    la acción de recuperación que el contrato le prescribe.
    """

    code = "payload_too_large"

    def __init__(self, message: str) -> None:
        super().__init__(413, message)


class SchemaVersionUnsupported(HttpError):
    """422 `schema_unsupported` (contrato: `components/responses/UnprocessableEntity`).

    RD-03: el `schema_version` se comprueba **antes** de mirar el contenido del
    lote. Un cliente que hable una versión que este servidor ya no entiende
    debe enterarse por este código —el mismo que negoció en
    `GET /v1/server/info`— y no por un error de validación de un campo suelto.
    """

    code = "schema_unsupported"

    def __init__(self, schema_version: int) -> None:
        super().__init__(
            422,
            f"schema_version {schema_version} is not supported "
            f"(supported: {caps.MIN_SUPPORTED_SCHEMA_VERSION}..{caps.SCHEMA_VERSION}).",
        )


class CursorInvalid(HttpError):
    """400 `validation_error`: el `cursor` no tiene la forma que este servidor emite.

    Se traduce aquí —y no se deja caer al manejador global de `ValueError`
    (`core/api_errors.py`)— porque ese manejador está pensado para un cuerpo
    JSON ilegible y su mensaje sería confuso para un `cursor` de query string
    con mala forma. `sync.cursor.InvalidCursor` hereda de `ValueError` por
    conveniencia interna, no para que este endpoint dependa de esa relación.
    """

    code = "validation_error"

    def __init__(self, exc: InvalidCursor) -> None:
        super().__init__(400, str(exc))


def _check_batch_limits(request, payload: PushRequest) -> None:
    """Los dos límites de ADR-003, leídos de `serverinfo.capabilities`.

    Se leen de allí y no se repiten como literales porque ese módulo es lo que
    el cliente descarga para saber cómo partir sus lotes: si los dos números
    pudieran divergir, el cliente creería tener un permiso que el servidor no
    concede (lo advierte el propio docstring de `capabilities.py`).

    El límite de bytes se mide sobre el cuerpo **ya leído en memoria** por
    Django. Django corta antes en `DATA_UPLOAD_MAX_MEMORY_SIZE` —fijado en
    `settings/base.py` justo por encima de este límite— y ese corte también
    acaba en `413` gracias al manejador de `RequestDataTooBig`
    (`core/api_errors.py`), de modo que un cuerpo gigante nunca se lee entero
    y aun así el cliente recibe el mismo código.
    """
    if len(payload.ops) > caps.SYNC_PUSH_MAX_OPS:
        raise PayloadTooLarge(
            f"Batch has {len(payload.ops)} operations; the limit is {caps.SYNC_PUSH_MAX_OPS}."
        )
    if len(request.body) > caps.SYNC_PUSH_MAX_BYTES:
        raise PayloadTooLarge(f"Batch body exceeds {caps.SYNC_PUSH_MAX_BYTES} bytes.")


@router.post(
    "/sync/push",
    response={200: PushResponse},
    summary="Lote de operaciones idempotentes (aplicación síncrona transaccional)",
    operation_id="syncPush",
)
def sync_push(request, payload: PushRequest):
    """`POST /v1/sync/push`.

    Un `200` **no** significa que todo se aplicara: el resultado es por
    operación (`applied` / `conflict` / `rejected`). Es la diferencia central
    de ADR-003 con la referencia — no hay un job asíncrono al que preguntarle
    después qué pasó; cuando esta respuesta vuelve, la transacción ya commiteó.
    """
    if not (caps.MIN_SUPPORTED_SCHEMA_VERSION <= payload.schema_version <= caps.SCHEMA_VERSION):
        raise SchemaVersionUnsupported(payload.schema_version)
    _check_batch_limits(request, payload)

    results = services.apply_push(
        get_request_context(request),
        device_id=payload.device_id,
        batch_uuid=payload.batch_uuid,
        ops=payload.ops,
    )
    return {
        "schema_version": caps.SCHEMA_VERSION,
        "server_time": timezone.now(),
        "results": results,
    }


@router.get(
    "/sync/bootstrap",
    response={200: BootstrapResponse},
    summary="Estado inicial completo + cursor",
    operation_id="syncBootstrap",
)
def sync_bootstrap(request):
    """`GET /v1/sync/bootstrap`. Se invoca tras instalar la app o tras un `409 cursor_expired`."""
    state = pull.bootstrap_state(get_request_context(request))
    return {
        "schema_version": caps.SCHEMA_VERSION,
        "server_time": timezone.now(),
        **state,
    }


@router.get(
    "/sync/pull",
    response={200: PullResponse},
    summary="Filas cambiadas + tombstones + next_cursor",
    operation_id="syncPull",
)
def sync_pull(
    request,
    cursor: str = Query(..., min_length=1),
    limit: int = Query(caps.SYNC_PULL_DEFAULT_LIMIT, ge=1, le=caps.SYNC_PULL_MAX_LIMIT),
):
    """`GET /v1/sync/pull?cursor=&limit=`.

    `cursor` es opaco (`sync/cursor.py`): se decodifica aquí, en el borde de
    transporte, para que `pull.pull_page` trabaje siempre con el `int` real —
    igual que `sync_push` decide el límite de lote en esta capa y deja la
    transacción a `services.py`.
    """
    try:
        cursor_value = decode_cursor(cursor)
    except InvalidCursor as exc:
        raise CursorInvalid(exc) from exc

    page = pull.pull_page(get_request_context(request), cursor_value=cursor_value, limit=limit)
    return {
        "schema_version": caps.SCHEMA_VERSION,
        "server_time": timezone.now(),
        **page.as_dict(),
    }
