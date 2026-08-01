"""La transacción del push: lock → idempotencia → CAS → log (ADR-003, doc 08 §4.2).

**El orden exacto, y por qué es ese** (doc 08 §4.2, diagrama de secuencia):

```
BEGIN
  SELECT pg_advisory_xact_lock(org_id)          ← 1. antes de tocar nada
  SELECT ... FROM sync_op_log WHERE op_uuid IN  ← 2. idempotencia, ya bajo el lock
  SELECT MAX(change_seq) ...                    ← 3. de dónde sigue el cursor
  SELECT ... FROM form_template_version|...     ← 4. referencias del lote
  por operación:                                ← 5. cero queries de permisos
      UPSERT ... WHERE version = base_version   ←    una sentencia, con change_seq dentro
  INSERT INTO sync_op_log ...                   ← 6. el resultado de cada operación
COMMIT                                          ← 7. suelta el lock
```

Los tres puntos donde el orden **no** es intercambiable:

1. **El lock va primero y cubre el lote entero.** Si se tomara por operación,
   entre una y la siguiente se colaría otra transacción de la misma
   organización y el orden de asignación de `change_seq` dejaría de coincidir
   con el orden de `COMMIT` (doc 08 §4.4: la fila perdida para siempre).

2. **La comprobación de idempotencia va *después* de tomar el lock, no antes.**
   Es lo que cierra la carrera "misma `op_uuid` dos veces a la vez". Sin el
   lock habría una ventana entre "no está en `sync_op_log`" y "la escribo" en
   la que las dos requests leerían "no está" y aplicarían las dos. Con el lock
   tomado antes de la lectura, la segunda request **no llega a leer** hasta
   que la primera ha commiteado; entonces lee (`READ COMMITTED` toma snapshot
   nuevo por sentencia), encuentra la fila y devuelve el resultado guardado.
   El lock no es solo del `change_seq`: es el mecanismo **único** de invariante
   de concurrencia del push (RD-14, corrige H-13). No hay un segundo candado,
   ni un `SELECT FOR UPDATE`, ni una constraint que haga de red.

3. **`change_seq` se asigna dentro del lock y se escribe en la misma sentencia
   que aplica el cambio.** Ver `changeseq.py` para el argumento completo.

**Autorización: resuelta antes del bucle, cero queries dentro** (RD-07 /
H-07 / ADR-013). El bucle no llama a ninguna `can_*`: para `visit`, la
política depende solo del `org_id` del recurso, y el `org_id` de toda fila que
este endpoint escribe es `ctx.org_id` por construcción (el contrato no admite
`org_id` en `VisitPayload`; lo pone el servidor en `sql.build_insert`). Siendo
el mismo predicado para las 500 operaciones, evaluarlo 500 veces sería ruido:
se evalúa **una** vez, arriba, con `check_write_visit`. Si una entidad futura
tuviera una política que dependiera de la fila, la llamada baja al bucle y
sigue costando cero queries — que es la garantía que RD-07 pide y la que mide
`tests/test_query_budget.py`.

**Qué no hace este paso** (y dónde vive): validar `answers` contra el JSON
Schema de su `template_version` es el Paso 20; el pull y su cursor, el Paso 9;
los adjuntos, el Paso 10.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import connection, transaction
from django.utils import timezone

from visitacampo.core.context import RequestContext
from visitacampo.core.models import Assignment, Device, FormTemplateVersion, SyncOpLog, Visit
from visitacampo.core.policies import PolicyDenied, check_write_visit
from visitacampo.core.scoping import scoped_qs
from visitacampo.sync import sql
from visitacampo.sync.changeseq import ChangeSeqAllocator, acquire_org_lock
from visitacampo.sync.payloads import (
    CODE_NOT_FOUND,
    CODE_VALIDATION_ERROR,
    Rejection,
    VisitWrite,
    parse_op,
)
from visitacampo.sync.records import visit_record
from visitacampo.sync.schemas import RawSyncOp

__all__ = ["apply_push"]

#: `code` del contrato para un resultado `conflict` ("Presente en `rejected`
#: (y en `conflict` con `code: conflict`)").
CODE_CONFLICT = "conflict"

STATUS_APPLIED = "applied"
STATUS_CONFLICT = "conflict"
STATUS_REJECTED = "rejected"


@dataclass(slots=True)
class _OrgResource:
    """Recurso mínimo que una política necesita: a qué organización pertenece.

    Cumple el `Protocol` `OrgScopedResource` de `core/policies.py`. Existe para
    poder preguntar "¿puede este actor escribir visitas *de esta organización*?"
    sin fabricar una instancia de `Visit` a medio construir.

    No es `frozen`: un `Protocol` con un atributo declarado (`org_id: UUID`)
    exige que sea asignable, y una dataclass congelada no lo es. Es una
    instancia de un solo uso, local a una llamada, así que la inmutabilidad
    aquí no compra nada.
    """

    org_id: uuid.UUID


class DeviceMismatch(PolicyDenied):
    """403: el `device_id` del lote no es el del token que lo envía.

    ADR-008 ata la outbox y el refresh token al dispositivo, y
    `refresh_session` ya rechaza un refresh presentado desde otro `device_id`.
    Aceptar aquí un lote que dice venir de otro dispositivo permitiría
    atribuir trabajo de campo a un teléfono que nunca lo capturó, y dejaría en
    `sync_op_log` —que es traza de auditoría— un `device_id` falso. Es un
    problema de identidad, no de formato: por eso `403` y no `422`.
    """

    def __init__(self) -> None:
        super().__init__(403, "forbidden", "The batch device_id does not match the caller.")


def _rejected_result(op: RawSyncOp, rejection: Rejection) -> dict[str, Any]:
    return {
        "op_uuid": str(op.op_uuid),
        "entity": op.entity,
        "entity_id": None if rejection.entity_id is None else str(rejection.entity_id),
        "status": STATUS_REJECTED,
        "server_version": None,
        "server_row": None,
        "error": {
            "code": rejection.code,
            "message": rejection.message,
            "details": rejection.details,
        },
    }


def _applied_result(op: RawSyncOp, entity_id: uuid.UUID, version: int) -> dict[str, Any]:
    return {
        "op_uuid": str(op.op_uuid),
        "entity": op.entity,
        "entity_id": str(entity_id),
        "status": STATUS_APPLIED,
        "server_version": version,
        "server_row": None,
        "error": None,
    }


def _conflict_result(op: RawSyncOp, visit: Visit) -> dict[str, Any]:
    """Conflicto **con la fila autoritativa completa** adjunta (contrato).

    Sin `server_row` el cliente tendría que re-descargar el proyecto entero
    para reconciliar, que es exactamente el comportamiento de QFieldCloud que
    ADR-003 rechaza.
    """
    return {
        "op_uuid": str(op.op_uuid),
        "entity": op.entity,
        "entity_id": str(visit.pk),
        "status": STATUS_CONFLICT,
        "server_version": visit.version,
        "server_row": visit_record(visit),
        "error": {
            "code": CODE_CONFLICT,
            "message": "The row changed on the server since base_version.",
            "details": {"field": "base_version", "reason": "stale"},
        },
    }


def _saved_results(ctx: RequestContext, op_uuids: list[uuid.UUID]) -> dict[str, dict[str, Any]]:
    """Resultados ya registrados, en **una** query para todo el lote.

    Una consulta por operación sería el N+1 que este paso existe para no
    tener. Va por `scoped_qs`: el log de otra organización no es visible, así
    que una `op_uuid` ajena no puede devolver el resultado de nadie más.
    """
    if not op_uuids:
        return {}
    rows = scoped_qs(ctx, SyncOpLog).filter(op_uuid__in=op_uuids).values_list("op_uuid", "result")
    return {str(op_uuid): result for op_uuid, result in rows}


def _existing_ids(ctx: RequestContext, model: Any, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    return set(scoped_qs(ctx, model).filter(pk__in=ids).values_list("pk", flat=True))


@dataclass(frozen=True, slots=True)
class _References:
    """Qué referencias del lote existen **en esta organización**.

    Se resuelven en bloque, antes del bucle: tres queries como mucho, sean 1 o
    500 operaciones. No es una optimización, es corrección en dos frentes:

    - **RD-11**: las FK del esquema son globales, así que sin esta
      comprobación una organización podría crear una visita apuntando a la
      `form_template_version` de otra. `scoped_qs` hace que una referencia
      ajena sencillamente no exista.
    - **Nunca un 500**: una FK inexistente reventaría el `INSERT` con
      `IntegrityError` y, dentro de una transacción, dejaría el resto del lote
      inservible. Comprobado antes, es un `rejected` con detalle.
    """

    template_versions: set[uuid.UUID]
    assignments: set[uuid.UUID]
    devices: set[uuid.UUID]

    def missing(self, fields: dict[str, Any]) -> str | None:
        """Nombre del primer campo con una referencia inexistente, o `None`."""
        for field, known in (
            ("template_version_id", self.template_versions),
            ("assignment_id", self.assignments),
            ("device_id", self.devices),
        ):
            value = fields.get(field)
            if value is not None and value not in known:
                return field
        return None


def _resolve_references(ctx: RequestContext, writes: list[VisitWrite]) -> _References:
    wanted: dict[str, set[uuid.UUID]] = {
        "template_version_id": set(),
        "assignment_id": set(),
        "device_id": set(),
    }
    for write in writes:
        for field, bucket in wanted.items():
            value = write.fields.get(field)
            if value is not None:
                bucket.add(value)
    return _References(
        template_versions=_existing_ids(
            ctx, FormTemplateVersion, wanted["template_version_id"]
        ),
        assignments=_existing_ids(ctx, Assignment, wanted["assignment_id"]),
        devices=_existing_ids(ctx, Device, wanted["device_id"]),
    )


def _execute(cursor: Any, statement: sql.Statement) -> tuple[int, ...] | None:
    """Ejecuta la sentencia CAS. `None` ⇒ la guarda no coincidió ⇒ conflicto."""
    cursor.execute(statement.sql, list(statement.params))
    row = cursor.fetchone()
    return None if row is None else tuple(row)


def _log_row(
    ctx: RequestContext,
    *,
    op_uuid: uuid.UUID,
    device_id: uuid.UUID,
    batch_uuid: uuid.UUID,
    entity: str,
    entity_id: uuid.UUID,
    result: dict[str, Any],
) -> SyncOpLog:
    return SyncOpLog(
        op_uuid=op_uuid,
        org_id=ctx.org_id,
        device_id=device_id,
        batch_uuid=batch_uuid,
        entity=entity,
        entity_id=entity_id,
        result=result,
    )


def apply_push(
    ctx: RequestContext,
    *,
    device_id: uuid.UUID,
    batch_uuid: uuid.UUID,
    ops: list[RawSyncOp],
) -> list[dict[str, Any]]:
    """Aplica el lote entero en **una** transacción y devuelve un resultado por operación.

    El orden de `results` es el de `ops` (contrato: "un resultado por operación
    enviada, en el mismo orden"), incluidas las operaciones repetidas dentro
    del propio lote, que reciben todas el resultado de la primera.
    """
    if ctx.device_id is None or ctx.device_id != device_id:
        raise DeviceMismatch
    # Autorización: una vez, antes del bucle (ADR-013, RD-07). Cero queries:
    # los roles ya vienen resueltos en el contexto.
    check_write_visit(ctx, _OrgResource(ctx.org_id))

    with transaction.atomic():
        acquire_org_lock(ctx.org_id)

        saved = _saved_results(ctx, [op.op_uuid for op in ops])

        # Paso puro: validar todo lo que no venga ya resuelto del log.
        parsed: dict[str, VisitWrite | Rejection] = {}
        for op in ops:
            key = str(op.op_uuid)
            if key in saved or key in parsed:
                continue
            parsed[key] = parse_op(op.entity, op.op_type, op.base_version, op.payload)

        references = _resolve_references(
            ctx, [item for item in parsed.values() if isinstance(item, VisitWrite)]
        )
        allocator = ChangeSeqAllocator.for_org(ctx)
        now = timezone.now()

        results: list[dict[str, Any]] = []
        log_rows: list[SyncOpLog] = []
        with connection.cursor() as cursor:
            for op in ops:
                key = str(op.op_uuid)
                if key in saved:
                    # Idempotencia: el resultado guardado, sin re-aplicar nada.
                    results.append(saved[key])
                    continue

                outcome = _process(
                    ctx,
                    cursor=cursor,
                    op=op,
                    parsed=parsed[key],
                    references=references,
                    allocator=allocator,
                    now=now,
                )
                results.append(outcome)
                saved[key] = outcome  # un `op_uuid` repetido en el mismo lote

                entity_id = outcome["entity_id"]
                if entity_id is not None:
                    log_rows.append(
                        _log_row(
                            ctx,
                            op_uuid=op.op_uuid,
                            device_id=device_id,
                            batch_uuid=batch_uuid,
                            entity=op.entity,
                            entity_id=uuid.UUID(entity_id),
                            result=outcome,
                        )
                    )

        if log_rows:
            # `ignore_conflicts=True`: `op_uuid` es PK **global**, así que una
            # `op_uuid` que ya exista en OTRA organización (colisión o intento
            # deliberado) haría saltar un `IntegrityError` que tumbaría el
            # lote entero con un 500. Ignorado, el peor caso es que esta
            # operación no quede registrada: su reenvío se resolverá por CAS
            # como `conflict` con la fila autoritativa, que es una respuesta
            # correcta y sin pérdida de datos.
            #
            # El `scoped_qs` es la puerta que exige el test de arquitectura;
            # en una inserción su `WHERE` no interviene, y el aislamiento lo
            # da el `org_id = ctx.org_id` que `_log_row` escribe en cada fila.
            scoped_qs(ctx, SyncOpLog).bulk_create(log_rows, ignore_conflicts=True)

    return results


def _process(
    ctx: RequestContext,
    *,
    cursor: Any,
    op: RawSyncOp,
    parsed: VisitWrite | Rejection,
    references: _References,
    allocator: ChangeSeqAllocator,
    now: datetime,
) -> dict[str, Any]:
    """Resuelve **una** operación ya validada. Cero queries de permisos."""
    if isinstance(parsed, Rejection):
        return _rejected_result(op, parsed)

    missing = references.missing(parsed.fields)
    if missing is not None:
        return _rejected_result(
            op,
            Rejection(
                code=CODE_NOT_FOUND,
                message=f"Referenced {missing} does not exist in this organization.",
                details={"field": missing, "reason": "unknown_reference"},
                entity_id=parsed.entity_id,
            ),
        )

    # `take()` antes de saber si el CAS va a coincidir: el número tiene que
    # viajar dentro de la propia sentencia. Un conflicto deja por tanto un
    # hueco en la secuencia, y es inofensivo: el cursor de pull avanza con
    # `change_seq > cursor`, así que le afectan los duplicados y el desorden,
    # nunca los huecos.
    change_seq = allocator.take()
    if parsed.op_type == "create":
        statement = sql.build_insert(
            org_id=ctx.org_id,
            visit_id=parsed.entity_id,
            change_seq=change_seq,
            created_by=ctx.user_id,
            fields=parsed.fields,
        )
    elif parsed.base_version is None:
        # Inalcanzable: `parse_op` ya exige `base_version` en `update`/`delete`.
        # Se contesta como rechazo y **no** con un `assert`: un `assert`
        # desaparece con `python -O` y dejaría en su lugar un `TypeError` —la
        # forma servidor del hallazgo H-02/H-03 (validación que en release no
        # valida)— convertido en el 500 que este archivo no puede producir.
        return _rejected_result(
            op,
            Rejection(
                code=CODE_VALIDATION_ERROR,
                message="update and delete require base_version.",
                details={"field": "base_version", "reason": "missing"},
                entity_id=parsed.entity_id,
            ),
        )
    else:
        statement = sql.build_update(
            org_id=ctx.org_id,
            visit_id=parsed.entity_id,
            base_version=parsed.base_version,
            change_seq=change_seq,
            fields=parsed.fields,
            deleted_at=now if parsed.op_type == "delete" else None,
        )

    applied = _execute(cursor, statement)
    if applied is not None:
        return _applied_result(op, parsed.entity_id, version=applied[0])

    server_row = scoped_qs(ctx, Visit).filter(pk=parsed.entity_id).first()
    if server_row is None:
        # El CAS falló y la fila no existe **en esta organización**. O el
        # `update` apunta a una visita que nunca existió, o el `create` chocó
        # con un `id` que pertenece a otra organización. Los dos casos se
        # contestan igual y sin distinguirlos (RD-11: confirmar la existencia
        # de una fila ajena sería el oráculo de enumeración de H-16), y como
        # `rejected` y no como `conflict`: el contrato exige `server_row` en
        # todo conflicto, y aquí no hay fila autoritativa que adjuntar.
        return _rejected_result(
            op,
            Rejection(
                code=CODE_NOT_FOUND,
                message="Target row does not exist in this organization.",
                details={"field": "payload.id", "reason": "unknown_row"},
                entity_id=parsed.entity_id,
            ),
        )
    return _conflict_result(op, server_row)
