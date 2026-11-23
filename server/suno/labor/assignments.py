"""Asignaciones de trabajadores al proyecto (costos, pieza 6d, spec 1.2; decisión 9 del plan).

- Crear pide `can_create_labor`; editar y borrar, `can_update_labor`/`can_delete_labor` (el
  Colaborador, solo las suyas).
- Dentro del vínculo (ingreso ≤ inicio; fin ≤ retiro) y sin cruces en el mismo proyecto: se
  comprueba con el proyecto bloqueado (`lock_project`).
- Editar o borrar no deja fuera días ya tareados en planillas no anuladas del proyecto.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404

from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import Project, ProjectWorker, TimesheetLine, TimesheetStatus, Worker
from suno.core.models.labor import mask_document
from suno.core.policies import can_create_labor, can_delete_labor, can_update_labor
from suno.core.scoping import scoped_qs
from suno.materials.common import lock_project, locked

from .common import LaborRejected


@dataclass(frozen=True)
class AssignmentData:
    worker_id: UUID
    starts_on: date
    ends_on: date | None


def _worker(ctx: RequestContext, worker_id: UUID) -> Worker:
    """La fila del trabajador, bloqueada (después del proyecto, ADR-0035): un retiro a la vez
    espera a que se guarde la asignación y la cierra, o la asignación ve el retiro."""
    found = scoped_qs(ctx, Worker).select_for_update(no_key=True).filter(pk=worker_id).first()
    if found is None:
        raise LaborRejected(["Elige un trabajador del registro."])
    return found


def _check(
    ctx: RequestContext, project: Project, data: AssignmentData, exclude: UUID | None = None
) -> Worker:
    worker = _worker(ctx, data.worker_id)
    errors: list[str] = []
    if data.ends_on is not None and data.ends_on < data.starts_on:
        errors.append("La asignación termina antes de empezar.")
    if data.starts_on < worker.hired_on:
        errors.append(f"No puede empezar antes del ingreso ({worker.hired_on:%d/%m/%Y}).")
    if worker.retired_on is not None and (data.ends_on is None or data.ends_on > worker.retired_on):
        errors.append(f"Termina después del retiro ({worker.retired_on:%d/%m/%Y}).")
    others = (
        scoped_qs(ctx, ProjectWorker)
        .filter(project=project, worker=worker)
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=data.starts_on))
    )
    if data.ends_on is not None:
        others = others.filter(starts_on__lte=data.ends_on)
    if exclude is not None:
        others = others.exclude(pk=exclude)
    clash = others.first()
    if clash is not None:
        until = f"{clash.ends_on:%d/%m/%Y}" if clash.ends_on else "sin fin"
        errors.append(f"Se cruza con la asignación del {clash.starts_on:%d/%m/%Y} ({until}).")
    if errors:
        raise LaborRejected(errors)
    return worker


def _outside(
    ctx: RequestContext, project: Project, row: ProjectWorker, data: AssignmentData | None
) -> date | None:
    """El primer día tareado (planillas no anuladas del proyecto) que la asignación cubría y la
    nueva (o ninguna, al borrar) ya no cubre."""
    lines = (
        scoped_qs(ctx, TimesheetLine)
        .filter(timesheet__project=project, worker_id=row.worker_id, worked_on__gte=row.starts_on)
        .exclude(timesheet__status=TimesheetStatus.REVERSED)
    )
    if row.ends_on is not None:
        lines = lines.filter(worked_on__lte=row.ends_on)
    if data is not None:
        keep = Q(worked_on__gte=data.starts_on)
        if data.ends_on is not None:
            keep &= Q(worked_on__lte=data.ends_on)
        lines = lines.exclude(keep)
    return lines.order_by("worked_on").values_list("worked_on", flat=True).first()


def _metadata(row: ProjectWorker, worker: Worker, change: str) -> dict[str, Any]:
    return {
        "project": str(row.project_id),
        "assignment": str(row.pk),
        "worker": str(worker.pk),
        "document": f"{worker.document_type} {mask_document(worker.document_number)}",
        "change": change,
        "starts_on": row.starts_on.isoformat(),
        "ends_on": row.ends_on.isoformat() if row.ends_on else "",
    }


def create_assignment(ctx: RequestContext, project: Project, data: AssignmentData) -> ProjectWorker:
    if not can_create_labor(ctx, project):
        raise PermissionDenied
    with transaction.atomic():
        lock_project(ctx, project)
        worker = _check(ctx, project, data)
        row = scoped_qs(ctx, ProjectWorker).create(
            org_id=ctx.org_id, project=project, worker=worker, starts_on=data.starts_on,
            ends_on=data.ends_on, created_by_id=ctx.user_id, updated_by_id=ctx.user_id,
        )  # fmt: skip
        audit.record(
            ctx,
            action=AuditAction.LABOR_ASSIGNMENT_CHANGED,
            target=f"project_worker:{row.pk}",
            metadata=_metadata(row, worker, "created"),
        )
    return row


def update_assignment(
    ctx: RequestContext, project: Project, assignment_id: UUID, data: AssignmentData
) -> ProjectWorker:
    current = get_assignment(ctx, project, assignment_id)
    if data.worker_id != current.worker_id:
        raise LaborRejected(["El trabajador de una asignación no cambia: crea otra."])
    with transaction.atomic():
        # Proyecto, trabajador y asignación: el mismo orden que `retire_worker` (trabajador y
        # luego sus asignaciones), sin ciclos.
        lock_project(ctx, project)
        _worker(ctx, current.worker_id)
        row = locked(ctx, ProjectWorker, project, assignment_id)
        if not can_update_labor(ctx, row):
            raise PermissionDenied
        worker = _check(ctx, project, data, exclude=row.pk)
        day = _outside(ctx, project, row, data)
        if day is not None:
            raise LaborRejected([f"Hay tareo el {day:%d/%m/%Y} fuera de las nuevas fechas."])
        row.starts_on, row.ends_on = data.starts_on, data.ends_on
        row.updated_by_id = ctx.user_id
        row.save()
        audit.record(
            ctx,
            action=AuditAction.LABOR_ASSIGNMENT_CHANGED,
            target=f"project_worker:{row.pk}",
            metadata=_metadata(row, worker, "updated"),
        )
    return row


def delete_assignment(ctx: RequestContext, project: Project, assignment_id: UUID) -> None:
    current = get_assignment(ctx, project, assignment_id)
    with transaction.atomic():
        lock_project(ctx, project)
        worker = _worker(ctx, current.worker_id)
        row = locked(ctx, ProjectWorker, project, assignment_id)
        if not can_delete_labor(ctx, row):
            raise PermissionDenied
        day = _outside(ctx, project, row, None)
        if day is not None:
            raise LaborRejected([f"Hay tareo el {day:%d/%m/%Y} en esta asignación."])
        metadata = _metadata(row, worker, "deleted")
        row.delete()
        audit.record(ctx, action=AuditAction.LABOR_ASSIGNMENT_CHANGED,
                     target=f"project_worker:{assignment_id}", metadata=metadata)  # fmt: skip


def get_assignment(ctx: RequestContext, project: Project, assignment_id: UUID) -> ProjectWorker:
    found = scoped_qs(ctx, ProjectWorker).filter(pk=assignment_id, project=project).first()
    if found is None:
        raise Http404
    return found
