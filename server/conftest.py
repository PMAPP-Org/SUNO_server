"""Bootstrap de Django para `pytest server/` sin el plugin `pytest-django`.

`.github/workflows/backend.yml` (Paso 3) solo instala `pytest` a secas en
CI, no `pytest-django`. Este conftest hace a mano lo mínimo que ese plugin
haría automáticamente: fijar `DJANGO_SETTINGS_MODULE` y llamar a
`django.setup()` antes de que pytest importe cualquier módulo de test.
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()
