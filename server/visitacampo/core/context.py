"""`RequestContext`: identidad, organización y roles resueltos **una vez** por request.

Referencias normativas:
- **ADR-013** — autorización con funciones de política puras sobre un
  `RequestContext` inmutable construido con **una sola query** antes de
  cualquier bucle.
- **RD-07** (hallazgo **H-07**, HIGH) — QFieldCloud llamaba a
  `permissions_utils.can_create_delta(user, delta)` *dentro* del loop de
  deltas; cada llamada re-consultaba la vista SQL de roles ⇒ N+1. Aquí los
  roles se resuelven **fuera** del bucle, y las políticas
  (`visitacampo.core.policies`) son evaluación en memoria: cero queries por
  recurso, cueste el lote 1 o 500 operaciones.
- **RD-16** (hallazgo **H-20**, LOW) — fetches redundantes de la entidad de
  contexto. Se resuelven una vez y se cuelgan del propio contexto
  (`resolve_entity`).

**Qué NO vive aquí**: el token. Decodificar el JWT, validar su firma y su
expiración es del Paso 7. Este módulo define la *costura*: quien sepa leer un
token produce un `Principal` (tres identificadores) y esta capa lo convierte en
un `RequestContext` con los roles ya resueltos. Así el núcleo de autorización
es testeable sin criptografía y el Paso 7 no puede "olvidarse" de resolver el
contexto: la dependencia de Ninja lo hace por él.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import UUID

from ninja.security import HttpBearer

from visitacampo.core.models import Membership

T = TypeVar("T")

# Tipo de un rol. En ejecución es siempre el `str` del valor de `Role`
# (`"OWNER"`, `"FIELD_AGENT"`…), que es lo que guarda la columna
# `membership.role`. Se declara como `Any` porque Django no publica tipos:
# mypy no reconoce `models.TextChoices` y lee `Role.OWNER` como la tupla
# `("OWNER", "Propietario")` del cuerpo de la clase, no como el `str` que
# realmente es. Tiparlo como `str` obligaría a sembrar de `# type: ignore`
# cada política — ruido que ocultaría los errores de tipo de verdad.
RoleValue = Any

# Atributo donde la dependencia deja el contexto. Se declara como constante
# (y no se escribe `request.ctx` a mano por ahí) para que renombrarlo sea un
# cambio de una línea y para que el accesor de abajo sea el único camino.
REQUEST_CONTEXT_ATTR = "ctx"


class RequestContextMissing(RuntimeError):
    """No hay `RequestContext` en la request — se deniega, no se improvisa.

    Deny-by-default (ADR-013): un accesor que devolviera `None` invitaría a
    escribir `if ctx: ...` y a que la rama sin contexto acabara siendo la que
    no comprueba nada. Aquí la ausencia de contexto es un error de
    programación ruidoso, nunca un permiso implícito.
    """


class ContextEntityCache:
    """Memoización **por request** de la entidad de contexto (RD-16 / H-20).

    Es el único componente mutable del contexto, y a propósito: lo que ADR-013
    exige inmutable es la *identidad* (usuario, organización, roles,
    dispositivo), porque de ella dependen todas las decisiones de
    autorización. Este cache no decide nada; solo evita re-consultar lo mismo
    dentro de la misma request.

    Cachea también los `None`: "esa entidad no existe" es una respuesta tan
    válida como cualquier otra y re-consultarla sería exactamente el fetch
    redundante que RD-16 elimina.
    """

    __slots__ = ("_values",)

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    def resolve(self, key: str, loader: Callable[[], T]) -> T:
        """Devuelve el valor cacheado de `key`, invocando `loader` como mucho una vez."""
        if key not in self._values:
            self._values[key] = loader()
        value: T = self._values[key]
        return value

    def is_cached(self, key: str) -> bool:
        return key in self._values


@dataclass(frozen=True, slots=True)
class Principal:
    """Lo que un token autenticado afirma, ya validado y sin roles todavía.

    Es la frontera entre el Paso 7 (auth/JWT) y este paso. `org_id` viaja en
    el token en vez de deducirse del usuario para que resolver el contexto
    cueste **una** query (la de roles) y no dos.
    """

    user_id: UUID
    org_id: UUID
    device_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Contexto **inmutable** de la request (ADR-013).

    `roles` es un `frozenset`: inmutable como el resto del objeto y con
    pertenencia O(1), que es la operación que ejecutan todas las políticas
    dentro de los bucles de sync.
    """

    user_id: UUID
    org_id: UUID
    roles: frozenset[RoleValue]
    device_id: UUID | None = None
    # Fuera de `__eq__`/`__repr__`: la identidad de un contexto son sus ids y
    # sus roles, no lo que haya cacheado; y el repr no debe volcar entidades
    # completas en un log (RD-12).
    entities: ContextEntityCache = field(
        default_factory=ContextEntityCache,
        compare=False,
        repr=False,
    )

    @property
    def is_member(self) -> bool:
        """`True` solo si el usuario tiene alguna membresía en `org_id`."""
        return bool(self.roles)

    def has_any_role(self, roles: Iterable[RoleValue]) -> bool:
        """`True` si el usuario tiene **al menos uno** de los roles pedidos.

        Deny-by-default en los dos casos borde: sin membresías (`roles`
        vacío) y sin roles exigidos (conjunto vacío) devuelve `False`. Pedir
        "ningún rol en concreto" nunca puede conceder acceso.
        """
        return not self.roles.isdisjoint(roles)

    def resolve_entity(self, key: str, loader: Callable[[], T]) -> T:
        """Resuelve y cachea la entidad de contexto de esta request (RD-16)."""
        return self.entities.resolve(key, loader)


