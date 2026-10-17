#!/usr/bin/env bash
# Un backup completo: BD cifrada + adjuntos, con rotación y métrica de éxito.
# Ver infra/backup/README.md. Variables:
#   PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE  conexión a PostgreSQL
#   BACKUP_AGE_RECIPIENT    llave PÚBLICA age (age1…); la privada no está aquí
#   BACKUP_REMOTE           destino rclone, p. ej. `backup:visitacampo-backups`
#   BACKUP_RETENTION_DAYS   días que se conservan los volcados (def. 30)
#   BACKUP_BLOBS_SOURCE     origen de adjuntos: remoto rclone (`blobs:bucket`)
#                           o ruta local (`/blobs`); vacío = no se copian
#   BACKUP_METRICS_DIR      directorio del textfile collector (def. /metrics)
set -euo pipefail

: "${BACKUP_AGE_RECIPIENT:?BACKUP_AGE_RECIPIENT (llave pública age) es obligatoria}"
: "${BACKUP_REMOTE:?BACKUP_REMOTE (destino rclone) es obligatorio}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
METRICS_DIR="${BACKUP_METRICS_DIR:-/metrics}"

log() { printf '%s backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
object="db/visitacampo-${stamp}.dump.age"

# 1. Volcado a disco y cifrado ANTES de subir: si pg_dump falla a medias, no
#    llega al bucket un objeto truncado con aspecto de backup válido.
log "pg_dump de ${PGDATABASE}@${PGHOST}"
pg_dump --format=custom --no-owner --no-privileges --file "$work/db.dump"
pg_restore --list "$work/db.dump" > /dev/null # el volcado se puede leer
age --recipient "$BACKUP_AGE_RECIPIENT" --output "$work/db.dump.age" "$work/db.dump"
rm -f "$work/db.dump" # en claro, nunca más tiempo del necesario

log "subiendo ${object} ($(stat -c %s "$work/db.dump.age") bytes)"
rclone copyto "$work/db.dump.age" "${BACKUP_REMOTE}/${object}"

# 2. Adjuntos: direccionados por contenido e inmutables (ADR-007), así que
#    `copy` (nunca `sync`): un borrado en origen no se propaga al backup.
if [ -n "${BACKUP_BLOBS_SOURCE:-}" ]; then
  log "copiando adjuntos desde ${BACKUP_BLOBS_SOURCE}"
  rclone copy --immutable "$BACKUP_BLOBS_SOURCE" "${BACKUP_REMOTE}/blobs"
fi

# 3. Rotación de volcados (los adjuntos no rotan: son el dato, no una copia).
log "borrando volcados de más de ${RETENTION_DAYS} días"
rclone delete --min-age "${RETENTION_DAYS}d" "${BACKUP_REMOTE}/db"

# 4. Métrica para el monitoreo (alerta BackupStale): escritura atómica.
if [ -d "$METRICS_DIR" ] && [ -w "$METRICS_DIR" ]; then
  tmp="$METRICS_DIR/.backup.prom.$$"
  {
    echo '# HELP visitacampo_backup_last_success_timestamp_seconds Fin del último backup correcto.'
    echo '# TYPE visitacampo_backup_last_success_timestamp_seconds gauge'
    echo "visitacampo_backup_last_success_timestamp_seconds $(date +%s)"
    echo '# HELP visitacampo_backup_last_size_bytes Tamaño cifrado del último volcado.'
    echo '# TYPE visitacampo_backup_last_size_bytes gauge'
    echo "visitacampo_backup_last_size_bytes $(stat -c %s "$work/db.dump.age")"
  } > "$tmp"
  mv "$tmp" "$METRICS_DIR/backup.prom"
fi

log "OK ${object}"
