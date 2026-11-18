# ADR-0034: Inventario como kárdex de movimientos más saldos guardados con checks en la BD

- **Estado:** Aceptada. Implementada en el PR #84 (pieza 6c), pendiente de integrar.
- **Fecha:** 2026-11-15

## Contexto

Calcular el saldo sumando todos los movimientos en cada consulta escala mal, y guardar solo el saldo pierde la historia que pide un kárdex.

## Decisión

- Un registro único de movimientos (`StockMovement`) con secuencia creciente por (bodega, material), un solo documento de origen y signo coherente con su tipo.
- Saldos guardados (`StockBalance`, único por bodega y material) que se actualizan **en la misma transacción** que el movimiento, con bloqueo de fila ([ADR-0035](0035-orden-de-bloqueos.md)).
- Invariantes en la BD: `stock_balance_not_negative`, `stock_balance_empty_has_no_value`, `stock_movement_sequence_uq`, `stock_movement_one_origin`, `stock_movement_sign`, `stock_movement_unit_cost_ok`.
- El saldo es siempre la suma de los movimientos de su par.

## Consecuencias

- Inventario y kárdex con consultas fijas (inventario 2, kárdex 1) y rápidas con 50 000 movimientos.
- Dos fuentes que deben cuadrar; las protege que solo `materials/kardex.py` escribe ambas.

## Referencias

- `plans/2026-11-15-costos-pieza6c-materiales-design.md` (rama de #84), «Arquitectura»; `plans/suno-progreso.md` en la misma rama.
- `server/suno/materials/kardex.py`, `server/suno/core/models/material.py` (rama de #84).