def resolve_request_context(principal: Principal) -> RequestContext:
    """Construye el contexto con **exactamente una query** (RD-07).

    Esa query es la de roles. Se usa `values_list("role", flat=True)` y no
    `select_related`/`prefetch_related` porque de `Membership` solo se
    necesita una columna: no hay relación que seguir, así que el JOIN que
    ahorraría `select_related` no llega a existir. El resultado es una sola
    sentencia `SELECT role FROM membership WHERE user_id=%s AND org_id=%s`,
    servida por el índice único `membership_org_user_uq`.

    Un usuario sin membresía en `org_id` obtiene `roles` vacío — no un error:
    la denegación la deciden las políticas, de forma uniforme y deny-by-default.
    """
    roles = frozenset(
        Membership.objects.filter(
            user_id=principal.user_id,
            org_id=principal.org_id,
        ).values_list("role", flat=True)
    )
    return RequestContext(
        user_id=principal.user_id,
        org_id=principal.org_id,
        roles=roles,
        device_id=principal.device_id,
    )


def get_request_context(request: Any) -> RequestContext:
    """Devuelve el contexto que la dependencia dejó en la request.

    Lanza `RequestContextMissing` si no lo hay o si lo que hay no es un
    `RequestContext` — nunca devuelve `None` (ver `RequestContextMissing`).
    """
    ctx = getattr(request, REQUEST_CONTEXT_ATTR, None)
    if not isinstance(ctx, RequestContext):
        raise RequestContextMissing(
            "La request no tiene un RequestContext resuelto. Toda vista "
            "autenticada debe declarar `auth=` con una subclase de "
            "RequestContextAuth (ADR-013)."
        )
    return ctx


class RequestContextAuth(HttpBearer):
    """Dependencia de Django Ninja que resuelve el contexto antes de la vista.

    **Por qué una clase de auth de Ninja y no un middleware de Django**: un
    middleware corre antes de que Ninja haya mirado el `Authorization`, así
    que tendría que decodificar el token por su cuenta y aplicarse a rutas que
    no son de la API. En Ninja, en cambio, lo que devuelve `authenticate()` es
    lo que el framework inyecta como `request.auth` y ejecuta **antes del
    cuerpo de la vista** — que es literalmente el requisito de ADR-013
    ("antes de cualquier bucle"). `HttpBearer` aporta además el parseo del
    header, el `401` automático cuando `authenticate()` devuelve `None` y el
    esquema de seguridad correcto en el OpenAPI generado.

    Subclase única prevista: la del Paso 7, que implementa `resolve_principal`
    decodificando el JWT. `resolve_principal` es abstracto a propósito — una
    implementación por defecto sería una forma de autenticarse sin token.
    """

    @abstractmethod
    def resolve_principal(self, request: Any, token: str) -> Principal | None:
        """Traduce el token en un `Principal`, o `None` si no es válido (Paso 7)."""

    def authenticate(self, request: Any, token: str) -> RequestContext | None:
        principal = self.resolve_principal(request, token)
        if principal is None:
            # `None` ⇒ Ninja responde 401 sin ejecutar la vista. No se resuelve
            # ningún contexto: sin principal no hay a quién resolverle roles.
            return None
        ctx = resolve_request_context(principal)
        setattr(request, REQUEST_CONTEXT_ATTR, ctx)
        return ctx
