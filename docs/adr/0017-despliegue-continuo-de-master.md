# ADR-0017: Despliegue continuo de master por digest

- **Estado:** Aceptada (reemplaza a [ADR-0016](0016-despliegue-al-publicar-release.md))
- **Fecha:** 2026-11-08

## Contexto

Con el despliegue atado a la release, producción iba detrás de `master` y cada corrección esperaba un PR de release. Se quería que lo integrado estuviera en producción.

## Decisión

Separar despliegue y release:

- `deploy-master.yml`: cada push a `master` construye `suno-api:master-<SHA>`, la publica en GHCR con procedencia firmada y llama a `deploy.yml` con el digest.
- `deploy.yml` verifica la atestación, obtiene `deploy.sh` del mismo SHA, comprueba que el SHA pertenece a `origin/master`, toma backup, levanta con `compose up --wait`, comprueba `/v1/readyz` y hace rollback si algo falla. Si faltan los secretos, falla en vez de informar éxito.
- `release.yml` sigue creando tags, CHANGELOG, SBOM e imágenes semánticas; los tags sirven para rollback manual.

## Consecuencias

- Todo lo que entra a `master` llega a producción en minutos: un PR roto es un incidente, de ahí la regla de CI en verde y la de migraciones aditivas ([ADR-0025](0025-solo-migraciones-aditivas-con-produccion.md)).
- `concurrency` evita dos despliegues a la vez.

## Referencias

- Commit `e2e507e`, plan [`plans/2026-11-08-continuous-master-deploy-plan.md`](../../plans/2026-11-08-continuous-master-deploy-plan.md).
- `.github/workflows/deploy-master.yml`, [`../operacion/despliegue.md`](../operacion/despliegue.md).
