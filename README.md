# VisitaCampo

VisitaCampo es una aplicación offline-first de seguimiento de visitas de campo, compuesta por un cliente móvil Flutter y un backend Django.

## Fuente de verdad

La arquitectura propuesta para este proyecto está documentada en [`docs/08-arquitectura-propuesta-visitacampo.md`](docs/08-arquitectura-propuesta-visitacampo.md). Ante cualquier duda de diseño o implementación, ese documento (y el resto de `docs/`) prevalece sobre supuestos ad-hoc.

El plan de construcción paso a paso vive en [`plans/visitacampo-construccion.md`](plans/visitacampo-construccion.md).

## Estructura del monorepo

```
VisitaCampo/
├── app/     # Cliente Flutter (se inicializa en el Paso 10 del plan)
├── server/  # Backend Django (se inicializa en el Paso 4 del plan)
│   └── openapi/  # Contrato de API v1 (OpenAPI 3.1) — fuente de verdad de ambos extremos
├── infra/   # Docker Compose, Caddy, scripts de despliegue — ver infra/README.md (Paso 2)
├── docs/    # Documentos de arquitectura (ya existentes)
└── plans/   # Plan de construcción (ya existente)
```

## Comandos de verificación

Conjunto acumulativo; cada paso del plan añade los suyos.

| Qué verifica | Comando |
|---|---|
| Sintaxis del contrato de API (OpenAPI 3.1) | `python -m openapi_spec_validator server/openapi/v1.yaml` |
| YAML de Docker Compose válido (base + override dev) | `docker compose -f infra/docker-compose.yml -f infra/docker-compose.override.dev.yml config` |
| `.env` completo respecto a `.env.example` | `bash infra/scripts/check_envvars.sh` |
| Pipeline de CI completo (backend + mobile + gates), local o vía GitHub Actions | `bash infra/scripts/ci_local.sh` (local) / `.github/workflows/backend.yml` + `mobile.yml` (Actions) |
| Lint anti-TLS-bypass (RD-01, ADR-009) | `bash infra/scripts/lint_no_tls_bypass.sh` |

Requiere `python -m pip install openapi-spec-validator` (se integrará en las dependencias de CI
en el Paso 4). Detalles del contrato y su estrategia de generación de código en
[`server/openapi/README.md`](server/openapi/README.md).

## Entorno local

El entorno local (Docker Compose, servicios, variables de entorno) se define en `infra/` — ver
[`infra/README.md`](infra/README.md) para instrucciones de arranque, la selección de overrides vía
`COMPOSE_FILE` y el estado de los servicios `api`/`worker` (placeholders hasta los Pasos 4 y 19).

## CI y gates de calidad/seguridad

Desde el Paso 3 existe un gate de CI (`.github/workflows/backend.yml`,
`.github/workflows/mobile.yml`) que incluye, entre otros, el lint anti-TLS-bypass
**RD-01** (ADR-009). Ver [`CONTRIBUTING.md`](CONTRIBUTING.md) para la regla
completa: **ningún paso posterior de este plan se considera completo si su
propio job de CI no pasa**, y cómo ejecutar el pipeline localmente
(`bash infra/scripts/ci_local.sh`) mientras el repositorio no tenga remoto en
GitHub.

## Convención de commits

Los commits que llegan a `master` siguen [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix(server):`, `ci:`…): de ellos salen la versión, el tag y el CHANGELOG de cada release (ver [CONTRIBUTING.md](CONTRIBUTING.md#releases-tags-y-versiones-automáticos)). Los PR se integran con *Squash and merge* y `pr-title.yml` exige el formato en el título. Además:

- Mensajes de commit descriptivos, en modo imperativo, que expliquen el **qué** y —cuando no sea obvio— el **porqué**.
- Como mínimo, un commit por paso completado del plan de construcción (`plans/visitacampo-construccion.md`), de forma que el historial de git refleje el avance del plan.
- Los commits pueden dividirse en unidades más pequeñas dentro de un mismo paso si eso mejora la legibilidad del historial.
