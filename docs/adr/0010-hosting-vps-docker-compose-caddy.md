# ADR-0010: Un VPS con Docker Compose y Caddy; R2 para blobs y backups

- **Estado:** Aceptada. Enmendada por [ADR-0014](0014-monitoreo-prometheus-grafana.md) (monitoreo) y, en el despliegue, por [ADR-0016](0016-despliegue-al-publicar-release.md), a su vez reemplazada por [ADR-0017](0017-despliegue-continuo-de-master.md).
- **Fecha:** 2026-07-30

## Contexto

Una persona opera todo, la disponibilidad objetivo es 99 % y la app absorbe caídas porque trabaja sin conexión.

## Decisión

Un VPS pequeño con Docker Compose: `caddy`, `api`, `worker`, `db`. Blobs y backups en Cloudflare R2. Un `docker-compose.yml` base más overrides por `COMPOSE_FILE`; `.env.example` exhaustivo comprobado en CI; `pg_dump` cifrado y restauración probada; sin memcached ni cron externo.

## Consecuencias

- Costo bajo y sin lock-in; un único punto de fallo, mitigado por la app offline y por tener blobs y backups fuera del VPS.
- Disparador de migración: p95 de `/sync/push` por encima de 2 s de forma sostenida, o más de 4 h/mes de operación.
- **Diferencias con el código:** hay un quinto servicio `backup` (pg_dump + age + rclone); los overrides reales son `override.dev`, `prod` y `monitoring` (no `selfhosted`/`managed`); Sentry no está integrado.

## Referencias

- [Doc 08 §5, ADR-010](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
- `infra/`, [`../operacion/despliegue.md`](../operacion/despliegue.md).
