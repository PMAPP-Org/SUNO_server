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
├── infra/   # Docker Compose, Caddy, scripts de despliegue (se puebla en el Paso 2 del plan)
├── docs/    # Documentos de arquitectura (ya existentes)
└── plans/   # Plan de construcción (ya existente)
```

## Entorno local

El entorno local (Docker Compose, servicios, variables de entorno) se define en el Paso 2 del plan de construcción. Este README se actualizará con instrucciones concretas de arranque (`docker compose up`, etc.) cuando ese paso se complete.

## Convención de commits

Este repositorio no exige [Conventional Commits](https://www.conventionalcommits.org/), aunque el desarrollador puede adoptarlos si lo prefiere. La única convención obligatoria es:

- Mensajes de commit descriptivos, en modo imperativo, que expliquen el **qué** y —cuando no sea obvio— el **porqué**.
- Como mínimo, un commit por paso completado del plan de construcción (`plans/visitacampo-construccion.md`), de forma que el historial de git refleje el avance del plan.
- Los commits pueden dividirse en unidades más pequeñas dentro de un mismo paso si eso mejora la legibilidad del historial.
