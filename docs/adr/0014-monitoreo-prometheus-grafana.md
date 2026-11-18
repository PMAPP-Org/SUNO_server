# ADR-0014: Monitoreo con Prometheus, Alertmanager y Grafana opcionales, y vigilancia externa

- **Estado:** Aceptada (enmienda a ADR-0010)
- **Fecha:** 2026-10-17

## Contexto

Un uptime externo y Sentry detectan que la API no responde, pero no avisan de disco lleno, certificado sin renovar, backups detenidos o Postgres saturado. ISO 27001 A.8.16 pide evidencia de monitoreo.

## Decisión

- Capa interna opcional (`infra/docker-compose.monitoring.yml`): Prometheus, Alertmanager (correo por SMTP), node-, blackbox- y postgres-exporter y Grafana provisionado, escuchando solo en `127.0.0.1`.
- Capa externa: `.github/workflows/uptime.yml` cada 15 minutos; abre y cierra un issue `incidente`.
- Reglas con pruebas (`promtool test rules`) y una prueba de integración que detiene la API y exige la alerta.

## Consecuencias

- El VPS mínimo pasa a 2 GB si se activa el monitoreo.
- La salud se infiere por sondas y métricas, sin cAdvisor (evita montar el socket de Docker).

## Referencias

- [Doc 08 §5, ADR-014](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
- `infra/monitoring/`, `.github/workflows/monitoring.yml`, `.github/workflows/uptime.yml`. Commit `f8b17d5`.
