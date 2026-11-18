# ADR-0028: Presupuesto con ítem estable, versión contractual y vigente

- **Estado:** Aceptada
- **Fecha:** 2026-11-01

## Contexto

Un presupuesto de obra cambia con otrosíes, pero la matriz, el libro de costos y los contratos deben seguir apuntando al mismo ítem. También hay que comparar lo contratado con lo vigente.

## Decisión

- Identidad estable del ítem (`BudgetItem`) y valores por versión (`BudgetItemVersion`). Todo lo demás (matriz, costos, contratos, materiales) apunta al `BudgetItem`.
- Cada presupuesto tiene una versión **contractual** fija y una **vigente**; varios presupuestos por proyecto.
- Cargos configurables por versión (nombre, %, base `direct`, `subtotal` o `line:<n>`), con plantilla AIU + IVA sobre utilidad. La aritmética vive en `budgets/totals.py` y la reutilizan contratos.
- Carga desde el Excel del usuario con mapeo de columnas guardado ([ADR-0036](0036-importaciones-en-dos-pasos.md)). Precio unitario directo; sin APU por ahora.

## Consecuencias

- Una versión nueva no rompe asignaciones; un ítem que pasa a capítulo conserva su historia y se avisa.
- Las etiquetas y descripciones se arman con el nombre vigente del ítem (ver problemas conocidos).

## Referencias

- `plans/2026-11-01-costos-pieza3-presupuesto-design.md`; PR #53.
- `server/suno/core/models/budget.py`, `server/suno/budgets/`.
