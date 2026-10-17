#!/usr/bin/env bash
# Genera infra/.env para levantar el stack de producción en CI
# (.github/workflows/dast.yml): settings de prod, secretos aleatorios de un
# solo uso, clave JWT Ed25519 nueva, Caddy sirviendo `$DAST_HOST` con su CA
# interna y adjuntos en el filesystem del contenedor (sin R2/MinIO).
#
# Nada de esto vale fuera del run: cada ejecución genera secretos nuevos y
# el archivo muere con el runner. Nunca usar para un despliegue real.
#
# Imprime en stdout la contraseña del usuario de demo, para el paso que lo
# siembra con `bootstrap_demo`.
set -euo pipefail

ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"

# Nombre con punto bajo `.localhost`: Caddy le emite certificado con su CA
# interna igual que a `localhost`, pero un cliente Java (ZAP) solo envía SNI
# para nombres con punto, y sin SNI Caddy corta el handshake.
DAST_HOST="${DAST_HOST:-api.visitacampo.localhost}"

rand() { openssl rand -hex 32; }

POSTGRES_PASSWORD="$(rand)"
DEMO_PASSWORD="$(rand)"
JWT_KEY_B64="$(openssl genpkey -algorithm ed25519 | base64 -w0)"

cat > "$ENV_FILE" <<EOF
COMPOSE_FILE=docker-compose.yml
POSTGRES_DB=visitacampo
POSTGRES_USER=visitacampo
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_HOST=db
POSTGRES_PORT=5432
DJANGO_SECRET_KEY=$(rand)
DJANGO_DEBUG=false
DJANGO_SETTINGS_MODULE=config.settings.prod
DJANGO_ALLOWED_HOSTS=${DAST_HOST}
JWT_SIGNING_KEY=${JWT_KEY_B64}
JWT_SIGNING_KEY_FILE=
STORAGE_BACKEND=localfs
STORAGE_S3_ENDPOINT_URL=
STORAGE_S3_ACCESS_KEY_ID=
STORAGE_S3_SECRET_ACCESS_KEY=
STORAGE_S3_BUCKET_NAME=
STORAGE_S3_REGION=auto
STORAGE_S3_USE_PATH_STYLE=false
EMAIL_HOST=localhost
EMAIL_PORT=25
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=false
CADDY_DOMAIN=${DAST_HOST}
CADDY_ACME_EMAIL=ci@example.invalid
# TEST-NET-3 (RFC 5737): ningún cliente real del CI está ahí, así que el
# DAST comprueba que /admin/ queda cerrado.
CADDY_ADMIN_ALLOWED_IPS=203.0.113.0/24
# El DAST crea la organización después de arrancar el worker: con 5 s se
# comprueba en segundos que el worker la descubre sin reiniciarse.
JOB_BOOTSTRAP_INTERVAL_SECONDS=5
EOF
chmod 600 "$ENV_FILE"

echo "$DEMO_PASSWORD"
