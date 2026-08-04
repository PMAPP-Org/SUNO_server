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

from django.contrib.auth.hashers import Argon2PasswordHasher

from .base import *  # noqa: F401,F403

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes"}

# "*" es intencional y solo válido en dev: conveniencia para levantar el
# servidor con `docker compose` sin tener que fijar el host de antemano.
# prod.py exige una lista explícita vía DJANGO_ALLOWED_HOSTS.
ALLOWED_HOSTS = ["*"]


class DevArgon2PasswordHasher(Argon2PasswordHasher):
    """Argon2id con `memory_cost` reducido, solo para esta máquina de desarrollo.

    Añadido durante el checkpoint 14b (2026-08-03): esta máquina tiene 7.4 GB
    de RAM y con frecuencia <500 MB libres (ver `visitacampo-construccion.md`
    §12); el `memory_cost` por defecto de Django (100 MiB) hace que
    `argon2.low_level.hash_secret` falle con `HashingError: Memory allocation
    error` incluso para un solo hash. 19 MiB es el mínimo recomendado por
    OWASP para Argon2id, no un valor arbitrario débil. Sigue siendo Argon2id
    (ADR-008/RD-09) — solo cambia cuánta memoria pide, no el algoritmo.

    **Solo vive en `dev.py`.** `prod.py` no importa este módulo y usa el
    `Argon2PasswordHasher` sin modificar de `base.py`; este ajuste no debe
    copiarse a `prod.py` bajo ninguna circunstancia.
    """

    memory_cost = 19 * 1024  # 19 MiB (mínimo OWASP), vs. 100 MiB por defecto


PASSWORD_HASHERS = [
    "config.settings.dev.DevArgon2PasswordHasher",
    *PASSWORD_HASHERS[1:],  # noqa: F405 (importado vía `from .base import *`)
]
