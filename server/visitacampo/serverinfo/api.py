"""Router de `GET /v1/server/info` — negociación de compatibilidad (doc 08 §6).

**Por qué un paquete propio y no dentro de `health/`**: `/healthz` es
deliberadamente mudo (sin versión, sin dependencias, sin autenticación) porque
está expuesto al mundo; `/v1/server/info` es lo contrario — dice qué versión
de esquema habla el servidor y qué límites aplica— y por eso el contrato lo
declara autenticado. Meterlos en el mismo módulo acabaría, tarde o temprano,
con alguien filtrando en `/healthz` lo que solo debía salir aquí.

**Por qué tampoco es una app de Django**: no tiene modelos. Es un paquete
dentro de la app `visitacampo`, igual que `health/`.
"""

from __future__ import annotations

from django.utils import timezone
from ninja import Router

from visitacampo.serverinfo import capabilities as caps
from visitacampo.serverinfo.schemas import ServerInfoSchema

router = Router(tags=["server"])


@router.get(
    "/server/info",
    response={200: ServerInfoSchema},
    summary="Versión de API, schema_version, hora del servidor y capacidades",
    operation_id="serverInfo",
)
def server_info(request):
    """`GET /v1/server/info`.

    Autenticado: el contrato no le pone `security: []` y documenta un `401`,
    así que hereda el `bearerAuth` global de `config/api.py`. El cliente ya
    tiene token cuando negocia compatibilidad —lo primero que hace tras el
    login es preguntar aquí si puede sincronizar (Paso 13)—.
    """
    return {
        "api_version": "v1",
        "schema_version": caps.SCHEMA_VERSION,
        "min_supported_schema_version": caps.MIN_SUPPORTED_SCHEMA_VERSION,
        "server_time": timezone.now(),
        "capabilities": caps.capabilities(),
    }
