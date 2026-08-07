#!/bin/sh
# VisitaCampo — entrypoint del contenedor `api`. Adaptado del patrón de
# `qfieldcloud/docker-app/entrypoint.sh` ("espera a servicios, luego exec"),
# con las dos tareas adicionales que qfieldcloud reparte en su CI/imagen y
# que aquí hacen falta en el propio arranque: migrar y recolectar estáticos.
set -eu

python wait_for_db.py

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"
