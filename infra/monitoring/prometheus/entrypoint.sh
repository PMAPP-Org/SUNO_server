#!/bin/sh
# Genera prometheus.yml desde la plantilla con el dominio real y arranca.
set -eu
: "${CADDY_DOMAIN:?CADDY_DOMAIN es obligatoria}"
sed "s|__CADDY_DOMAIN__|${CADDY_DOMAIN}|g" /etc/prometheus/prometheus.yml.tmpl > /prometheus/prometheus.yml
exec /bin/prometheus \
  --config.file=/prometheus/prometheus.yml \
  --storage.tsdb.path=/prometheus \
  --storage.tsdb.retention.time="${PROMETHEUS_RETENTION:-15d}" \
  --web.listen-address=:9090
