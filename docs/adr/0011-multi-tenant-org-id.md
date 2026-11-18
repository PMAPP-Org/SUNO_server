# ADR-0011: Multi-tenant con esquema compartido y org_id desde el día 1

- **Estado:** Aceptada
- **Fecha:** 2026-07-30

## Contexto

La referencia usa herencia multi-tabla y vistas SQL de roles, origen de N+1 y de fugas de membresía. El MVP tiene una sola organización, pero habrá más.

## Decisión

`org_id NOT NULL` en toda tabla de negocio (`OrgScopedModel`). Identidad plana: `AppUser` + `Membership(org, user, role)`. Todo acceso pasa por `scoped_qs(ctx, Model)`, con un test de arquitectura que rompe la build si un servicio usa `Model.objects` sin scoping. Entre organizaciones se responde 404, no 403.

## Consecuencias

- Sumar organizaciones es insertar filas.
- Un bug de scoping sería una fuga; lo vigilan el test de arquitectura, las pruebas de aislamiento y el chequeo IDOR del DAST (`infra/ci/idor_check.py`).

## Referencias

- [Doc 08 §5, ADR-011](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
- `server/suno/core/scoping.py`, `server/suno/core/tests/test_architecture.py`. Ver [ADR-0023](0023-scoped-qs-unica-puerta-a-los-datos.md).
