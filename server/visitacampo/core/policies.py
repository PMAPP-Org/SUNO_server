"""Funciones de política **puras** sobre un `RequestContext` (ADR-013).

Este es el único módulo del backend donde se decide "quién puede hacer qué".
Toda función es pura: recibe el contexto y el recurso, **no hace I/O** y
devuelve `bool` (`can_*`) o lanza el error HTTP correspondiente (`check_*`).
El estilo se conserva de `permissions_utils.py` de QFieldCloud —reconocido
como patrón sólido en doc 07 (C-12)—; lo que se corrige es cómo se invoca y
qué comprueba:

- **H-07 / RD-07** — allí cada `can_*` consultaba la BD, y llamarlas dentro
  del loop de deltas producía N+1. Aquí no hay ninguna consulta: los roles
  llegan ya resueltos en `ctx` (`visitacampo.core.context`), así que autorizar
  200 operaciones cuesta exactamente lo mismo que autorizar una.
- **H-16 / RD-11** — allí `can_read_members` hacía `return True` sin
  comprobar membresía, y cualquier usuario autenticado podía enumerar los
  miembros de cualquier organización. Aquí **todas** las políticas pasan por
  `user_has_organization_roles`, que exige coincidencia de organización *y*
  un rol explícito. No existe ninguna ruta que devuelva `True` sin ambas
  comprobaciones, y el test `NoRoleGrantsNothingTests` lo verifica sobre el
  módulo entero, no política a política.

**Regla de mantenimiento (ADR-013)**: cualquier PR que toque este archivo
requiere revisión explícita, y las políticas se **extienden**, nunca se
puentean. Si una política necesitara un dato que no está ni en `ctx` ni en el
recurso, el sitio de resolverlo es la construcción del contexto (una query,
antes del bucle — RD-07/RD-16), nunca el cuerpo de la función.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from ninja.errors import HttpError

from visitacampo.core.context import RequestContext, RoleValue
from visitacampo.core.models import Role

__all__ = [
    "ADMIN_ROLES",
    "DEVICE_OWNER_ROLES",
    "MEMBER_ROLES",
    "WRITE_VISIT_ROLES",
    "PolicyDenied",
    "can_manage_device",
    "can_publish_form_template",
    "can_read_members",
    "can_read_visit",
    "can_write_visit",
    "check_manage_device",
    "check_publish_form_template",
    "check_read_members",
    "check_read_visit",
    "check_write_visit",
    "user_has_organization_roles",
]

# --- Conjuntos de roles -----------------------------------------------------
# Listas explícitas y nombradas, no comparaciones sueltas repartidas por el
# código: es lo que hace auditable de un vistazo "quién puede qué" (C-12).

#: Cualquier membresía. Ser miembro ya es una comprobación (nunca `True` a secas).
MEMBER_ROLES: frozenset[RoleValue] = frozenset(Role.values)

#: Administración de la organización.
ADMIN_ROLES: frozenset[RoleValue] = frozenset({Role.OWNER, Role.ADMIN})

#: Quien captura o corrige trabajo de campo. `VIEWER` queda fuera por definición.
WRITE_VISIT_ROLES: frozenset[RoleValue] = frozenset(
    {Role.OWNER, Role.ADMIN, Role.SUPERVISOR, Role.FIELD_AGENT}
)

#: Quien puede tener un dispositivo propio que administrar.
DEVICE_OWNER_ROLES: frozenset[RoleValue] = WRITE_VISIT_ROLES

# --- Códigos de error del contrato (`components.schemas.ErrorCode`) ---------
CODE_FORBIDDEN = "forbidden"
CODE_NOT_FOUND = "not_found"


class OrgScopedResource(Protocol):
    """Lo mínimo que una política necesita de un recurso: a qué org pertenece.

    Tipar así (y no como `Visit`) mantiene la pureza visible: una política no
    puede navegar relaciones —lo que dispararía una query perezosa dentro de
    un bucle, que es justo el bug H-07— porque no declara tener acceso a ellas.
    """

    org_id: UUID


class DeviceLike(OrgScopedResource, Protocol):
    owner_id: UUID


class PolicyDenied(HttpError):
    """Denegación de política, con el `code` estable del contrato.

    `HttpError` es la excepción que Django Ninja traduce a respuesta; `code`
    es el campo `code` del cuerpo de error uniforme (doc 08 §6). El manejador
    que renderiza ese cuerpo se instala con la capa de API (Paso 7): aquí solo
    se transporta el dato para que no haya que reconstruirlo después.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.code = code
        super().__init__(status_code, message)


# --- Primitiva --------------------------------------------------------------


def user_has_organization_roles(
    ctx: RequestContext,
    org_id: UUID,
    roles: Iterable[RoleValue],
) -> bool:
    """`True` si el usuario del contexto es miembro de `org_id` con algún rol de `roles`.

    Es la primitiva que doc 07 recomienda para cerrar H-16
    ("`return user_has_organization_roles(user, org, [...])`"), y la única
    puerta por la que pasan todas las políticas de este módulo. Comprueba las
    dos cosas que hay que comprobar: **la organización** (aislamiento entre
    tenants, RD-11) y **el rol** (autorización).
    """
    return ctx.org_id == org_id and ctx.has_any_role(roles)


