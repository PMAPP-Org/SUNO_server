# ADR-0033: Costeo de materiales al promedio ponderado móvil por bodega y material

- **Estado:** Aceptada. Implementada en el PR #84 (pieza 6c, v0.20.0).
- **Fecha:** 2026-11-15

## Contexto

Los materiales comprados a bodega se vuelven costo cuando salen a obra. Hay que valorar cada salida de forma simple, auditable y sin conocer el futuro.

## Decisión

- **Promedio ponderado móvil por par (bodega, material)**, con IVA incluido como el resto del libro.
- El costo unitario de una salida es valor / cantidad del saldo y **queda fijo al registrarla**.
- Si la salida deja la cantidad en 0, se lleva exactamente el valor restante: el saldo nunca queda con valor sin cantidad.
- Sin inventario negativo.
- Devoluciones al costo de su línea de origen; traslados al promedio y sin costo; el sobrante de un conteo entra al promedio (o al costo de la última entrada si el saldo está en cero) y no es costo.
- Precisión: cantidades con 4 decimales, valores a centavos, unitario con 6.

## Consecuencias

- El costo pasa al libro por cortes de consumos periódicos que aprueba otra persona, no salida por salida.
- Una compra a bodega solo se anula si el saldo alcanza para retirarla.

## Referencias

- `plans/2026-11-15-costos-pieza6c-materiales-design.md`, «Costeo».
- `server/suno/materials/stock.py`.
