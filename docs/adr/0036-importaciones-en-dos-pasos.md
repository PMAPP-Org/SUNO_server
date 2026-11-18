# ADR-0036: Importaciones en dos pasos con vista previa

- **Estado:** Aceptada
- **Fecha:** 2026-10-31

## Contexto

La oficina carga datos en lote desde archivos: cronograma de MS Project, presupuestos, matriz, costos y catálogo de materiales. Un archivo malo no debe dejar datos a medias, y lo que se aplica debe ser lo que se vio.

## Decisión

- Toda importación tiene **vista previa** (con diferencias, avisos y errores por fila) y un paso de **aplicar** en una transacción con bloqueo.
- Cronograma, presupuesto y matriz guardan la importación pendiente en la BD y detectan una **vista previa obsoleta** (los datos cambiaron desde que se generó).
- Libro de costos y catálogo de materiales no usan tabla de preparación: las filas validadas viajan **firmadas** en el formulario (una hora), con un **nonce de un solo uso** (`CostImportToken`, `MaterialImportToken`), y se validan otra vez al aplicar.
- Lectura segura de `.xlsx` y XML: límites de tamaño, filas y columnas, sin macros, `defusedxml`.
- La importación corre en la misma request, sin la cola de jobs.

## Consecuencias

- Nada se aplica sin que alguien lo haya visto; un doble envío no duplica.
- Archivos muy grandes ocupan un worker web; los límites lo acotan (20 MB y 20 000 filas en presupuestos; 2000 filas en costos).

## Referencias

- Specs de etapa 1, piezas 3, 4a, 6a y 6c; `plans/suno-progreso.md` (6a, «Revisión de código»).
- `server/suno/projects/importer.py`, `budgets/importer.py`, `matrix/importer.py`, `costs/importer.py`.
