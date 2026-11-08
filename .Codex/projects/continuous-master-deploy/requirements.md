# Despliegue continuo de `master`

## Objetivo

Mantener producción en el último commit desplegable de `master` sin depender de que exista una release semántica.

## Requisitos

- Cada `push` a `master` construye una imagen inmutable del SHA exacto.
- La imagen se publica por digest y con atestación de procedencia verificable.
- El servidor hace checkout del mismo SHA que produjo la imagen.
- El despliegue conserva backup previo, healthcheck y rollback automático.
- Un error de configuración de producción falla de forma visible; no puede terminar en verde sin desplegar.
- La variable de imagen heredada de v0.13 y `SUNO_API_IMAGE` permiten desplegar y revertir entre versiones antiguas y nuevas.
- Las releases y tags continúan existiendo para versionado, CHANGELOG, SBOM y rollback manual.

## Criterios de éxito

- El workflow de `master` puede desplegar un SHA de 40 caracteres.
- `deploy.sh` acepta tags semánticos y SHA pertenecientes a `origin/master`.
- El flujo manual por tag sigue funcionando.
- Producción responde `200` en `/v1/readyz` después del despliegue.
