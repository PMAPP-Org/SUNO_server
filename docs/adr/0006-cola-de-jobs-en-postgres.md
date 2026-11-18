# ADR-0006: Cola de trabajos en PostgreSQL con SKIP LOCKED y lease

- **Estado:** Aceptada
- **Fecha:** 2026-07-30

## Contexto

Con el push síncrono (ADR-0003) el camino crítico no necesita cola. Quedan trabajos periódicos y asíncronos de bajo volumen.

## Decisión

Tabla `job` consumida por un proceso `worker` (`manage.py run_worker`) con `SELECT … FOR UPDATE SKIP LOCKED`, lease (`locked_by`, `locked_until`), heartbeat, reaper de leases vencidos, `dead_letter` tras `max_attempts`, unicidad por restricción parcial y salida redactada (`scrub`). Los periódicos usan la misma cola: sin Celery, Redis, cron externo ni docker-in-docker.

## Consecuencias

- `docker compose up` levanta todo; los jobs se ven y se reintentan desde el Admin.
- Prueba obligatoria de `kill -9` al worker (`server/suno/jobs/tests/test_kill9.py`).
- Periódicos actuales: `queue.reap`, `sync.purge_tombstones`, `attachments.gc_unconfirmed_uploads`, `security.purge_logs`.

## Referencias

- [Doc 08 §5, ADR-006](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
- `server/suno/jobs/queue.py`, `server/suno/jobs/periodic.py`.
