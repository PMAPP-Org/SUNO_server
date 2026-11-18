# ADR-0016: Desplegar a producción al publicar una release

- **Estado:** Reemplazada por [ADR-0017](0017-despliegue-continuo-de-master.md)
- **Fecha:** 2026-10-17

## Contexto

ADR-0010 preveía `ssh` + `docker compose pull && up -d`. Con release-please (ADR-0015) cada release ya produce una imagen versionada con procedencia.

## Decisión

`release.yml` llama a `deploy.yml` (workflow reutilizable, environment `production`) al crear un tag `vX.Y.Z`. `deploy.yml` verifica la atestación de la imagen, entra por SSH y ejecuta `infra/scripts/deploy.sh`, que toma un backup previo, levanta la versión y hace rollback automático si `/v1/readyz` falla.

## Consecuencias

- Producción solo cambiaba al integrar el PR de release.
- El paso de secretos al workflow reutilizable falló en v0.3.0 y v0.4.0 (faltaba `secrets: inherit`), corregido en PR #18.
- Producción quedaba detrás de `master` entre releases; por eso se reemplazó.

## Referencias

- Commit `3878274` (automated deploy with rollback), PR #18 (`44b9278`).
- `infra/scripts/deploy.sh`, `.github/workflows/deploy.yml`.
