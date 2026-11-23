# ADR-0035: Orden de bloqueos para serializar sin interbloqueos

- **Estado:** Aceptada
- **Fecha:** 2026-10-31

## Contexto

Varias operaciones concurrentes tocan las mismas filas: push de varios teléfonos, revisión de registros desde la web, salidas de bodega simultáneas. RD-14 pide un solo mecanismo por invariante.

## Decisión

- **Push:** `pg_advisory_xact_lock` por organización al inicio de la transacción; bajo ese lock se asigna `change_seq`.
- **Revisión de registros:** `review_record` toma el **mismo** candado de organización que el push, y el push bloquea con `select_for_update` las filas validadas del lote. Así una revisión concurrente no se cuela.
- **Inventario (6c):** las filas de `StockBalance` se bloquean con `select_for_update` **en orden de (bodega, material)**; los pares que no existen se crean antes con `ON CONFLICT DO NOTHING`.
- **Importación del catálogo de materiales (6c):** bloquea en orden de `pk`.
- **Libro (6a):** el soporte se sube antes de bloquear la fila del movimiento, para no sostener el lock durante I/O.
- **Mano de obra (6d):** guardar o importar tareo bloquea proyecto, planilla y las filas de los trabajadores en orden de `pk` **antes** de validar asignaciones y retiro; aprobar agrega después las filas del factor. Crear, editar o borrar una asignación bloquea proyecto, trabajador y asignación. Retirar bloquea el trabajador y luego escribe sus asignaciones. Así un retiro y un guardado o una asignación concurrentes se serializan por la fila del trabajador (revisión I5, 2026-11-19).
- Unicidad que depende de carreras (número de contrato, `client_id`): restricción única en la BD; el choque se traduce al mensaje normal.

## Consecuencias

- Ninguna pareja de operaciones toma los mismos locks en orden distinto.
- El lock por organización serializa los push de una org; es el riesgo que mide la prueba de carga.

## Referencias

- `server/suno/sync/changeseq.py`; `plans/suno-progreso.md` (etapa 1, «Correcciones de la revisión» (c); pieza 6a; pieza 6c).
- `server/suno/backoffice/services.py`, `server/suno/materials/kardex.py`.
