# ADR-0029: Las cantidades ejecutadas viajan dentro del registro

- **Estado:** Aceptada
- **Fecha:** 2026-11-06

## Contexto

En tareas medidas por cantidades, el agente registra lo ejecutado por ítem (y, desde 4b, con hora, GPS y modo de ubicación por fila). Había que decidir si eran una entidad de sync propia o parte del registro.

## Decisión

- Las cantidades viajan dentro del registro: `Record.quantities` (JSONB), con un `CHECK` del trío tarea / % / cantidades. Cuentan solo cuando el registro está validado.
- Registro incremental: lo ejecutado en ese registro. Las correcciones son registros nuevos (negativos), no ediciones.
- El servidor solo rechaza por problemas estructurales (`quantities_without_task`, `item_not_in_project`, `location_outside_task`, `duplicate_client_id`); lo demás se acepta y se resalta en la revisión. La nota obligatoria la exige la app.
- Los campos por fila de 4b (`client_id`, `captured_at`, `gps`, `seen`, `location_mode`) son opcionales y aditivos: sin cambio de `SCHEMA_VERSION`.

## Consecuencias

- Una sola operación de sync por registro; la idempotencia y el CAS del registro cubren las cantidades.
- Una app anterior que edita un registro sin validar le borra las cantidades (su `update` no las lleva); por eso 4b exigió la versión actualizada.

## Referencias

- `plans/2026-11-03-costos-pieza4-matriz-design.md`, `plans/2026-11-09-costos-pieza4b-cantidades-design.md`; PR #56 y #65.
- `server/suno/sync/payloads.py`, `server/suno/matrix/`.
