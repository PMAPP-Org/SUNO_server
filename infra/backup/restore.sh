#!/usr/bin/env bash
# Restaura un volcado cifrado sobre la BD de PG* (BORRA lo que haya).
# Uso: restore.sh [latest | db/visitacampo-AAAAMMDDTHHMMSSZ.dump.age]
#
# Necesita la llave PRIVADA age en BACKUP_AGE_IDENTITY_FILE, montada solo
# para restaurar (nunca vive en el servidor). Ver infra/backup/README.md.
set -euo pipefail

: "${BACKUP_REMOTE:?BACKUP_REMOTE (origen rclone) es obligatorio}"
: "${BACKUP_AGE_IDENTITY_FILE:?BACKUP_AGE_IDENTITY_FILE (llave privada age) es obligatoria}"
target="${1:-latest}"

log() { printf '%s restore: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

if [ "$target" = "latest" ]; then
  # Los nombres llevan la fecha en UTC con formato ordenable.
  target="db/$(rclone lsf --files-only "${BACKUP_REMOTE}/db" | sort | tail -1)"
  [ "$target" != "db/" ] || { log "no hay volcados en ${BACKUP_REMOTE}/db"; exit 1; }
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

log "descargando ${target}"
rclone copyto "${BACKUP_REMOTE}/${target}" "$work/db.dump.age"
age --decrypt --identity "$BACKUP_AGE_IDENTITY_FILE" --output "$work/db.dump" "$work/db.dump.age"

log "restaurando en ${PGDATABASE}@${PGHOST} (--clean: reemplaza el contenido actual)"
pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error \
  --dbname "$PGDATABASE" "$work/db.dump"
log "OK ${target}"
