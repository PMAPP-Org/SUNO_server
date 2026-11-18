# ADR-0038: Un registro validado no cambia por el push

- **Estado:** Aceptada
- **Fecha:** 2026-10-31

## Contexto

El avance validado alimenta el % del cronograma, el EV y, desde 6b, la cantidad que se paga en las actas. Si el teléfono pudiera cambiar un registro ya validado, el dinero cambiaría sin una nueva revisión.

## Decisión

- Si la última revisión de un registro es `approved`, el push no puede cambiar su tarea, su %, sus cantidades ni borrarlo: responde `rejected` con `record_validated`.
- Una operación con `base_version` obsoleto contesta `conflict` antes que `record_validated`.
- La revisión vigente se decide de forma determinista (`-created_at, -id`).
- La app sabe que un registro está validado por ese rechazo y solo deja descartar la edición.

## Consecuencias

- Para corregir un registro validado hace falta una nueva revisión o un registro de corrección (en cantidades, uno negativo).

## Referencias

- `plans/suno-progreso.md` (etapa 1, «Correcciones de la revisión» (a), (b), (e) y decisión 15); PR #46; renombrado de `visit_validated` a `record_validated` en PR #60.
- `server/suno/sync/services.py`, `server/suno/backoffice/services.py`.
