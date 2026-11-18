# ADR-0026: El control se calcula al pedirlo, con cálculo puro y consultas fijas

- **Estado:** Aceptada
- **Fecha:** 2026-11-11

## Contexto

El control (PV, EV, SPI y, desde 6a, AC, CPI, EAC) depende de cronograma, presupuesto vigente, matriz, registros validados y libro de costos, que cambian a cualquier hora. Guardar cortes obliga a recalcularlos o invalidarlos.

## Decisión

- Sin cortes guardados: la página Control y su Excel se calculan en cada request para la fecha de corte pedida.
- Un **cargador** (`control/loader.py`) trae todo con un número fijo de consultas (9 sin costos; 16 con costos, 14 sin presupuesto aplicado), siempre con `scoped_qs`.
- El **cálculo es puro**, sin BD: `earned_value.py`, `actual_cost.py`, `indices.py`, `periods.py`.
- Pruebas de carga con objetivo de 1 s (5000 pares; 20 000 movimientos en 6a). En GitHub Actions el umbral es 2 s (`CI=true`, PR #78).

## Consecuencias

- Siempre coherente con los datos; nada que invalidar.
- El costo crece con el proyecto; está medido (0,25 s en la pieza 5; 0,436 s con costos).
- Si un proyecto real supera el objetivo, la salida es guardar agregados, no cambiar el cálculo.

## Referencias

- Specs de las piezas 5 y 6a («Persistencia del cálculo: al momento»); `plans/suno-progreso.md`.
- `server/suno/control/`. PR #67 y #75.
