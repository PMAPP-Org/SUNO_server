#!/usr/bin/env bash
# Proceso del servicio `backup`: un backup al arrancar y luego cada
# BACKUP_INTERVAL_HOURS (def. 24). Un fallo se registra y se reintenta en el
# siguiente ciclo; si los fallos se repiten, la métrica deja de avanzar y el
# monitoreo dispara BackupStale (infra/monitoring/prometheus/alerts.yml).
set -uo pipefail

interval_hours="${BACKUP_INTERVAL_HOURS:-24}"

# Sin llave pública no hay backup posible. En desarrollo es lo normal; en
# producción la métrica no existirá y el monitoreo alerta (BackupMissing).
if [ -z "${BACKUP_AGE_RECIPIENT:-}" ] || [ -z "${BACKUP_REMOTE:-}" ]; then
  printf '%s backup: DESACTIVADO (falta BACKUP_AGE_RECIPIENT o el destino). Ver infra/backup/README.md\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >&2
  exec sleep infinity
fi

while true; do
  if ! backup.sh; then
    printf '%s backup: FALLÓ, se reintenta en %sh\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$interval_hours" >&2
  fi
  sleep "$((interval_hours * 3600))"
done
