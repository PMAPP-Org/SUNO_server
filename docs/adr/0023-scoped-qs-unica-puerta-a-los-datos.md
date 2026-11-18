# ADR-0023: scoped_qs como única puerta a los datos, con excepciones documentadas

- **Estado:** Aceptada (concreta [ADR-0011](0011-multi-tenant-org-id.md))
- **Fecha:** 2026-07-31

## Contexto

ADR-0011 pide que todo acceso pase por un queryset ya restringido a la organización. Algunas entidades no tienen `org_id` a propósito.

## Decisión

- `scoped_qs(ctx, Model)` devuelve `Model.objects.filter(org_id=ctx.org_id)` y lanza `NotOrgScopedError` si el modelo no es `OrgScopedModel`.
- Excepciones explícitas y con nombre, sin parámetro `ctx` cuando el aislamiento no aplica: `get_organization(ctx)`, `get_app_user(ctx)` y `blob_registry()` (`Blob` no tiene `org_id` porque es por contenido; la autorización de la descarga va por `RecordAttachment`, que sí lo tiene).
- `core/tests/test_architecture.py` falla si un módulo de servicios usa `Model.objects` directamente. Incluso los `bulk_create` pasan por `scoped_qs` (corrección `a1fcc11` en equipos).

## Consecuencias

- La regla se revisa en el checklist del PR («el acceso pasa por `scoped_qs`»).
- `scoping.py` está bajo CODEOWNERS.

## Referencias

- Paso 6 (`77c83ad`); Paso 17 para `blob_registry()`.
- `server/suno/core/scoping.py`, `.github/CODEOWNERS`, `.github/PULL_REQUEST_TEMPLATE.md`.
