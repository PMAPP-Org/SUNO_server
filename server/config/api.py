"""Router raíz de la API HTTP (Django Ninja), montado en `/v1/` (`config/urls.py`).

Paso 4 montó la infraestructura del router y el endpoint de salud. El Paso 7
añadió lo primero del contrato de verdad: autenticación (`/v1/auth/*`) y
negociación de capacidades (`/v1/server/info`). El Paso 8 añade el motor de
sincronización (`POST /v1/sync/push`). El resto (pull, adjuntos, jobs, admin)
llega en los Pasos 9-10, 17 y 19-20 — sigue sin adelantarse aquí (ADR-004:
"la lógica vive en la capa de servicios, no en las vistas").

**Autenticación por defecto**: el contrato declara `security: [bearerAuth]` a
nivel global y la desactiva operación por operación (`security: []` en
`login`, `refresh` y los endpoints de salud). Se refleja igual aquí:
`auth=JwtAuth()` en la API y `auth=None` en las operaciones exentas. El
sentido de la excepción importa — con el defecto al revés, olvidarse de
declarar `auth=` en un endpoint nuevo lo dejaría **abierto**; así, olvidarse
lo deja cerrado (deny-by-default, ADR-013).
"""

from ninja import NinjaAPI

from visitacampo.authn.api import router as auth_router
from visitacampo.authn.auth import JwtAuth
from visitacampo.core.api_errors import register_error_handlers
from visitacampo.health.api import router as health_router
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
