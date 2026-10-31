#!/usr/bin/env bash
# Corre pytest (u otro comando) del servidor en Docker, sin Python local:
# un PostgreSQL desechable y python:3.12-slim con requirements-dev.lock
# instalado en un volumen reutilizable (se reinstala si el lock cambia).
#
#   infra/scripts/test_docker.sh [args de pytest]       por defecto: toda la suite
#   infra/scripts/test_docker.sh --cmd "ruff check ."    otro comando dentro de server/
#   infra/scripts/test_docker.sh --down                  borra el Postgres y la red
set -euo pipefail
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SERVER_DIR="$(cd "$ROOT/server" && { pwd -W 2>/dev/null || pwd; })"
NET=suno-test
DB=suno-test-db
VENV=suno-test-venv

if [ "${1:-}" = "--down" ]; then
	docker rm -f "$DB" >/dev/null 2>&1 || true
	docker network rm "$NET" >/dev/null 2>&1 || true
	exit 0
fi

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
if [ -z "$(docker ps -q -f "name=^${DB}$")" ]; then
	docker rm -f "$DB" >/dev/null 2>&1 || true
	docker run -d --name "$DB" --network "$NET" \
		-e POSTGRES_DB=visitacampo -e POSTGRES_USER=visitacampo -e POSTGRES_PASSWORD=test \
		postgres:16-alpine >/dev/null
	# El entrypoint reinicia Postgres una vez tras inicializar: esperar a que
	# responda dos veces seguidas evita conectar justo antes del reinicio.
	until docker exec "$DB" pg_isready -U visitacampo -q && sleep 2 &&
		docker exec "$DB" pg_isready -U visitacampo -q; do sleep 1; done
fi

if [ "${1:-}" = "--cmd" ]; then
	CMD="${2:?falta el comando para --cmd}"
else
	CMD="pytest -q -p no:cacheprovider $*"
fi

docker run --rm --network "$NET" -v "$SERVER_DIR:/app" -v "$VENV:/venv" -w /app \
	-e POSTGRES_HOST="$DB" -e POSTGRES_PASSWORD=test -e VISITACAMPO_REQUIRE_DB=1 \
	-e PATH="/venv/bin:/usr/local/bin:/usr/bin:/bin" \
	python:3.12-slim sh -c '
		if ! cmp -s requirements-dev.lock /venv/.lock; then
			python -m venv /venv &&
			pip install -q --require-hashes -r requirements-dev.lock &&
			cp requirements-dev.lock /venv/.lock
		fi
		'"$CMD"
