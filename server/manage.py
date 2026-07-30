#!/usr/bin/env python
"""Utilidad de línea de comandos de Django (VisitaCampo backend).

Por defecto usa `config.settings.dev` (Paso 4: settings divididos en
base/dev/prod, ver server/config/settings/). En despliegue, `DJANGO_SETTINGS_MODULE`
se sobreescribe a `config.settings.prod` vía variable de entorno — nunca se
cambia este valor por defecto, para que un `manage.py` corrido sin variables
de entorno explícitas jamás caiga por accidente en la configuración de
producción.
"""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "No se pudo importar Django. ¿Está instalado y activo el "
            "entorno virtual? Ver server/requirements.txt."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
