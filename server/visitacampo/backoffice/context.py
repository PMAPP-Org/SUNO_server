"""El `RequestContext` de quien usa el Admin (Paso 24).

El Admin no pasa por `RequestContextAuth` (no hay JWT: es una sesión de
Django con MFA). El contexto se construye igual que en la API —usuario,
organización de origen y sus roles en ella, con una sola query— y se cachea
en la request para que una página con muchas filas no lo resuelva N veces.
"""

from __future__ import annotations

from typing import Any

from visitacampo.core.context import Principal, RequestContext, resolve_request_context

_CACHE_ATTR = "_backoffice_context"


def admin_context(request: Any) -> RequestContext:
    """Contexto del usuario del Admin, en su organización de origen."""
    cached = getattr(request, _CACHE_ATTR, None)
    if cached is None:
        user = request.user
        cached = resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))
        setattr(request, _CACHE_ATTR, cached)
    return cached


def is_platform_operator(request: Any) -> bool:
    """Superusuario: ve todas las organizaciones (ver `backoffice/__init__.py`)."""
    return bool(getattr(request.user, "is_superuser", False))
