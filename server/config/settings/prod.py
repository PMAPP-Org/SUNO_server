"""Settings de producción.

Paso 4: solo lo mínimo para que el módulo sea correcto y coherente con
`base.py` (DEBUG=False, ALLOWED_HOSTS explícito por entorno). El
endurecimiento completo de despliegue (gunicorn+uvicorn, proxies confiables
detrás de Caddy / `X-Forwarded-Proto`, HSTS, `SECURE_SSL_REDIRECT`, etc. —
ADR-009 RD-15, ADR-010) se configura en los pasos de infraestructura de
despliegue, no en este scaffolding.
"""

from __future__ import annotations

import os

from .base import *  # noqa: F401,F403

DEBUG = False

# Sin valor por defecto a propósito: en producción esta variable debe venir
# siempre declarada explícitamente (infra/.env.example / despliegue), un
# ALLOWED_HOSTS vacío con DEBUG=False hace que Django rechace todas las
# peticiones (fail-closed, no fail-open).
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
    if host.strip()
]
