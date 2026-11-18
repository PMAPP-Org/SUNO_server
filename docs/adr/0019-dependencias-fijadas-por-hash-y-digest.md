# ADR-0019: Dependencias fijadas por hash, imágenes por digest y Actions por SHA

- **Estado:** Aceptada
- **Fecha:** 2026-10-16

## Contexto

Una dependencia que cambia sin aviso rompe la reproducibilidad y abre la puerta a la cadena de suministro (OWASP A03:2025, ISO 27001 A.8.8).

## Decisión

- Python: se editan los `requirements*.txt` y se instalan los `requirements*.lock` generados con `uv pip compile --universal --generate-hashes`, con `--require-hashes` en CI y en la imagen. `infra/scripts/check_lockfiles.py` deja la CI en rojo si no coinciden.
- Imágenes base y de Compose fijadas por `@sha256`.
- GitHub Actions fijadas por SHA de commit con la versión en comentario; Dependabot actualiza ambos.
- Vulnerabilidades: pip-audit, osv-scanner y grype en CI; las aceptadas van a [`../seguridad/excepciones.md`](../seguridad/excepciones.md) con caducidad.

## Consecuencias

- Un bump de Dependabot necesita regenerar el lock (pasó en PR #15 → corregido en #18).
- Excepción conocida: las imágenes de MinIO de desarrollo no tienen digest (issue #21).

## Referencias

- Commits `bcc15c8`, `12519a0`; [`CONTRIBUTING.md`](../../CONTRIBUTING.md#dependencias-y-gates-de-seguridad-línea-base-iso-27001).
