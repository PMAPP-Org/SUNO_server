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
from visitacampo.core.models import OrgScopedModel

__all__ = ["NotOrgScopedError", "scoped_qs"]


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
