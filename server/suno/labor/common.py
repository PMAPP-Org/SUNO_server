"""Piezas compartidas de los servicios de mano de obra (costos, pieza 6d).

- Orden de bloqueo (ADR-0035, decisión 16 del plan): la fila del proyecto
  (`materials.common.lock_project`, `FOR NO KEY UPDATE`), la planilla (`FOR UPDATE`), las filas
  de los trabajadores en orden de `pk` (`FOR NO KEY UPDATE`) y, al aprobar, las del factor.
  Cambiar un jornal bloquea solo la fila del trabajador; cambiar el factor, las del factor. Así
  dos aprobaciones en obras distintas no dejan a nadie por encima del tope y no hay ciclos.
- Nadie aprueba ni rechaza lo que creó o editó por última vez (también la base lo exige).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied

from suno.attachments.web import Upload, UploadRejected, store_upload
from suno.core.context import RequestContext
from suno.core.models import BenefitFactor, Blob, TimesheetStatus, Worker, WorkerWage
from suno.core.scoping import scoped_qs

from .calc import History

MAX_NOTE = 500
MAX_NAME = 200
MAX_POSITION = 80
MAX_FILE_NAME = 200
MAX_DESCRIPTION = 300
MAX_ERRORS = 50
#: Filas (trabajador y destino) que acepta un formulario de la cuadrícula (decisión 11 del plan).
MAX_GRID_ROWS = 250


def too_many_rows(limit: int) -> str:
    return (
        f"La cuadrícula tiene más de {limit} filas (trabajador y destino): no se guarda para no "
        "perder datos. Carga el tareo desde Excel o parte la planilla en periodos más cortos."
    )


STALE = "La planilla cambió desde que la abriste; recarga."
SELF_DECISION = "Nadie aprueba ni rechaza lo que creó o editó: pídeselo a otra persona."
#: Las planillas que se editan (un rechazado, solo su autor) y las que cuentan para el tope.
OPEN_STATES = (str(TimesheetStatus.DRAFT), str(TimesheetStatus.REJECTED))
COUNTED_STATES = (*OPEN_STATES, str(TimesheetStatus.APPROVED))
#: Las que congelaron jornal y factor (decisión 7 del plan).
FROZEN_STATES = (str(TimesheetStatus.APPROVED), str(TimesheetStatus.REVERSED))


class LaborRejected(Exception):
    """No se puede guardar; `errors` son mensajes para el usuario."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__(self.errors[0] if self.errors else "Rechazado")


def lock_workers(ctx: RequestContext, worker_ids: Iterable[UUID]) -> dict[UUID, Worker]:
    """Bloquea las filas de los trabajadores en orden de `pk` y las devuelve."""
    ids = list(set(worker_ids))
    if not ids:
        return {}
    rows = scoped_qs(ctx, Worker).select_for_update(no_key=True).filter(pk__in=ids).order_by("pk")
    return {worker.pk: worker for worker in rows}


def lock_factors(ctx: RequestContext) -> None:
    """Bloquea las filas del factor de la organización en orden de `pk`."""
    list(
        scoped_qs(ctx, BenefitFactor)
        .select_for_update(no_key=True)
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def wage_histories(ctx: RequestContext, worker_ids: Iterable[UUID]) -> dict[UUID, History]:
    rows: dict[UUID, list[Any]] = defaultdict(list)
    for worker_id, valid_from, wage in (
        scoped_qs(ctx, WorkerWage)
        .filter(worker_id__in=list(set(worker_ids)))
        .values_list("worker_id", "valid_from", "daily_wage")
    ):
        rows[worker_id].append((valid_from, wage))
    return {worker_id: History.of(found) for worker_id, found in rows.items()}


def factor_history(ctx: RequestContext) -> History:
    return History.of(scoped_qs(ctx, BenefitFactor).values_list("valid_from", "factor"))


def clean_note(note: str, what: str) -> str:
    text = note.strip()
    if not text:
        raise LaborRejected([f"Escribe el motivo {what}."])
    if len(text) > MAX_NOTE:
        raise LaborRejected([f"El motivo pasa de {MAX_NOTE} caracteres."])
    return text


def require_editable(
    ctx: RequestContext, obj: Any, policy: Callable[[RequestContext, Any], bool]
) -> None:
    """Un borrador o un rechazado; un rechazado, solo su autor."""
    if obj.status not in OPEN_STATES:
        raise LaborRejected(["Solo se edita o se borra una planilla en borrador o rechazada."])
    if obj.status == TimesheetStatus.REJECTED and obj.created_by_id != ctx.user_id:
        raise PermissionDenied
    if not policy(ctx, obj):
        raise PermissionDenied


def require_version(sheet: Any, expected: int | None) -> None:
    """Control de concurrencia optimista: la versión que vio quien envía (`None`: sin
    comprobar, para los servicios internos). Se llama con la planilla bloqueada."""
    if expected is not None and sheet.version != expected:
        raise LaborRejected([STALE])


def require_decidable(ctx: RequestContext, obj: Any) -> None:
    if obj.status != TimesheetStatus.DRAFT:
        raise LaborRejected(["Solo se aprueba o se rechaza una planilla en borrador."])
    if ctx.user_id in (obj.created_by_id, obj.updated_by_id):
        raise LaborRejected([SELF_DECISION])


def stored_upload(upload: Upload | None) -> Blob | None:
    """Sube el soporte antes de la transacción: no se tiene el bloqueo mientras se escribe."""
    if upload is None:
        return None
    try:
        return store_upload(upload)
    except UploadRejected as exc:
        raise LaborRejected([str(exc)]) from exc
