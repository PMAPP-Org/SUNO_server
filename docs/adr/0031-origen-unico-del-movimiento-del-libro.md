# ADR-0031: Cada movimiento del libro tiene como mucho un documento de origen

- **Estado:** Aceptada. La restricción `cost_entry_single_origin` está en el PR #84 (pieza 6c), pendiente de integrar.
- **Fecha:** 2026-11-14 (FK del acta, 6b); 2026-11-15 (restricción, 6c)

## Contexto

Desde 6b, el libro recibe movimientos generados por documentos: actas de contrato, y en 6c compras, cortes de consumos y ajustes de inventario. Un movimiento generado no puede editarse ni reversarse por su cuenta sin descuadrar su documento.

## Decisión

- Una **FK nullable por tipo de documento** en `CostEntry` (`certificate`; en 6c `material_purchase`, `consumption_cut`, `inventory_adjustment`), en vez de una relación genérica.
- Restricción de la BD `cost_entry_single_origin`: como mucho una de esas FK está llena.
- Los movimientos con origen no se editan ni se reversan desde el libro: se anula el documento (la última acta aprobada, la compra, el corte), que genera sus reversos.
- El libro muestra el origen con su etiqueta enlazada.

## Consecuencias

- Integridad referencial real y consultas simples por tipo de origen.
- Cada tipo nuevo de documento es una columna y una migración.

## Referencias

- Specs 6b y 6c; PR #79 y #84.
- `server/suno/core/models/cost.py` (en la rama de #84: `_at_most_one_origin`).
