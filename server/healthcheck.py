"""Healthcheck del contenedor `api` (infra/docker-compose.yml).

Pide `/v1/healthz` directamente a gunicorn, como lo haría Caddy: con un `Host`
de `ALLOWED_HOSTS` y `X-Forwarded-Proto: https`. Sin eso, `config.settings.prod`
responde 400 (host no permitido) o redirige a HTTPS (`SECURE_SSL_REDIRECT`), y
el contenedor quedaría marcado como `unhealthy` estando sano. Solo usa la
stdlib: la imagen no trae curl.

Sale con 0 si la respuesta es 200, con 1 en cualquier otro caso.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("HEALTHCHECK_URL", "http://127.0.0.1:8000/v1/healthz")


def _host() -> str:
    allowed = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")]
    return next((h for h in allowed if h and h != "*" and not h.startswith(".")), "localhost")


def main() -> int:
    request = urllib.request.Request(
        URL, headers={"Host": _host(), "X-Forwarded-Proto": "https"}
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:  # nosec B310: URL fija local
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, OSError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
