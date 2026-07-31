"""Las consultas que ocurren **antes** de que exista un `RequestContext`.

Es el único módulo de `authn` que toca un manager de Django directamente, y
está en la lista de exenciones de `core/tests/test_architecture.py` por la
misma razón que `core/context.py`: `scoped_qs(ctx, Model)` necesita un `ctx`,
y el `ctx` se construye *a partir* del resultado de estas consultas. No se
puede filtrar por una organización que todavía no se conoce.

El módulo se mantiene **diminuto** a propósito. Todo lo que ocurre después de
identificar al usuario —crear el dispositivo, emitir el token, revocar la
familia en el logout— ya tiene contexto y va por `scoped_qs` (ver
`services.py`). Si este archivo crece, es señal de que alguien está haciendo
consultas sin aislamiento que sí podría hacer con él.

**Por qué el login no aparece aquí**: buscar el usuario por `username` sería
otra consulta pre-contexto, pero no hace falta escribirla —
`django.contrib.auth.authenticate()` ya la hace dentro de `ModelBackend`, y
además ejecuta un hash "dummy" cuando el usuario no existe, que es justo la
uniformidad de temporización que ADR-008 pide contra la enumeración de
usuarios. Reimplementarla aquí habría sido perder esa propiedad.
"""

from __future__ import annotations

from visitacampo.authn.models import RefreshToken

__all__ = ["find_refresh_token_by_selector"]


def find_refresh_token_by_selector(selector: str) -> RefreshToken | None:
    """Busca la fila por su `selector`, o `None`.

    Consulta sin scoping **por necesidad**: el cliente presenta un token
    opaco y la organización a la que pertenece es precisamente lo que esta
    consulta averigua. La seguridad no depende del filtro (que no puede
    existir) sino de lo que ocurre después: el `verifier` se compara en tiempo
    constante contra el hash y todo lo demás se resuelve ya con el contexto
    construido a partir de esta fila (`services.refresh_session`).

    El `selector` es único, así que esto es un acceso por índice único; y
    devolver `None` en vez de lanzar mantiene el 401 genérico como única
    respuesta posible ante un token que no existe.
    """
    return RefreshToken.objects.filter(selector=selector).first()
