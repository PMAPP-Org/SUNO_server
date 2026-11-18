# ADR-0013: Autorización con políticas puras sobre un RequestContext

- **Estado:** Aceptada
- **Fecha:** 2026-07-30

## Contexto

En la referencia las funciones `can_*` eran buenas, pero se invocaban dentro del bucle de deltas (N+1) y una devolvía `True` sin comprobar nada.

## Decisión

Funciones `can_x(ctx, recurso) -> bool` puras, sin I/O, en un módulo único (`core/policies.py`). Un `RequestContext` inmutable se resuelve una vez por request. Deny-by-default: la API exige `auth` salvo excepciones explícitas. Presupuesto de consultas constante con `assertNumQueries` en un push de 200 operaciones. 404 para recursos de otra organización.

## Consecuencias

- Autorización probada sin BD y push de costo constante.
- La matriz de capacidades por sección ([ADR-0022](0022-matriz-de-permisos-por-seccion.md)) se resuelve en el mismo `RequestContext`.

## Referencias

- [Doc 08 §5, ADR-013](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
- `server/suno/core/context.py`, `server/suno/core/policies.py`. Paso 6 (`77c83ad`).
