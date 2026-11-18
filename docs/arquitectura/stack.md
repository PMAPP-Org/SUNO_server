# Stack del servidor

Versiones tomadas de los lockfiles (`server/requirements*.lock`), `server/Dockerfile`, `infra/docker-compose*.yml` y `.github/workflows/` en `master` (v0.19.0). **El inventario exacto y completo** (dependencias transitivas incluidas) es el SBOM CycloneDX/SPDX que genera la CI: `backend.yml` lo deja como artefacto en cada PR y `release.yml` lo publica con cada release, junto con la imagen.

La app móvil tiene su propio `stack.md` en `docs/arquitectura/` de SUNO_client.

## Lenguaje, framework y base de datos

| Componente | Versión | Para qué | Por qué |
|---|---|---|---|
| Python | 3.12 (`python:3.12-slim-bookworm`, por digest) | Lenguaje del servidor. | [ADR-0004](../adr/0004-backend-django-ninja.md) |
| Django | 5.2.19 (LTS) | ORM, migraciones, Admin, vistas web, sesiones. | [ADR-0004](../adr/0004-backend-django-ninja.md) |
| Django Ninja | 1.7.3 | API v1 con esquemas Pydantic y OpenAPI. Sin DRF. | [ADR-0004](../adr/0004-backend-django-ninja.md) |
| Pydantic | 2.14.1 | Validación del contrato (`extra="forbid"`, RD-03). | [ADR-0004](../adr/0004-backend-django-ninja.md) |
| PostgreSQL | 16 (imagen oficial, por digest; sin PostGIS) | Datos, cola de jobs, log de sync, auditoría. | [ADR-0005](../adr/0005-postgresql-sin-postgis.md), [ADR-0006](../adr/0006-cola-de-jobs-en-postgres.md) |
| psycopg | 3.3.8 (binary) | Driver de PostgreSQL. | |
| gunicorn + uvicorn + uvicorn-worker | 23.0.0 / 0.54.0 / 0.4.0 | Servidor de aplicación (ASGI, 3 workers). | `server/requirements-runtime.txt` |

## Librerías clave

| Librería | Versión | Para qué | Por qué / dónde se decidió |
|---|---|---|---|
| argon2-cffi | 25.1.0 | Hash de contraseñas Argon2id. | [ADR-0008](../adr/0008-autenticacion-jwt-y-refresh-hasheado.md) |
| PyJWT + cryptography | 2.15.1 / 50.0.2 | Access token JWT EdDSA (Ed25519). | [ADR-0008](../adr/0008-autenticacion-jwt-y-refresh-hasheado.md); comparación de librerías en `suno/authn/tokens.py` |
| django-axes | 7.1.0 | Bloqueo por intentos de login. | [ADR-0008](../adr/0008-autenticacion-jwt-y-refresh-hasheado.md) |
| django-otp | 1.7.5 | MFA TOTP y códigos de respaldo en el Admin. | Riesgo R-20, ISO 27001 A.8.5 (`docs/seguridad/`) |
| boto3 | 1.43.124 | Firma de URLs S3 (sin red) y `HEAD`/`put` contra R2. | [ADR-0007](../adr/0007-adjuntos-s3-por-contenido.md); motivo en `requirements.txt` |
| whitenoise | 6.12.0 | Estáticos servidos por el propio proceso de Django. | Comentario en `requirements.txt` |
| openpyxl + defusedxml | 3.1.5 / 0.7.1 | Lectura y escritura de `.xlsx` (presupuestos, matriz, costos, exportaciones) y XML seguro (MSPDI, XLSForm). | Planes de C5/C6 y piezas de costos |
| reportlab | 5.1.0 | PDF del registro, del acta y del corte de consumos. | Plan C5/C6 |
| segno | 1.6.6 | QR de equipos en SVG, sin dependencias. | `requirements.txt` |

## Infraestructura

| Componente | Versión | Para qué | Por qué |
|---|---|---|---|
| Docker Compose | — | Orquestación en un VPS. | [ADR-0010](../adr/0010-hosting-vps-docker-compose-caddy.md) |
| Caddy | 2 (alpine, por digest) | Borde TLS, HSTS, cabeceras, IP permitidas del Admin. | [ADR-0010](../adr/0010-hosting-vps-docker-compose-caddy.md), [ADR-0009](../adr/0009-tls-estricto-y-pinning.md) |
| Cloudflare R2 (S3) | — | Blobs, mapas base, capas, soportes, backups. | [ADR-0007](../adr/0007-adjuntos-s3-por-contenido.md) |
| age + rclone | Paquetes de Debian sobre `postgres:16` | Backups cifrados con llave pública y subida a S3. | `infra/backup/README.md` |
| Prometheus / Alertmanager / Grafana | v3.15.1 / v0.34.2 / 13.3.0 | Monitoreo opcional. | [ADR-0014](../adr/0014-monitoreo-prometheus-grafana.md) |
| node-, postgres-, blackbox-exporter | v1.12.1 / v0.20.1 / v0.29.1 | Métricas del host, de la BD y sondas HTTPS. | [ADR-0014](../adr/0014-monitoreo-prometheus-grafana.md) |
| MinIO, Mailpit | `latest` sin digest / v1.31.6 | Solo desarrollo: S3 y correo locales. | Issue #21 (MinIO dejó de publicar imágenes) |

## CI, calidad y seguridad

| Herramienta | Versión | Para qué |
|---|---|---|
| GitHub Actions | Acciones fijadas por SHA | CI, release, despliegue ([ADR-0019](../adr/0019-dependencias-fijadas-por-hash-y-digest.md)). |
| release-please | action v5.0.0 | Versiones, tags y CHANGELOG ([ADR-0015](../adr/0015-release-please-y-squash-merge.md)). |
| ruff / mypy / pytest | 0.17.3 / 2.4.1 / 9.2.0 | Lint, tipos y pruebas (cobertura ≥ 90 %, contra PostgreSQL real). |
| bandit / pip-audit / Semgrep | 1.9.4 / 2.10.1 / 1.183.0 | SAST y CVE en lockfiles. |
| CodeQL | action v4.39.1 | Análisis estático (Python y workflows). |
| Schemathesis / OWASP ZAP | 4.33.1 / imagen por digest | DAST contra el stack de producción (`dast.yml`). |
| gitleaks / osv-scanner / grype | 8.30.1 / 2.7.0 / 0.120.1 | Secretos en el historial y CVE en dependencias e imagen. |
| syft | 1.54.1 | SBOM CycloneDX y SPDX. |
| actionlint / hadolint / zizmor | 1.7.12 / 2.15.1 / — | Lint de workflows y Dockerfile. |
| promtool | (imagen de Prometheus) | Pruebas de reglas de alerta (`monitoring.yml`). |
