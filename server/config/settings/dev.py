"""Settings de desarrollo local.

Se activa por defecto en `manage.py` (`DJANGO_SETTINGS_MODULE=config.settings.dev`).
`python manage.py check --deploy` corre contra este módulo en CI/local (Paso
3/4): los warnings de "dev-settings" que produce (DEBUG=True, SECRET_KEY sin
rotar, sin HSTS/SSL redirect, etc.) son **esperados y aceptables** aquí —
son exactamente lo que `--deploy` está diseñado para señalar en un entorno
de desarrollo, y no bloquean el comando (no lanza excepción, solo advierte).
Ese endurecimiento real se hace en `prod.py` y se completa en pasos de
despliegue posteriores (fuera del alcance del Paso 4).
"""

from __future__ import annotations

import os

from .base import *  # noqa: F401,F403

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes"}

# "*" es intencional y solo válido en dev: conveniencia para levantar el
# servidor con `docker compose` sin tener que fijar el host de antemano.
# prod.py exige una lista explícita vía DJANGO_ALLOWED_HOSTS.
ALLOWED_HOSTS = ["*"]
