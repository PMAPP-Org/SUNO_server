# ADR-0004: Backend en Python 3.12, Django 5 y Django Ninja

- **Estado:** Aceptada
- **Fecha:** 2026-07-30

## Contexto

Un desarrollador, presupuesto limitado y la necesidad de API de sync, validación estricta, ORM con migraciones, autenticación, cola y un back-office desde el primer día.

## Decisión

Django 5.x LTS + Django Ninja + Pydantic v2 + PostgreSQL, servido por gunicorn con workers uvicorn. Django Admin como back-office de la fase 1. Sin DRF. Regla de capas: las vistas parsean, llaman a un servicio y serializan.

## Consecuencias

- Admin, migraciones y test runner de serie; `check --deploy` en CI.
- Ninja es más pequeño que DRF; se usa de forma conservadora y la lógica vive en servicios.
- **Evolución:** el back-office ya no es solo el Admin; hay una plataforma web propia ([ADR-0020](0020-plataforma-web-con-vistas-django.md)).

## Referencias

- [Doc 08 §5, ADR-004](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
- `server/requirements.txt`, `server/config/api.py`. Paso 4 (`0c19941`).
