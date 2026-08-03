"""Purga de tombstones y el piso de cursor que la hace segura (Paso 9 §4.4).

Doc 08 §4.4: los tombstones (`deleted_at IS NOT NULL`) se purgan físicamente
90 días después de su borrado lógico (`RETENTION_DAYS`). El plan de este paso
lo dice explícitamente: "se implementa la lógica pura y se deja preparada" —
el *job* periódico que invoca esta función de verdad, con recurrencia y
lease, es el Paso 19 (ADR-006). Lo que este archivo entrega es la función en
sí, ya correcta y probada, y el mecanismo que hace que purgar sea seguro para
el pull: `SyncCursorFloor` (ver su docstring en `core/models/ops.py`).

**Por qué la purga tiene que actualizar algo más que borrar filas.** Sin
`SyncCursorFloor`, borrar un tombstone con `change_seq=50` sería indistinguible
—para un cliente con `cursor=20`— de que esa fila nunca existió: su próximo
`GET /v1/sync/pull?cursor=c1:...20` vería `change_seq > 20` sin la fila 50 y
avanzaría su cursor sin enterarse de que algo se borró. `cursor_is_expired`
es la comprobación que convierte esa pérdida silenciosa en un `409
cursor_expired` explícito (`sync/api.py`), que es justo lo que doc 08 §4.4
exige como "camino de recuperación explícito y probado, no un caso no
manejado".
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from visitacampo.core.context import RequestContext
from visitacampo.core.models import RETENTION_DAYS, SyncCursorFloor
from visitacampo.core.scoping import scoped_qs
from visitacampo.sync.changeseq import SYNCED_MODELS

__all__ = ["current_floor", "cursor_is_expired", "default_cutoff", "purge_expired_tombstones"]


def default_cutoff(now: datetime | None = None) -> datetime:
    """El instante de corte de retención: `now - RETENTION_DAYS` (90 días)."""
    return (now or timezone.now()) - timedelta(days=RETENTION_DAYS)


def purge_expired_tombstones(ctx: RequestContext, cutoff: datetime | None = None) -> int:
    """Borra físicamente los tombstones de `ctx.org_id` anteriores a `cutoff`.

    Recorre las mismas `SYNCED_MODELS` que `changeseq.py` (las cuatro
    entidades del cursor de pull), no solo `visit`: un tombstone purgado de
    cualquiera de ellas es la misma clase de pérdida silenciosa si no se
    registra en `SyncCursorFloor`.

    **Alcance: una organización por llamada**, igual que el resto del módulo
    de sync (`ctx.org_id`, vía `scoped_qs`). El Paso 19 es quien decide cómo
    iterar todas las organizaciones del despliegue —hoy, en el MVP, una sola
    (ADR-011, S-09)— y con qué `RequestContext` (o su equivalente de sistema)
    invoca esta función por cada una.

    Envuelta en una transacción porque el piso (`SyncCursorFloor`) tiene que
    subir **atómicamente** con el borrado que lo justifica: si el proceso
    muriera entre las dos mitades, la mitad peligrosa es purgar sin subir el
    piso (dejaría cursores viejos sin detectar como expirados), así que las
    dos van juntas o ninguna.
    """
    resolved_cutoff = cutoff if cutoff is not None else default_cutoff()
    with transaction.atomic():
        highest_purged = 0
        purged_count = 0
        for model in SYNCED_MODELS:
            eligible = scoped_qs(ctx, model).filter(
                deleted_at__isnull=False, deleted_at__lt=resolved_cutoff
            )
            highest = eligible.aggregate(top=Max("change_seq"))["top"]
            deleted, _ = eligible.delete()
            purged_count += deleted
            if highest is not None and highest > highest_purged:
                highest_purged = highest

        if highest_purged > 0:
            # `select_for_update()`: dos purgas concurrentes de la misma
            # organización (un reintento del job de Paso 19 tras un timeout,
            # por ejemplo) no deben poder pisarse el `floor` — la misma razón
            # de `SELECT FOR UPDATE` que el Paso 15 exige para la rotación de
            # refresh tokens.
            floor_row, created = (
                scoped_qs(ctx, SyncCursorFloor)
                .select_for_update()
                .get_or_create(org_id=ctx.org_id, defaults={"floor": highest_purged})
            )
            if not created and highest_purged > floor_row.floor:
                floor_row.floor = highest_purged
                floor_row.save(update_fields=["floor"])
    return purged_count


def current_floor(ctx: RequestContext) -> int:
    """El piso de purga vigente de `ctx.org_id`, o 0 si la organización nunca purgó nada.

    Extraído de `cursor_is_expired` porque `pull.py::bootstrap_state` también
    lo necesita: el cursor que un bootstrap devuelve nunca puede ser inferior
    al piso, o el pull inmediatamente siguiente lo rechazaría con
    `cursor_expired` (visto en la revisión de este paso: si la fila con el
    `change_seq` más alto de la organización es justo la que se purgó,
    `highest_change_seq` por sí solo cae por debajo del piso que esa misma
    purga dejó).
    """
    floor_row = scoped_qs(ctx, SyncCursorFloor).first()
    return floor_row.floor if floor_row is not None else 0


def cursor_is_expired(ctx: RequestContext, cursor_value: int) -> bool:
    """`True` si `cursor_value` es anterior a la última purga de `ctx.org_id`.

    Una organización que nunca ha purgado nada no tiene fila en
    `SyncCursorFloor`: su piso implícito es 0, y ningún `change_seq` real
    (siempre `>= 1`, ver `changeseq.ChangeSeqAllocator`) puede ser menor que
    eso, así que la función nunca devuelve `True` para esas organizaciones.
    """
    return cursor_value < current_floor(ctx)
