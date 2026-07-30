"""Esquemas Pydantic (vía Ninja `Schema`) para los endpoints de salud.

Refleja `components.schemas.HealthStatus` de `server/openapi/v1.yaml`
(Paso 1 — contrato). Deliberadamente mínimo: nunca versión, hostname ni qué
dependencia concreta falla (doc 08 §6 / ADR-004).
"""

from typing import Literal

from ninja import Schema


class HealthStatus(Schema):
    status: Literal["ok", "unavailable"]
