"""Router raíz de la API HTTP (Django Ninja), montado en `/v1/` (`config/urls.py`).

Paso 4 (scaffolding): solo monta la infraestructura del router y el
endpoint trivial de salud, para probar que el montaje funciona. El resto
del contrato definido en `server/openapi/v1.yaml` (auth, sync, attachments,
jobs, admin...) se implementa en los Pasos 7-10, 17, 19-20 — no se adelanta
aquí (ADR-004: "la lógica vive en la capa de servicios, no en las vistas").
"""

from ninja import NinjaAPI

from visitacampo.health.api import router as health_router

api = NinjaAPI(
    title="VisitaCampo API",
    version="v1",
    # OpenAPI real ya vive versionado en server/openapi/v1.yaml (Paso 1);
    # el docs UI autogenerado de Ninja es solo una ayuda de desarrollo.
    urls_namespace="v1",
)

api.add_router("", health_router)
