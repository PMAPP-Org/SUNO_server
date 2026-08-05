"""`scoped_qs`: la única puerta de lectura a una tabla de negocio (ADR-011, RD-11).

Todo acceso a datos de una organización empieza aquí::

    for visit in scoped_qs(ctx, Visit).filter(change_seq__gt=cursor):
        ...

y **nunca** en `Visit.objects.all()` filtrado después. La diferencia no es de
estilo: un `objects.all()` al que se le olvide el `org_id` es una fuga entre
tenants que ningún test funcional detecta (los datos "correctos" también
salen). Con este módulo, el filtro por organización es el punto de partida y
lo que hay que hacer para saltárselo es explícito y revisable — además, el
test de arquitectura (`tests/test_architecture.py`) falla la build si un
módulo de servicios toca un manager directamente.

**Por qué un archivo propio y no `context.py`**: `policies.py` debe seguir
siendo puro (sin I/O) y `context.py` construye el contexto una sola vez por
request. Esto es otra cosa: la puerta de datos que consume el contexto ya
construido. Un archivo, una responsabilidad.

**Por qué se decide por herencia de `OrgScopedModel`** y no por "¿tiene un
campo llamado `org`?": `OrgScopedModel` (ver `models/base.py`) es la base
abstracta que ADR-011 obliga a heredar en toda tabla de negocio, así que la
comprobación es la misma invariante que ya verifica `test_models.py`, no una
heurística sobre nombres de campo que un modelo nuevo podría incumplir sin
que nadie se entere.
"""

from __future__ import annotations

from django.db import models
from django.db.models import QuerySet

from visitacampo.core.context import RequestContext
from visitacampo.core.models import AppUser, Blob, Organization, OrgScopedModel

__all__ = [
    "NotOrgScopedError",
    "blob_registry",
    "get_app_user",
    "get_organization",
    "scoped_qs",
]


class NotOrgScopedError(TypeError):
    """El modelo pedido no tiene `org_id`, así que no puede scoparse.

    Se lanza en vez de devolver un queryset sin filtrar: un
    `scoped_qs(ctx, Blob)` que devolviera `Blob.objects.all()` daría a quien
    lo lee la impresión —falsa— de estar leyendo datos ya aislados por
    organización. Deny-by-default también significa fallar ruidosamente
    cuando no se puede cumplir la garantía que el nombre de la función promete.

    Los dos modelos exentos del `org_id` de ADR-011 (`Organization`, raíz del
    scoping, y `Blob`, contenido deduplicado a propósito entre organizaciones
    — RD-08) llegan aquí por diseño: su autorización se resuelve por la tabla
    que sí lleva `org_id` (`visit_attachment` en el caso de `Blob`, Paso 10).
    """


def scoped_qs[M: models.Model](ctx: RequestContext, model: type[M]) -> QuerySet[M]:
    """Queryset de `model` restringido a la organización del contexto.

    No aplica ningún otro filtro **a propósito**: los tombstones
    (`deleted_at`) forman parte de lo que el pull tiene que entregar
    (ADR-003), así que esconderlos aquí rompería la sincronización de
    borrados. El aislamiento entre tenants es la única garantía de esta
    función, y es completa: cualquier `.filter()`/`.exclude()` encadenado
    después se compone con el `WHERE org_id = %s`, no lo sustituye.
    """
    if not (isinstance(model, type) and issubclass(model, models.Model)):
        raise TypeError(
            f"scoped_qs espera la clase de un modelo de Django, no {model!r}. "
            "Se pasa el modelo (`scoped_qs(ctx, Visit)`), no una instancia."
        )
    if not issubclass(model, OrgScopedModel):
        raise NotOrgScopedError(
            f"{model.__name__} no hereda de OrgScopedModel: no tiene `org_id` y "
            "no puede aislarse por organización (ADR-011). Si es una tabla de "
            "negocio nueva, hazla heredar de OrgScopedModel; si es una de las "
            "dos excepciones documentadas (Organization, Blob), su autorización "
            "se resuelve por la tabla que sí lleva `org_id`."
        )
    return model._default_manager.filter(org_id=ctx.org_id)


def get_organization(ctx: RequestContext) -> Organization:
    """La fila `Organization` del contexto — la única que puede leer.

    `Organization` es la raíz del scoping (ver `NotOrgScopedError` arriba): no
    tiene `org_id` que comparar porque **es** el `org_id`, así que
    `scoped_qs(ctx, Organization)` no tendría sentido y de hecho falla a
    propósito. `ctx.org_id` ya viene resuelto y verificado contra `Membership`
    al construir el `RequestContext` (`core/context.py`, una query, antes de
    cualquier bucle), así que este `get(pk=...)` no es una fuga de aislamiento
    — es leer la única fila que el propio contexto ya nombra por su clave
    primaria. Introducido para el bootstrap del pull (Paso 9,
    `BootstrapResponse.organization`).
    """
    return Organization._default_manager.get(pk=ctx.org_id)


def blob_registry() -> QuerySet[Blob]:
    """La tabla `blob` **sin filtrar por organización**, porque no tiene `org_id`.

    Es la tercera —y última— excepción documentada de este módulo, junto con
    `get_organization` y `get_app_user`, y la que `NotOrgScopedError` ya
    anticipaba en su docstring desde el Paso 6: `Blob` no lleva `org_id` **a
    propósito** (RD-08), porque su razón de ser es deduplicar contenido entre
    dispositivos y entre organizaciones. Un `scoped_qs(ctx, Blob)` no es que
    esté prohibido: es que no significa nada, y por eso falla ruidosamente.

    **No recibe `ctx`, y eso es la advertencia, no un olvido.** Una firma
    `blob_registry(ctx)` sugeriría que el contexto acota algo, y no acota nada.
    Quien llame a esta función es responsable de haber resuelto la autorización
    **antes**, y solo hay una forma sancionada de hacerlo: comprobar con
    `scoped_qs(ctx, VisitAttachment)` que existe un adjunto de la organización
    del solicitante que referencie ese `sha256` (Paso 10 / ADR-007 punto 6, y
    es exactamente lo que hace `attachments/services.py::download_url`). El
    riesgo real es acotado incluso si alguien se saltara ese paso: conocer un
    `sha256` implica poseer ya los bytes exactos que lo producen, así que la
    tabla no revela por sí sola nada que quien pregunta no tuviera ya.

    Se devuelve el `QuerySet` completo y no el manager para que el llamador
    tenga `filter`/`get`/`bulk_create` sin que este módulo tenga que ir
    exponiendo un método por operación.
    """
    return Blob._default_manager.all()


def get_app_user(ctx: RequestContext) -> AppUser:
    """El `AppUser` autenticado del contexto (perfil devuelto por login/bootstrap).

    `AppUser` tampoco hereda `OrgScopedModel` (es `AUTH_USER_MODEL`; su `org`
    es "organización de origen", no la clave que lo aísla — la membresía real
    vive en `Membership`, ver el docstring de `AppUser`). No hace falta
    filtrar también por `org`: `ctx.roles` ya viene resuelto de
    `Membership.objects.filter(user_id=ctx.user_id, org_id=ctx.org_id)`
    (`core/context.py`), así que un `ctx` con roles no vacíos ya certificó la
    pertenencia de este usuario a esta organización antes de que este
    accesor se llame.
    """
    return AppUser._default_manager.get(pk=ctx.user_id)
