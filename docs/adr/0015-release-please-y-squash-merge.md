# ADR-0015: Versionado con release-please, Conventional Commits y Squash and merge

- **Estado:** Aceptada
- **Fecha:** 2026-10-16

## Contexto

Las versiones, tags y notas de release se hacían a mano o no se hacían. ISO 27001 pide trazabilidad de cambios (A.8.32) y el equipo es de una o dos personas.

## Decisión

- Los commits de `master` siguen Conventional Commits (`feat:`, `fix(sync):`, `feat!:`…).
- Los PR se integran con **Squash and merge**; el título del PR es el commit, y `pr-title.yml` exige el formato.
- release-please (`release.yml`) mantiene un PR `chore: release X.Y.Z` con el CHANGELOG y la versión (`version.txt`, `server/pyproject.toml`). Al integrarlo crea el tag, la GitHub Release, la imagen semántica en GHCR y el SBOM con atestación.
- SUNO_server y SUNO_client se versionan por separado con la misma regla.

## Consecuencias

- `feat:` sube la menor, `fix:` el parche y `!` la mayor (antes de 1.0, la menor).
- El CHANGELOG es fuente para [`../bugs/historial.md`](../bugs/historial.md) (sección «Corregido»).
- Los commits directos a `master` sin PR también entran al CHANGELOG (pasó el 2026-11-08); la protección de rama sigue pendiente de un Admin (issue #7).

## Referencias

- Commit `6fc009a` (ci: automate versioning, tags and signed, attested releases).
- `.github/workflows/release.yml`, `.github/workflows/pr-title.yml`, `release-please-config.json`, [`CONTRIBUTING.md`](../../CONTRIBUTING.md#releases-tags-y-versiones-automáticos).
