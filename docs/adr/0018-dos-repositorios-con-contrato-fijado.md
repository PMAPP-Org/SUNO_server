# ADR-0018: Dos repositorios, servidor y app, unidos por un contrato fijado

- **Estado:** Aceptada
- **Fecha:** 2026-10-17

## Contexto

El monorepo `Seguimiento_Proyectos` mezclaba la CI de Flutter y la de Django: cada PR corría ambas y los releases no podían separarse.

## Decisión

Separar en `PMAPP-Org/SUNO_server` (`server/`, `infra/`, `docs/`, `plans/`) y `PMAPP-Org/SUNO_client` (la app en la raíz), cada uno con su historial, su CI y su release-please. El contrato vive en `server/openapi/v1.yaml` y `fixtures/`; la app guarda una copia fijada en `contract/` (con el SHA de origen) y `contract.yml` comprueba su integridad. La compatibilidad en ejecución la negocian `schema_version` y `/v1/server/info`.

## Consecuencias

- Un cambio de contrato son dos PR: el del servidor y el que actualiza `contract/` en la app.
- La arquitectura transversal, el SGSI y los planes viven en SUNO_server.
- El monorepo quedó archivado con el historial conjunto hasta v0.2.0.

## Referencias

- Commits `190dc2f` (servidor) y `3810cac` (app); `df4b0dc` (contrato fijado).
- [`server/openapi/README.md`](../../server/openapi/README.md), `plans/suno-progreso.md` («Estado al 2026-10-17»).
