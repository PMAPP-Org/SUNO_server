"""Entry point WSGI. No se usa en producción (ADR-004: gunicorn con workers
`uvicorn`, ver `config/asgi.py`); se conserva por compatibilidad con
herramientas que todavía esperan un `wsgi.py` (p.ej. algunos hosts de
desarrollo local)."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

application = get_wsgi_application()
