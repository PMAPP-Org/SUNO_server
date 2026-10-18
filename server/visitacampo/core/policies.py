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
    "SUPERVISION_ROLES",
    "WRITE_VISIT_ROLES",
    "PolicyDenied",
    "can_assign_work",
    "can_export_visits",
    "can_manage_device",
    "can_manage_personal_data",
    "can_publish_form_template",
    "can_read_job_output",
    "can_read_members",
    "can_read_visit",
    "can_record_consent",
    "can_review_visit",
    "can_upload_attachment",
    "can_write_visit",
    "check_assign_work",
    "check_export_visits",
    "check_manage_device",
    "check_manage_personal_data",
    "check_publish_form_template",
    "check_read_job_output",
    "check_read_members",
    "check_read_visit",
    "check_record_consent",
    "check_review_visit",
    "check_upload_attachment",
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

#: Back-office (Paso 24): quien supervisa el trabajo de campo — lo valida o
#: rechaza y lo exporta. `FIELD_AGENT` y `VIEWER` quedan fuera.
SUPERVISION_ROLES: frozenset[RoleValue] = frozenset({Role.OWNER, Role.ADMIN, Role.SUPERVISOR})

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


def can_upload_attachment(ctx: RequestContext, org_id: UUID) -> bool:
    """Pedir una URL prefirmada de subida (Paso 17, ADR-007).

    Mismos roles que escribir una visita —un adjunto **es** trabajo de campo—,
    así que un `VIEWER` no obtiene URL de subida: no captura, no sube.

    Existe como política propia en vez de reutilizar `can_write_visit` porque
    el contrato hace `visit_id` **opcional** en `PresignRequest`: el dispositivo
    puede pedir la firma de una foto antes de decidir a qué visita la adjunta.
    Sin `visit_id` no hay recurso que pasar a `can_write_visit`, y forzar uno
    ficticio para poder llamarla sería justo el tipo de puenteo que ADR-013
    prohíbe. Cuando **sí** viene `visit_id`, el servicio llama además a
    `check_write_visit` sobre la visita real: esta política es el suelo, no el
    techo.

    Lo que **no** hace, y es deliberado: no mira el `sha256`. Autorizar por
    contenido no tendría sentido (el hash aún no existe en el servidor) y
    tampoco haría falta — el aislamiento real de los adjuntos vive en
    `visit_attachment`, que sí lleva `org_id`.
    """
    return user_has_organization_roles(ctx, org_id, WRITE_VISIT_ROLES)


def can_read_job_output(ctx: RequestContext, job: OrgScopedResource) -> bool:
    """Leer el estado y la **salida** de un job asíncrono (Paso 19, RD-12).

    Solo administración (`OWNER`/`ADMIN`), que es lo que ADR-006 exige
    literalmente ("`job.output` … solo es legible por ADMIN") como segunda
    mitad de la mitigación de **H-17**: la primera es que la salida se
    persiste ya redactada (`core/scrub.py`), y esta es que ni siquiera la
    salida redactada se enseña a quien no administra el tenant.

    **Por qué esta política gobierna el endpoint entero** y no solo el campo:
    el contrato declara `Job` con `output` entre sus propiedades `required`
    (`GET /v1/jobs/{id}`), así que no existe una vista "job sin output" que
    ofrecer a un `SUPERVISOR`. Inventar aquí una segunda política más laxa
    para el resto de los campos sería exactamente el double-bookkeeping que
    RD-14 prohíbe en la puerta de al lado: dos reglas para un mismo recurso
    que un día divergen. Un miembro sin rol de administración recibe `403`
    (hay recurso, le falta rol) y uno de otra organización `404` (RD-11) — los
    dos códigos que el contrato declara para este endpoint.
    """
    return user_has_organization_roles(ctx, job.org_id, ADMIN_ROLES)


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


def can_review_visit(ctx: RequestContext, visit: OrgScopedResource) -> bool:
    """Validar o rechazar una visita desde el back-office (Paso 24).

    Supervisión de la organización de la visita. Un agente no valida su propio
    trabajo ni el de otros (segregación de funciones, ISO 27001 A.5.3).
    """
    return user_has_organization_roles(ctx, visit.org_id, SUPERVISION_ROLES)


def can_assign_work(ctx: RequestContext, org_id: UUID) -> bool:
    """Crear asignaciones de trabajo para los agentes (Paso 24): supervisión."""
    return user_has_organization_roles(ctx, org_id, SUPERVISION_ROLES)


def can_export_visits(ctx: RequestContext, org_id: UUID) -> bool:
    """Exportar visitas a CSV/Excel (Paso 24).

    Un archivo exportado sale del control de la aplicación (ubicaciones,
    respuestas): solo supervisión, y cada exportación queda en la auditoría.
    """
    return user_has_organization_roles(ctx, org_id, SUPERVISION_ROLES)


def can_record_consent(ctx: RequestContext, org_id: UUID) -> bool:
    """Registrar la propia autorización de datos personales (Ley 1581, art. 9).

    Cualquier miembro, para sí mismo: la autorización es del titular. Que el
    dispositivo sea suyo lo comprueba el servicio (`privacy/services.py`).
    """
    return user_has_organization_roles(ctx, org_id, MEMBER_ROLES)


def can_manage_personal_data(ctx: RequestContext, org_id: UUID) -> bool:
    """Atender derechos del titular: consulta y supresión (Ley 1581, Paso 24).

    Solo administración: suprimir es irreversible, y exportar entrega todos los
    datos de una persona.
    """
    return user_has_organization_roles(ctx, org_id, ADMIN_ROLES)


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


def check_upload_attachment(ctx: RequestContext, org_id: UUID) -> None:
    if not can_upload_attachment(ctx, org_id):
        raise _denial(ctx, org_id)


def check_read_job_output(ctx: RequestContext, job: OrgScopedResource) -> None:
    if not can_read_job_output(ctx, job):
        raise _denial(ctx, job.org_id)


def check_manage_device(ctx: RequestContext, device: DeviceLike) -> None:
    if not can_manage_device(ctx, device):
        raise _denial(ctx, device.org_id)


def check_review_visit(ctx: RequestContext, visit: OrgScopedResource) -> None:
    if not can_review_visit(ctx, visit):
        raise _denial(ctx, visit.org_id)


def check_assign_work(ctx: RequestContext, org_id: UUID) -> None:
    if not can_assign_work(ctx, org_id):
        raise _denial(ctx, org_id)


def check_export_visits(ctx: RequestContext, org_id: UUID) -> None:
    if not can_export_visits(ctx, org_id):
        raise _denial(ctx, org_id)


def check_record_consent(ctx: RequestContext, org_id: UUID) -> None:
    if not can_record_consent(ctx, org_id):
        raise _denial(ctx, org_id)


def check_manage_personal_data(ctx: RequestContext, org_id: UUID) -> None:
    if not can_manage_personal_data(ctx, org_id):
        raise _denial(ctx, org_id)
