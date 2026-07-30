"""Entry point ASGI. ADR-004: en producción se sirve con gunicorn + workers
`uvicorn` apuntando a `config.asgi:application` (la configuración de
gunicorn en sí es de despliegue, fuera del alcance de este paso)."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

application = get_asgi_application()
