"""Router raíz de la API HTTP (Django Ninja), montado en `/v1/` (`config/urls.py`).

Paso 4 montó la infraestructura del router y el endpoint de salud. El Paso 7
añadió lo primero del contrato de verdad: autenticación (`/v1/auth/*`) y
negociación de capacidades (`/v1/server/info`). El Paso 8 añade el motor de
sincronización (`POST /v1/sync/push`) y el Paso 9 el pull. El Paso 17 añade el
carril de adjuntos (`/v1/attachments/*`) y el Paso 19 el estado de los jobs
(`GET /v1/jobs/{id}`), que es toda la superficie HTTP de la cola: el worker no
habla con nadie por HTTP. Con eso, el contrato del Paso 1 queda completo.

**Autenticación por defecto**: el contrato declara `security: [bearerAuth]` a
nivel global y la desactiva operación por operación (`security: []` en
`login`, `refresh` y los endpoints de salud). Se refleja igual aquí:
`auth=JwtAuth()` en la API y `auth=None` en las operaciones exentas. El
sentido de la excepción importa — con el defecto al revés, olvidarse de
declarar `auth=` en un endpoint nuevo lo dejaría **abierto**; así, olvidarse
lo deja cerrado (deny-by-default, ADR-013).
"""

from ninja import NinjaAPI

from visitacampo.attachments.api import router as attachments_router
from visitacampo.authn.api import router as auth_router
from visitacampo.authn.auth import JwtAuth
from visitacampo.basemaps.api import router as basemaps_router
from visitacampo.core.api_errors import register_error_handlers
from visitacampo.health.api import router as health_router
from visitacampo.jobs.api import router as jobs_router
from visitacampo.layers.api import router as layers_router
from visitacampo.privacy.api import router as privacy_router
from visitacampo.serverinfo.api import router as server_info_router
from visitacampo.sync.api import router as sync_router

api = NinjaAPI(
    title="VisitaCampo API",
    version="v1",
    # OpenAPI real ya vive versionado en server/openapi/v1.yaml (Paso 1);
    # el docs UI autogenerado de Ninja es solo una ayuda de desarrollo.
    urls_namespace="v1",
    auth=JwtAuth(),
)

# Cuerpo de error uniforme del contrato (doc 08 §6) para toda la API.
register_error_handlers(api)

api.add_router("", health_router)
api.add_router("", auth_router)
api.add_router("", server_info_router)
api.add_router("", sync_router)
api.add_router("", attachments_router)
api.add_router("", jobs_router)
api.add_router("", privacy_router)
api.add_router("", basemaps_router)
api.add_router("", layers_router)
