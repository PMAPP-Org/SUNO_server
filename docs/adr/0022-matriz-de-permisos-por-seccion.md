# ADR-0022: Matriz de permisos por sección, fija en código

- **Estado:** Aceptada (complementa [ADR-0013](0013-autorizacion-requestcontext-politicas-puras.md))
- **Fecha:** 2026-11-07

## Contexto

Los roles de organización (`OWNER`…`VIEWER`) no alcanzaban para separar quién gestiona equipos, quién aprueba costos o quién solo consulta presupuestos, ni para limitar a ciertos proyectos.

## Decisión

- `core/access.py` define un **catálogo cerrado**: secciones (`FIELD_RECORDS`, `PROJECTS`, `BUDGETS`, `COSTS`, `FORMS`, `EQUIPMENT`, `GIS`, `REPORTS`, `USERS_ACCESS`, `OPERATIONS`), roles por sección (Gestor, Revisor, Colaborador, Consulta, Operador de equipo, Auditor SST, Administrador de accesos) y la matriz `CAPABILITY_MATRIX` de capacidades (`read`, `create`, `update`, `delete`, `approve`, `reject`, `reverse`, `export`…).
- La matriz es **código versionado, no datos**: una combinación nueva pasa por revisión y pruebas. Lo configurable es la asignación (`SectionGrant`) con alcance de organización o de proyectos (`SectionProjectScope`).
- Valores desconocidos se niegan (útil con app y servidor en versiones distintas).
- Las políticas (`can_read_costs`, `can_update_cost`…) consultan la matriz a través del `RequestContext`. El Colaborador solo edita lo suyo.

## Consecuencias

- Una sección nueva (Costos, 6a) es una migración y un cambio de la matriz con sus pruebas (`core/tests/test_access_matrix.py`).
- No hay matriz configurable por organización; quedó fuera de alcance en 6a.

## Referencias

- Commit `103fb31` (section access); PR #75 (sección Costos y `reverse`).
- `server/suno/core/access.py`, `server/suno/core/models/access.py`, spec 6a (`plans/2026-11-12-costos-pieza6a-costo-real-design.md`, «Permisos»).
