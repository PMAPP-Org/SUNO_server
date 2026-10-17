#!/bin/sh
# Genera alertmanager.yml: con ALERT_EMAIL_TO, avisos por correo usando el SMTP
# de la app; sin él, sin receptor (las alertas solo se ven en la UI).
set -eu
if [ -n "${ALERT_EMAIL_TO:-}" ]; then
  umask 077
  printf '%s' "${EMAIL_HOST_PASSWORD:-}" > /tmp/alertmanager-smtp-password
  case "${EMAIL_USE_TLS:-true}" in
    true | True | TRUE | 1 | yes) tls=true ;;
    *) tls=false ;;
  esac
  sed -e "s|__SMTP_SMARTHOST__|${EMAIL_HOST}:${EMAIL_PORT}|" \
    -e "s|__ALERT_EMAIL_FROM__|${ALERT_EMAIL_FROM}|" \
    -e "s|__SMTP_USERNAME__|${EMAIL_HOST_USER:-}|" \
    -e "s|__SMTP_REQUIRE_TLS__|${tls}|" \
    -e "s|__ALERT_EMAIL_TO__|${ALERT_EMAIL_TO}|" \
    /etc/alertmanager/alertmanager.email.yml.tmpl > /alertmanager/alertmanager.yml
else
  echo "alertmanager: ALERT_EMAIL_TO vacío, las alertas no se envían a nadie" >&2
  cp /etc/alertmanager/alertmanager.null.yml /alertmanager/alertmanager.yml
fi
exec /bin/alertmanager --config.file=/alertmanager/alertmanager.yml --storage.path=/alertmanager
