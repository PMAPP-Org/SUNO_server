"""Router de Ninja para `POST /v1/sync/push` (contrato `server/openapi/v1.yaml`).

ADR-004: la vista no decide nada. Aquí solo viven los **límites del lote**
—que son una propiedad del transporte, no del protocolo de sync— y la
traducción JSON; la transacción entera está en `services.py`.

**Autenticación**: el contrato no le pone `security: []`, así que hereda el
`bearerAuth` global montado en `config/api.py` (`JwtAuth`, Paso 7). Con él,
`RequestContextAuth` deja el `RequestContext` resuelto —una sola query de
roles— **antes** de que corra el cuerpo de la vista, que es el requisito
literal de ADR-013 y lo que hace posible que el bucle no consulte permisos.
"""

from __future__ import annotations

from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from visitacampo.core.context import get_request_context
from visitacampo.serverinfo import capabilities as caps
from visitacampo.sync import services
from visitacampo.sync.schemas import PushRequest, PushResponse

__all__ = ["PayloadTooLarge", "SchemaVersionUnsupported", "router"]

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
        raise PayloadTooLarge(
            f"Batch body exceeds {caps.SYNC_PUSH_MAX_BYTES} bytes."
        )


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
    if not (
        caps.MIN_SUPPORTED_SCHEMA_VERSION <= payload.schema_version <= caps.SCHEMA_VERSION
    ):
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
