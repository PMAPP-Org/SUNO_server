# ADR-0025: Con producción, las migraciones publicadas no se reescriben

- **Estado:** Aceptada (reemplaza a [ADR-0024](0024-renombre-reescribiendo-migraciones.md))
- **Fecha:** 2026-11-09

## Contexto

Desde el 2026-11-08 cada commit de `master` se despliega ([ADR-0017](0017-despliegue-continuo-de-master.md)). El PR #60 aún reescribía las migraciones 0001–0014.

## Decisión

- Una migración que ya llegó a `master` no se edita ni se renombra. Los cambios van en una migración nueva, aditiva y reversible.
- El renombre `Visit` → `Record` va en `0027_rename_visit_to_record.py`, probada con datos reales en `core/tests/test_rename_visit_migration.py`.
- Se mantiene el contrato `SCHEMA_VERSION = 2` sin convivencia: la app con esquema 1 deja de sincronizar.
- Cada pieza comprueba la migración hacia atrás y de vuelta (0028, 0029 y 0030 lo registran en `plans/suno-progreso.md`).

## Consecuencias

- Los nombres viejos sobreviven en las migraciones históricas; la guardia de nombres tiene una lista blanca acotada para ellas.
- El checklist del PR pide que las migraciones se puedan aplicar con la versión anterior aún corriendo.

## Referencias

- PR #60 (`390d7ed`); `plans/suno-progreso.md` (nota del 2026-11-09).
- `server/suno/core/migrations/0027_rename_visit_to_record.py`, `.github/PULL_REQUEST_TEMPLATE.md`.
