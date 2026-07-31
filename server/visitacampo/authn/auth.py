"""`JwtAuth`: la implementación concreta de `RequestContextAuth` (Paso 6 → Paso 7).

El Paso 6 dejó `RequestContextAuth` con `resolve_principal` **abstracto** a
propósito ("una implementación por defecto sería una forma de autenticarse sin
token"). Esta es su única implementación: traduce el access token JWT en un
`Principal`, y la clase base se encarga del resto —resolver los roles con una
sola query y colgar el `RequestContext` de la request antes de que corra el
cuerpo de la vista (ADR-013)—.

**Por qué el `Principal` sale del token y no de la BD**: es lo que hace que
una request autenticada cueste **una** query (la de roles) y no dos. El token
es de vida corta (15 min, ADR-008) y está firmado, así que `sub`, `org` y
`device` son afirmaciones verificadas criptográficamente; lo único que no
puede viajar en él son los roles, porque cambiarle el rol a alguien tiene que
surtir efecto sin esperar a que caduque su token.
"""

from __future__ import annotations

from typing import Any

from visitacampo.authn.tokens import decode_access_token
from visitacampo.core.context import Principal, RequestContextAuth

__all__ = ["JwtAuth"]


class JwtAuth(RequestContextAuth):
    """Autenticación por `Authorization: Bearer <jwt>` (contrato `bearerAuth`).

    En el OpenAPI que **genera** Ninja este esquema aparece como `JwtAuth`:
    Ninja lo nombra con `auth.__class__.__name__` y no ofrece forma de
    cambiarlo. No se fuerza el nombre por otras vías porque el contrato
    autoritativo es `server/openapi/v1.yaml` (Paso 1) —donde el esquema se
    llama `bearerAuth`— y el OpenAPI generado es solo la ayuda de desarrollo
    que ya documenta `config/api.py`.
    """

    def resolve_principal(self, request: Any, token: str) -> Principal | None:
        """Decodifica el token. `None` ⇒ Ninja responde 401 sin ejecutar la vista.

        Nunca lanza: `decode_access_token` ya colapsa firma inválida, algoritmo
        no permitido, expiración y claims malformados en un único `None`, de
        modo que el cliente recibe siempre el mismo 401 y no puede distinguir
        el motivo del rechazo.
        """
        claims = decode_access_token(token)
        if claims is None:
            return None
        return Principal(
            user_id=claims.user_id,
            org_id=claims.org_id,
            device_id=claims.device_id,
        )
