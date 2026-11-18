# ADR-0003: Sync con outbox, CAS por versión y aplicación síncrona

- **Estado:** Aceptada
- **Fecha:** 2026-07-30

## Contexto

QFieldCloud encola la aplicación de cada delta porque necesita QGIS. En SUNO aplicar un cambio es un UPSERT en Postgres, así que la cola, el polling y la re-descarga completa tras un conflicto sobran.

## Decisión

1. Outbox local con `op_uuid` UUIDv7 generado en el dispositivo.
2. `POST /v1/sync/push` aplica todo en una transacción y responde el resultado por operación en la misma request.
3. Concurrencia optimista: cada operación lleva `base_version`; si no coincide, `conflict` con la fila autoritativa.
4. Servidor autoritativo, sin merge de tres vías; la versión del agente va a cuarentena visible y nunca se descarta en silencio.
5. Pull incremental por cursor `change_seq`, asignado bajo `pg_advisory_xact_lock` por organización.
6. Carril de adjuntos independiente.
7. Contrato versionado, validado con Pydantic v2 en el servidor y parsers tipados en la app.

## Consecuencias

- Confirmación inmediata; menos código; un log de request contiene el ciclo entero.
- El lote se acota (500 operaciones o 5 MiB) y el lock serializa los push de una organización. La prueba de carga semanal mide ese riesgo (S-05) y hoy no cumple el objetivo: ver [`../bugs/problemas-conocidos.md`](../bugs/problemas-conocidos.md).
- El contrato tiene los estados `applied`, `conflict` y `rejected`; el `duplicate` de doc 08 §4.2 no existe (un `op_uuid` repetido devuelve su resultado guardado).

## Referencias

- [Doc 08 §4.2 y §5, ADR-003](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
- `server/suno/sync/services.py`, `server/suno/sync/changeseq.py`, `server/openapi/v1.yaml`.
- Pasos 8 y 9 (commits `13a372f`, `0a8a4d4`).
