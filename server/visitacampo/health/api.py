"""Router de Ninja para los endpoints de salud (`/v1/healthz`, `/v1/readyz`).

Paso 4 (scaffolding): prueba que el montaje de Django Ninja en
`server/config/api.py` funciona. Sigue la regla de arquitectura de ADR-004
("la lógica vive en la capa de servicios, no en las vistas"): estos
handlers no tienen lógica de negocio que delegar todavía, así que son la
implementación completa por ahora — cuando `readyz` necesite comprobar
dependencias reales (Paso 5+), esa comprobación debe vivir en un servicio
(p.ej. `visitacampo/health/services.py`), no crecer inline aquí.
"""

from ninja import Router

from .schemas import HealthStatus

router = Router(tags=["health"])


@router.get("/healthz", auth=None, response=HealthStatus, summary="Liveness")
def healthz(request):
    """Liveness: el proceso está vivo.

    Sin autenticación y sin filtrar información (doc 08 §6, ADR-004): no
    devuelve versión, hostname ni estado de dependencias — ver
    server/openapi/v1.yaml `/v1/healthz`.
    """
    return {"status": "ok"}


@router.get("/readyz", auth=None, response=HealthStatus, summary="Readiness")
def readyz(request):
    """Readiness — PLACEHOLDER (Paso 4).

    Responde siempre `{"status": "ok"}` sin comprobar nada todavía. A partir
    del Paso 5 (cuando exista una conexión real a Postgres/storage que
    verificar) este handler debe:
      - Comprobar la conexión a PostgreSQL y al storage S3-compatible.
      - Devolver 503 con `{"status": "unavailable"}` si alguna dependencia
        crítica no responde, **sin indicar cuál** (doc 08 §6 / ADR-010
        "Observabilidad de bajo coste"; ver server/openapi/v1.yaml
        `/v1/readyz`, que documenta la respuesta 503).
      - Delegar la comprobación en un servicio, no vivir inline aquí
        (regla de arquitectura de ADR-004).
    """
    return {"status": "ok"}
