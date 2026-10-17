# SUNO_server

Backend de VisitaCampo, la aplicación offline-first de seguimiento de visitas de campo. Contiene:
- la **API** v1 de sincronización, autenticación y adjuntos (Django 5 + Django Ninja);
- el **panel de administración** (Django Admin con MFA obligatorio);
- la **infraestructura** de despliegue: Docker Compose, Caddy, backups cifrados, monitoreo, aprovisionamiento del VPS y despliegue con rollback.

La app móvil (Flutter) está en [`PMAPP-Org/SUNO_client`](https://github.com/PMAPP-Org/SUNO_client).

## Fuente de verdad

- **Arquitectura:** [`docs/08-arquitectura-propuesta-visitacampo.md`](docs/08-arquitectura-propuesta-visitacampo.md). Ante cualquier duda de diseño, ese documento (y el resto de `docs/`) prevalece sobre supuestos ad hoc.
- **Contrato de la API:** [`server/openapi/v1.yaml`](server/openapi/v1.yaml), más los casos de paridad de formularios en `server/openapi/fixtures/`. La app usa una copia fijada en su `contract/` ([`server/openapi/README.md`](server/openapi/README.md)).
- **Plan de construcción y su seguimiento:** [`plans/`](plans/).
- **SGSI ISO 27001** de todo el sistema (app incluida): [`docs/seguridad/`](docs/seguridad/README.md).
- **Runbooks de operación:** [`docs/operacion/`](docs/operacion/README.md).

## Estructura

```
SUNO_server/
├── server/   # Django: API (Ninja), panel de administración, worker de la cola
│   └── openapi/  # Contrato de API v1 (OpenAPI 3.1) y fixtures compartidos con la app
├── infra/    # Docker Compose, Caddy, backups, monitoreo, aprovisionamiento y despliegue
├── docs/     # Arquitectura, SGSI (seguridad/) y runbooks (operacion/)
└── plans/    # Plan de construcción y seguimiento
```

## Comandos de verificación

| Qué verifica | Comando |
|---|---|
| Sintaxis del contrato de API (OpenAPI 3.1) | `python -m openapi_spec_validator server/openapi/v1.yaml` |
| YAML de Docker Compose válido (base + override dev) | `docker compose -f infra/docker-compose.yml -f infra/docker-compose.override.dev.yml config` |
| `.env` completo respecto a `.env.example` | `bash infra/scripts/check_envvars.sh` |
| Pipeline de CI del backend en local | `bash infra/scripts/ci_local.sh` |

## Entorno local

El entorno local (Docker Compose, servicios y variables de entorno) se define en `infra/`. Ver [`infra/README.md`](infra/README.md) para arrancarlo y para elegir los overrides con `COMPOSE_FILE`.

## CI y gates de calidad y seguridad

Cada PR pasa:
- **Backend:** ruff, mypy, pytest (cobertura ≥90 %), `check --deploy`, migraciones, Bandit y pip-audit.
- **Dinámicas:** DAST con Schemathesis y OWASP ZAP contra el stack de producción, y el simulacro de backup → borrado → restauración.
- **Monitoreo e infraestructura:** pruebas del monitoreo y, cuando cambia `infra/`, de `provision.sh` y `deploy.sh`.
- **Comunes:** gitleaks, osv-scanner, Semgrep (OWASP Top 10), CodeQL, actionlint, Dependency Review y Scorecard.

La regla completa está en [`CONTRIBUTING.md`](CONTRIBUTING.md): **ningún paso del plan está completo si su CI no pasa**. La correspondencia con OWASP Top 10:2025 e ISO 27001 está en [`docs/seguridad/owasp-top10-2025.md`](docs/seguridad/owasp-top10-2025.md).

## Releases y despliegue

release-please versiona a partir de Conventional Commits. Cada release publica la imagen `ghcr.io/<owner>/visitacampo-api` con SBOM y atestación de procedencia, y `deploy.yml` la despliega en producción si el environment `production` está configurado ([`docs/operacion/despliegue.md`](docs/operacion/despliegue.md)).

## Convención de commits

Los commits de `master` siguen [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix(sync):`, `ci:`…). De ellos salen la versión, el tag y el CHANGELOG de cada release ([`CONTRIBUTING.md`](CONTRIBUTING.md#releases-tags-y-versiones-automáticos)). Los PR se integran con *Squash and merge*, y `pr-title.yml` exige el formato en el título.

## Historia

Este repositorio salió del monorepo `PMAPP-Org/Seguimiento_Proyectos` (archivado) con el historial de `server/`, `infra/`, `docs/` y `plans/`. La app y su historial están en SUNO_client. El monorepo archivado conserva el historial conjunto hasta v0.2.0.
