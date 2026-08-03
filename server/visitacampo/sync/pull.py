"""`GET /v1/sync/bootstrap` y `GET /v1/sync/pull` (doc 08 §4.4, §6).

Contraparte de lectura de `services.py` (el push). Dos operaciones, un solo
módulo porque comparten la misma tabla de entidades sincronizables
(`_ENTITY_MODELS`, hermana de `changeseq.SYNCED_MODELS` y
`records.RECORD_BUILDERS`) y el mismo cursor:

- **Bootstrap**: estado **vivo** completo de la organización (doc 08 §6:
  "estado inicial completo, sin paginación en v1") + el cursor desde el que
  seguir con el pull. No hay `change_seq > cursor` que aplicar: es "todo lo
  que hay hoy", así que el cursor que se entrega es simplemente el más alto
  ya asignado (`changeseq.highest_change_seq`) — el pull siguiente solo verá
  lo que cambie *después* de este bootstrap.
- **Pull**: página incremental por `change_seq`, con el algoritmo de "top-N de
  una unión de listas ordenadas" explicado en `_merged_page` — es la pieza que
  hace que "orden estricto ascendente de `change_seq`" (contrato) sea cierto
  aunque las cuatro entidades vivan en tablas distintas.

**Autorización**: la misma que el push (`can_read_visit`/`check_read_visit`,
`core/policies.py`) — "cualquier miembro de la organización", ya documentado
ahí explícitamente para el pull ("el pull de sync entrega a cada dispositivo
el conjunto de datos de su organización"). No hace falta una política nueva:
las cuatro entidades del pull son visibles al mismo nivel que `visit`, no hay
un control más fino que aplicar en el MVP.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ninja.errors import HttpError

from visitacampo.core.context import RequestContext
from visitacampo.core.models import (
    Assignment,
    FormTemplateVersion,
    SyncedModel,
    Visit,
    VisitAttachment,
)
from visitacampo.core.policies import check_read_visit
from visitacampo.core.scoping import get_app_user, get_organization, scoped_qs
from visitacampo.sync.changeseq import highest_change_seq
from visitacampo.sync.cursor import encode_cursor
from visitacampo.sync.records import RECORD_BUILDERS, tombstone
from visitacampo.sync.retention import current_floor, cursor_is_expired

__all__ = ["CursorExpired", "PullPage", "bootstrap_state", "pull_page"]

#: Entidad ↔ modelo, en el mismo orden que `changeseq.SYNCED_MODELS` y
#: `records.RECORD_BUILDERS` — las tres listas nombran el mismo conjunto y
#: `tests/test_pull.py::EntityCoverageTests` lo verifica, igual que
#: `test_changeseq.py` ya hace para `SYNCED_MODELS` por su cuenta.
_ENTITY_MODELS: tuple[tuple[str, type[SyncedModel]], ...] = (
    ("visit", Visit),
    ("visit_attachment", VisitAttachment),
    ("assignment", Assignment),
    ("form_template_version", FormTemplateVersion),
)


@dataclass(slots=True)
class _OrgResource:
    """Ver `sync/services.py::_OrgResource` — misma forma, mismo motivo.

    No se importa de allí (nombre privado) porque lo único que una política
    necesita es `org_id`; duplicar tres líneas es más simple que exportar un
    símbolo pensado como interno de otro módulo solo para esto.
    """

    org_id: uuid.UUID


class CursorExpired(HttpError):
    """409, `code: cursor_expired` (contrato).

    Se lanza desde el servicio —no desde `api.py`— porque decidirlo exige leer
    `SyncCursorFloor` (`retention.cursor_is_expired`), que es una consulta de
    negocio, no una comprobación de transporte. Mismo patrón que
    `services.DeviceMismatch`: los `HttpError` que dependen de datos de la
    organización se definen junto al código que los produce.
    """

    code = "cursor_expired"

    def __init__(self) -> None:
        super().__init__(
            409,
            "Cursor is older than the tombstone retention window; run a full bootstrap.",
        )


def bootstrap_state(ctx: RequestContext) -> dict[str, Any]:
    """Contrato `BootstrapResponse`: estado vivo completo + cursor actual.

    "Vivo" (`deleted_at__isnull=True`) a propósito: doc 08 §6 dice "estado
    inicial completo" para un cliente que instala la app por primera vez o que
    perdió su copia local — no tiene sentido entregarle tombstones de filas
    que nunca tuvo. Un cliente que sí necesita los tombstones porque ya tenía
    una copia parcial usa `GET /v1/sync/pull` desde el `cursor` que este
    bootstrap devuelve, no el bootstrap otra vez.
    """
    check_read_visit(ctx, _OrgResource(ctx.org_id))

    organization = get_organization(ctx)
    user = get_app_user(ctx)

    live_by_entity = {
        entity: _live_rows(ctx, entity, model) for entity, model in _ENTITY_MODELS
    }

    # `max(...)`: si la fila con el `change_seq` más alto de la organización
    # fue justo la que se purgó físicamente, `highest_change_seq` por sí solo
    # cae por debajo del piso que esa purga dejó (`SyncCursorFloor`) — un
    # pull inmediatamente posterior a este bootstrap, con el cursor "corto"
    # que resultaría, sería rechazado con `cursor_expired` sin motivo: el
    # bootstrap ya entregó el estado reconciliado, no hay nada que ese pull
    # necesitara recuperar de antes del piso.
    cursor_value = max(highest_change_seq(ctx), current_floor(ctx))

    return {
        "cursor": encode_cursor(cursor_value),
        "organization": {"id": organization.pk, "name": organization.name},
        "user": {
            "id": user.pk,
            "username": user.get_username(),
            "display_name": user.display_name,
            "org_id": ctx.org_id,
            "roles": sorted(ctx.roles),
        },
        "form_template_versions": live_by_entity["form_template_version"],
        "assignments": live_by_entity["assignment"],
        "visits": live_by_entity["visit"],
        "visit_attachments": live_by_entity["visit_attachment"],
        "has_more": False,
    }


def _live_rows(ctx: RequestContext, entity: str, model: type[SyncedModel]) -> list[dict[str, Any]]:
    """Las filas vivas de `model` (`deleted_at IS NULL`), como `data` de su `*Record`.

    El bootstrap emite `BootstrapResponse.visits`/`.assignments`/... como
    arrays del tipo de entidad puro (`Visit`, `Assignment`…), no de
    `EntityRecord` — el array ya dice de qué tipo es cada fila por su clave,
    envolver cada elemento sería redundante. Se reutiliza el mismo
    `RECORD_BUILDERS` que el pull y se descarta el envoltorio (`{entity,
    data}` → `data`), en vez de mantener un segundo juego de serializadores
    que podría divergir del primero. `entity` se recibe explícito —no se
    infiere de `model._meta.model_name`— porque ese nombre no coincide letra
    a letra con la clave del contrato para dos de las cuatro entidades
    (`visitattachment`/`visit_attachment`, `formtemplateversion`/
    `form_template_version`).
    """
    builder = RECORD_BUILDERS[entity]
    return [builder(row)["data"] for row in scoped_qs(ctx, model).filter(deleted_at__isnull=True)]


@dataclass(frozen=True, slots=True)
class PullPage:
    """El resultado de `pull_page`, ya en forma de contrato `PullResponse` (sin envoltorio)."""

    rows: list[dict[str, Any]]
    tombstones: list[dict[str, Any]]
    next_cursor: str
    has_more: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "tombstones": self.tombstones,
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
        }


def _candidates(
    ctx: RequestContext, cursor_value: int, fetch_size: int
) -> list[tuple[str, SyncedModel]]:
    """Hasta `fetch_size` filas de **cada** entidad, con `change_seq > cursor_value`.

    `fetch_size` (no `limit`) es a propósito: ver `pull_page` para el
    argumento de por qué `limit + 1` por tabla basta para responder `has_more`
    con certeza y sin una query de más.
    """
    candidates: list[tuple[str, SyncedModel]] = []
    for entity, model in _ENTITY_MODELS:
        rows = (
            scoped_qs(ctx, model)
            .filter(change_seq__gt=cursor_value)
            .order_by("change_seq")[:fetch_size]
        )
        candidates.extend((entity, row) for row in rows)
    return candidates


def pull_page(ctx: RequestContext, *, cursor_value: int, limit: int) -> PullPage:
    """Contrato `PullResponse`: página de cambios en orden estricto de `change_seq`.

    **El algoritmo de la página** (mezclar cuatro tablas en un único orden):
    pedir `limit + 1` filas de **cada** entidad, ya ordenadas por
    `change_seq` — es la cantidad justa para que la página resultante sea
    correcta sin necesidad de una query de recuento aparte. El argumento:
    si una fila `f` está entre las `limit + 1` de menor `change_seq` de *toda*
    la organización, su posición dentro de **su propia** tabla no puede ser
    mayor que `limit + 1` (cada fila anterior a `f` en el orden global, o
    bien es de su misma tabla —y por tanto cuenta también en su posición
    local—, o es de otra tabla). Así que las `limit + 1` filas más antiguas de
    cada tabla ya contienen, con certeza, las `limit + 1` filas más antiguas
    de la unión completa. Mezclarlas y quedarse con las `limit` primeras da la
    página; que sobrevivan candidatas después de esas `limit` es, por el mismo
    argumento, la respuesta exacta a `has_more` — no una aproximación.

    `change_seq` es un contador **único por organización** entre las cuatro
    entidades (`changeseq.py`: "el push asignaría `MAX(visit.change_seq)+1`,
    podría repetir un número ya usado por un `assignment`"), así que dos filas
    de tablas distintas nunca empatan y el orden de la mezcla es total.
    """
    check_read_visit(ctx, _OrgResource(ctx.org_id))
    if cursor_is_expired(ctx, cursor_value):
        raise CursorExpired

    candidates = _candidates(ctx, cursor_value, fetch_size=limit + 1)
    candidates.sort(key=lambda pair: pair[1].change_seq)

    page = candidates[:limit]
    has_more = len(candidates) > limit

    rows: list[dict[str, Any]] = []
    tombstones: list[dict[str, Any]] = []
    for entity, instance in page:
        deleted_at: datetime | None = instance.deleted_at
        if deleted_at is None:
            rows.append(RECORD_BUILDERS[entity](instance))
        else:
            tombstones.append(
                tombstone(
                    entity=entity,
                    entity_id=instance.pk,
                    change_seq=instance.change_seq,
                    deleted_at=deleted_at,
                )
            )

    next_cursor_value = page[-1][1].change_seq if page else cursor_value
    return PullPage(
        rows=rows,
        tombstones=tombstones,
        next_cursor=encode_cursor(next_cursor_value),
        has_more=has_more,
    )
