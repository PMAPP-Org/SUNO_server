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

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False


def _require_strong_secret_key(value: str | None) -> str:
    """`DJANGO_SECRET_KEY` obligatoria y fuerte en producción (fail-closed).

    `base.py` cae a un valor `django-insecure-…` para que el desarrollo local
    funcione sin configurar nada. Si producción heredara ese valor (o el
    placeholder de `infra/.env.example`), cualquiera que lea el repositorio
    podría firmar cookies de sesión del Admin y demás datos firmados por
    Django (OWASP A02/A04). Criterios de `check --deploy` (security.W009):
    al menos 50 caracteres y 5 distintos, sin el prefijo `django-insecure-`.
    """
    if not value:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY es obligatoria en producción.")
    if value.startswith("django-insecure-") or "CAMBIAR" in value.upper():
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY tiene el valor de desarrollo o de la plantilla .env.example."
        )
    if len(value) < 50 or len(set(value)) < 5:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY es demasiado débil (mínimo 50 caracteres, 5 distintos)."
        )
    return value


SECRET_KEY = _require_strong_secret_key(os.environ.get("DJANGO_SECRET_KEY"))

# Sin valor por defecto a propósito: en producción esta variable debe venir
# siempre declarada explícitamente (infra/.env.example / despliegue), un
# ALLOWED_HOSTS vacío con DEBUG=False hace que Django rechace todas las
# peticiones (fail-closed, no fail-open).
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
    if host.strip()
]

# --- RD-15: cabeceras de proxy confiable detrás de Caddy (doc 08 §5, ADR-010) ---
# `infra/Caddyfile` sobrescribe INCONDICIONALMENTE X-Forwarded-Proto/For con
# el valor real de la conexión entrante a Caddy (ver los comentarios de ese
# archivo) — Django puede confiar en `X-Forwarded-Proto` precisamente porque
# Caddy es el único borde de la red que la escribe (el servicio `api` no
# publica puertos); un cliente en HTTP plano no puede spoofear "https"
# porque Caddy la reemplaza, nunca la reenvía.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# RD-15 literal: `USE_X_FORWARDED_HOST=False`. Caddy ya entrega el Host
# original (`header_up Host {host}`) y Django lo valida contra ALLOWED_HOSTS;
# una segunda fuente de verdad para el host solo añadiría superficie de
# envenenamiento de URLs absolutas.
USE_X_FORWARDED_HOST = False

# Redirige cualquier request HTTP plano que llegue a Django (no debería
# ocurrir nunca detrás de Caddy, que ya solo expone 80/443 con TLS
# automático, pero es la misma defensa en profundidad que `manage.py check
# --deploy` exige).
SECURE_SSL_REDIRECT = True

# HSTS: un año, incluye subdominios. Coherente con la cabecera que
# `infra/Caddyfile` ya envía a nivel de proxy (`Strict-Transport-Security`) —
# Django la envía también a nivel de aplicación por si algún día el proxy
# cambia sin que alguien recuerde tocar los dos sitios.
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Cookies de sesión/CSRF solo por HTTPS. VisitaCampo autentica la API con
# JWT (ADR-008), no con cookies de sesión, pero Django Admin (back-office de
# fase 1, ADR-004) sí las usa.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# --- Estáticos (Django Admin: CSS/JS de `django.contrib.admin`) ------------
# `staticfiles` usa el backend de whitenoise (no el `ManifestStaticFilesStorage`
# de Django a secas): mismo manifiesto con hash por contenido para cache
# busting, más compresión gzip/Brotli pregenerada en `collectstatic`, que es
# lo que permite a `WhiteNoiseMiddleware` (`config/settings/base.py`) servir
# los archivos ya comprimidos sin comprimirlos en cada request. Solo en
# `prod.py` (no en `base.py`/`dev.py`): requiere que `collectstatic` ya haya
# corrido y generado el manifiesto (`staticfiles.json`), y `dev.py` no lo
# garantiza fuera de los contenedores de `infra/docker-compose.yml`.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