def _denial(ctx: RequestContext, resource_org_id: UUID) -> PolicyDenied:
    """Elige entre `404` y `403` (RD-11).

    Si el recurso es de otra organización —o el solicitante ni siquiera es
    miembro de la suya— se responde **404**: un `403` confirmaría que el
    recurso existe, que es la fuga de información que el propio hallazgo H-16
    describe (enumeración de estructura ajena). El `403` se reserva para el
    caso en que no hay nada que ocultar: miembro de la organización correcta
    al que le falta el rol.
    """
    if resource_org_id != ctx.org_id or not ctx.is_member:
        return PolicyDenied(404, CODE_NOT_FOUND, "Resource not found.")
    return PolicyDenied(403, CODE_FORBIDDEN, "Insufficient role for this operation.")


# --- Políticas --------------------------------------------------------------


def can_read_visit(ctx: RequestContext, visit: OrgScopedResource) -> bool:
    """Leer una visita: cualquier miembro de su organización.

    En fase 1 no se estrecha a "solo las visitas propias": el pull de sync
    entrega a cada dispositivo el conjunto de datos de su organización
    (ADR-003) y `VIEWER` existe precisamente para consultar. Si un cliente
    exigiera visibilidad por equipo, se **extiende** esta función con el dato
    correspondiente ya resuelto en el `ctx` (ADR-013: extender, nunca
    puentear), no se añade un filtro suelto en la vista.
    """
    return user_has_organization_roles(ctx, visit.org_id, MEMBER_ROLES)


def can_write_visit(ctx: RequestContext, visit: OrgScopedResource) -> bool:
    """Crear o modificar una visita: todo rol salvo `VIEWER`.

    **Alcance deliberado**: esto responde solo "¿este actor está autorizado?".
    No responde "¿esta escritura es válida?" — el CAS optimista
    (`version`/`base_version`), los tombstones y la idempotencia por `op_uuid`
    son del protocolo de sync (ADR-003, Paso 8) y se comprueban en la
    transacción de escritura. Mezclar ambas cosas aquí rompería la pureza de
    la función (necesitaría leer la fila actual) y devolvería `403` donde el
    contrato exige `409 conflict`.

    Sirve igual para un alta: basta con pasar la instancia candidata sin
    guardar, porque lo único que se lee es su `org_id`.
    """
    return user_has_organization_roles(ctx, visit.org_id, WRITE_VISIT_ROLES)


def can_read_members(ctx: RequestContext, org_id: UUID) -> bool:
    """Listar los miembros de una organización. **Corrección directa de H-16.**

    El original (`permissions_utils.py:668–675`) era::

        def can_read_members(user, organization) -> bool:
            if not organization.is_organization:
                return False
            return True  # ← sin verificar si user es miembro

    Aquí se exige membresía explícita: un usuario sin membresías, o miembro de
    otra organización, obtiene `False`. Cualquier rol vale —conocer a los
    compañeros de la propia organización es parte de operar en ella—, pero
    **ser miembro no se presupone: se comprueba**.
    """
    return user_has_organization_roles(ctx, org_id, MEMBER_ROLES)


def can_publish_form_template(ctx: RequestContext, template: OrgScopedResource) -> bool:
    """Publicar una versión de plantilla de formulario (Paso 20, ADR-012).

    Solo administración (`OWNER`/`ADMIN`), y no `SUPERVISOR`: una plantilla
    define **qué datos se capturan y con qué validaciones** para toda la
    organización, y una versión publicada es inmutable y llega a todos los
    dispositivos por el pull. Es una decisión de configuración del tenant, no
    de operación de campo, así que se agrupa con el resto de la administración.

    No existe `can_edit_form_template`: una versión publicada **no se edita**,
    se publica otra (`forms/services.py`). Una política para editar sería una
    política para algo que el diseño no permite hacer.
    """
    return user_has_organization_roles(ctx, template.org_id, ADMIN_ROLES)


def can_manage_device(ctx: RequestContext, device: DeviceLike) -> bool:
    """Administrar un dispositivo (renombrar, revocar su sesión, darlo de baja).

    Dos vías, y ninguna de ellas concede nada sin rol:

    - los administradores de la organización, sobre cualquier dispositivo de
      ella (necesario para revocar un teléfono perdido, ADR-008);
    - su propietario, si tiene un rol que pueda operar en campo — un `VIEWER`
      no sincroniza desde un dispositivo, así que tampoco lo administra.
    """
    if ctx.org_id != device.org_id:
        return False
    if ctx.has_any_role(ADMIN_ROLES):
        return True
    return device.owner_id == ctx.user_id and ctx.has_any_role(DEVICE_OWNER_ROLES)


# --- Variantes `check_*` ----------------------------------------------------
# Misma decisión, expresada como excepción HTTP. Existen para que una vista no
# tenga que acordarse de traducir `False` al código correcto (y no acabe
# devolviendo un 403 donde RD-11 exige un 404).


def check_read_visit(ctx: RequestContext, visit: OrgScopedResource) -> None:
    if not can_read_visit(ctx, visit):
        raise _denial(ctx, visit.org_id)


def check_write_visit(ctx: RequestContext, visit: OrgScopedResource) -> None:
    if not can_write_visit(ctx, visit):
        raise _denial(ctx, visit.org_id)


def check_read_members(ctx: RequestContext, org_id: UUID) -> None:
    if not can_read_members(ctx, org_id):
        raise _denial(ctx, org_id)


def check_publish_form_template(ctx: RequestContext, template: OrgScopedResource) -> None:
    if not can_publish_form_template(ctx, template):
        raise _denial(ctx, template.org_id)


def check_manage_device(ctx: RequestContext, device: DeviceLike) -> None:
    if not can_manage_device(ctx, device):
        raise _denial(ctx, device.org_id)
