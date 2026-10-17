#!/usr/bin/env bash
# Despliega una release en el servidor, con rollback automático.
#
# Uso (como `deploy`, desde el clon del repositorio en el servidor):
#   bash infra/scripts/deploy.sh vX.Y.Z ghcr.io/<owner>/visitacampo-api@sha256:<digest>
#
# Lo invoca .github/workflows/deploy.yml por SSH tras verificar la atestación
# de procedencia de la imagen; también vale a mano (docs/operacion/despliegue.md).
#
# Pasos: checkout del tag → backup previo → `docker compose up` con la imagen
# fijada por digest → espera a que `api` esté sana → comprueba /v1/readyz por
# Caddy. Si algo falla, vuelve a la versión e imagen anteriores (estado en
# .deploy-state) y comprueba de nuevo.
#
# Códigos de salida:
#   0  desplegado
#   2  falló y se volvió a la versión anterior (el servicio sigue en pie)
#   3  falló y el rollback también: seguir docs/operacion/restauracion.md
#   1  error de uso o de precondición (no se tocó nada en marcha)
#
# Variables opcionales (las usa el CI para probar el script de verdad):
#   DEPLOY_NO_FETCH=1      no hace `git fetch` (tags ya presentes)
#   DEPLOY_SKIP_PULL=1     no hace `docker compose pull` (imagen local)
#   DEPLOY_ALLOW_TAG=1     acepta una imagen sin digest (solo CI)
#   SKIP_BACKUP=1          sin backup previo
#   DEPLOY_WAIT_TIMEOUT    segundos de espera a que `api` esté sana (def. 180)
#   READYZ_CURL_OPTS       opciones extra de curl (CI: -k por la CA interna)
set -euo pipefail

# El script hace `git checkout` de otra versión del repositorio, que incluye a
# este mismo archivo, y bash lee los scripts por partes mientras los ejecuta.
# Para no ejecutar una mezcla de dos versiones, se copia a un temporal y se
# relanza desde ahí antes de tocar nada.
if [ -z "${DEPLOY_SH_DETACHED:-}" ]; then
	REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
	copy="$(mktemp)"
	cp "${BASH_SOURCE[0]}" "$copy"
	DEPLOY_SH_DETACHED=1 REPO_DIR="$REPO_DIR" exec bash "$copy" "$@"
fi
trap 'rm -f "${BASH_SOURCE[0]}"' EXIT

version="${1:-}"
image="${2:-}"

log() { printf '%s deploy: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

[ -n "$version" ] && [ -n "$image" ] || die "uso: deploy.sh vX.Y.Z <imagen@sha256:digest>"
[[ "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || die "versión inválida: $version"
if [ "${DEPLOY_ALLOW_TAG:-0}" != 1 ] && [[ "$image" != *@sha256:* ]]; then
	die "la imagen debe ir fijada por digest (…@sha256:…): $image"
fi

cd "$REPO_DIR"
STATE="$REPO_DIR/.deploy-state"
ENV_FILE="$REPO_DIR/infra/.env"

[ -f "$ENV_FILE" ] || die "falta $ENV_FILE"
grep -qE '^COMPOSE_FILE=.*docker-compose\.prod\.yml' "$ENV_FILE" \
	|| die "COMPOSE_FILE en infra/.env debe incluir docker-compose.prod.yml"
domain="$(sed -n 's/^CADDY_DOMAIN=//p' "$ENV_FILE" | tail -1)"
[ -n "$domain" ] || die "falta CADDY_DOMAIN en infra/.env"

compose() { docker compose --project-directory infra "$@"; }

checkout() {
	[ "${DEPLOY_NO_FETCH:-0}" = 1 ] || git fetch --tags --quiet
	git -c advice.detachedHead=false checkout --quiet "refs/tags/$1"
}

ready() {
	local _
	for _ in $(seq 1 15); do
		# shellcheck disable=SC2086 # READYZ_CURL_OPTS son opciones separadas a propósito
		if curl -fsS -o /dev/null ${READYZ_CURL_OPTS:-} --resolve "${domain}:443:127.0.0.1" \
			"https://${domain}/v1/readyz"; then
			return 0
		fi
		sleep 2
	done
	return 1
}

start() { # $1 = imagen
	export VISITACAMPO_API_IMAGE="$1"
	[ "${DEPLOY_SKIP_PULL:-0}" = 1 ] || compose pull api worker
	compose up -d --remove-orphans --wait --wait-timeout "${DEPLOY_WAIT_TIMEOUT:-180}" && ready
}

previous="$(cat "$STATE" 2> /dev/null || true)"
log "versión actual: ${previous:-ninguna}; desplegando $version ($image)"

checkout "$version"

if [ "${SKIP_BACKUP:-0}" != 1 ]; then
	if grep -qE '^BACKUP_AGE_RECIPIENT=.+' "$ENV_FILE"; then
		log "backup previo"
		compose run --rm backup backup.sh || die "el backup previo falló: no se despliega"
	else
		log "AVISO: backups desactivados (sin BACKUP_AGE_RECIPIENT): despliegue sin backup previo"
	fi
fi

if start "$image"; then
	echo "$version $image" > "$STATE"
	docker image prune -f > /dev/null || true
	log "OK: $version en marcha y /v1/readyz responde"
	exit 0
fi

log "FALLO al desplegar $version"
compose logs --no-color --tail 50 api || true
if [ -z "$previous" ]; then
	log "no hay versión anterior a la que volver"
	exit 3
fi

read -r prev_version prev_image <<< "$previous"
log "rollback a $prev_version ($prev_image)"
checkout "$prev_version"
if start "$prev_image"; then
	log "rollback correcto: sigue en marcha $prev_version"
	exit 2
fi
log "el rollback TAMBIÉN falló: seguir docs/operacion/restauracion.md"
exit 3
