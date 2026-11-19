"""Planillas de tareo (costos, pieza 6d, spec 1.2, 2.2, 2.3 y 3.2).

- Una planilla cubre de 1 a 31 días, nunca en el futuro; las no anuladas de un proyecto no se
  cruzan (con el proyecto bloqueado). Número `TA-001` por proyecto, al crearla.
- Las líneas se escriben por días: guardar la semana de la cuadrícula (o el día de la vista
  móvil) reemplaza las líneas de esos días (decisión 11 del plan). Una línea igual (trabajador,
  día, destino) se suma.
- Cada día de un trabajador cae en una asignación al proyecto, y nadie pasa de 1,5 jornales
  un día entre todas las planillas no anuladas de la organización (con los trabajadores
  bloqueados en orden de `pk`, ADR-0035).
- Guardar es editar: un rechazado (solo su autor) vuelve a borrador y se quita el soporte
  firmado. El resumen (`summary`) se guarda en cada edición; "Recalcular el resumen" también es
  una edición. Subir el soporte no es editar.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.http import Http404
from django.utils import timezone

from suno.attachments.web import Upload
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    Project,
    ProjectWorker,
    Task,
    Timesheet,
    TimesheetLine,
    TimesheetLineItem,
    TimesheetStatus,
    Worker,
)
from suno.core.models.labor import MAX_FRACTION, MAX_PERIOD_DAYS
from suno.core.policies import can_create_labor, can_delete_labor, can_update_labor
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.materials.common import lock_project, locked, next_number
from suno.materials.destination import (
    DestinationData,
    check_destination,
    destination_key,
    destination_label,
    key_text,
)

from .calc import LineCost, Summary, days_text, fraction_ok, over_cap, price, summarize
from .common import (
    COUNTED_STATES,
    MAX_ERRORS,
    MAX_FILE_NAME,
    LaborRejected,
    factor_history,
    lock_workers,
    require_editable,
    stored_upload,
    wage_histories,
)

FIRST_DAY = date(2000, 1, 1)
#: 60 trabajadores × 31 días × 3 destinos son 5.580; el resto es margen.
MAX_LINES = 20_000


@dataclass(frozen=True)
class LineData:
    worker_id: UUID
    day: date
    destination: DestinationData
    fraction: Decimal


@dataclass(frozen=True)
class StoredLine:
    pk: UUID
    worker_id: UUID
    day: date
    #: `TimesheetLine.destination_key` (SHA-256 del destino).
    key: str
    scope: str
    chapter_id: UUID | None
    item_ids: tuple[UUID, ...]
    task_id: UUID | None
    task_label: str
    #: El número de esquema de la tarea ("2.1"), para volver a escribirla en la cuadrícula.
    task_outline: str
    fraction: Decimal

    @property
    def destination(self) -> DestinationData:
        return DestinationData(self.scope, self.item_ids, self.chapter_id, self.task_id)


@dataclass(frozen=True)
class Placed:
    worker_id: UUID
    day: date
    key: str
    scope: str
    chapter_id: UUID | None
    item_ids: tuple[UUID, ...]
    task_id: UUID | None
    fraction: Decimal


@dataclass(frozen=True)
class Evaluation:
    """Las líneas de una planilla con su costo de hoy (jornal y factor vigentes)."""

    lines: tuple[StoredLine, ...]
    costs: tuple[LineCost, ...]
    summary: Summary
    labels: Mapping[str, str]
    names: Mapping[UUID, str]


def hashed(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def default_period(today: date) -> tuple[date, date]:
    """La semana anterior, de lunes a domingo (spec 3.2 "Crear")."""
    monday = today - timedelta(days=today.weekday() + 7)
    return monday, monday + timedelta(days=6)


def period_days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def timesheet_metadata(sheet: Timesheet) -> dict[str, Any]:
    return {
        "project": str(sheet.project_id),
        "timesheet": str(sheet.pk),
        "number": sheet.number,
        "start": sheet.starts_on.isoformat(),
        "end": sheet.ends_on.isoformat(),
        "status": sheet.status,
        "total": str(sheet.total),
    }


def worker_names(ctx: RequestContext, worker_ids: Iterable[UUID]) -> dict[UUID, str]:
    return {w.pk: w.label for w in scoped_qs(ctx, Worker).filter(pk__in=list(set(worker_ids)))}


def load_lines(ctx: RequestContext, sheet: Timesheet) -> list[StoredLine]:
    """Las líneas con su destino. Dos consultas."""
    items: dict[UUID, list[UUID]] = defaultdict(list)
    for line_id, item_id in (
        scoped_qs(ctx, TimesheetLineItem)
        .filter(line__timesheet=sheet)
        .values_list("line_id", "item_id")
    ):
        items[line_id].append(item_id)
    return [
        StoredLine(
            pk,
            worker_id,
            day,
            key,
            scope,
            chapter_id,
            tuple(sorted(items.get(pk, ()), key=str)),
            task_id,
            f"{outline} {name}" if task_id else "",
            outline or "",
            fraction,
        )  # fmt: skip
        for pk, worker_id, day, key, scope, chapter_id, task_id, outline, name, fraction in (
            scoped_qs(ctx, TimesheetLine)
            .filter(timesheet=sheet)
            .order_by("worked_on", "worker_id")
            .values_list(
                "pk",
                "worker_id",
                "worked_on",
                "destination_key",
                "scope",
                "chapter_id",
                "task_id",
                "task__outline_number",
                "task__name",
                "fraction",
            )  # fmt: skip
        )
    ]


def labels_for(catalog: Catalog, lines: Iterable[StoredLine]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for line in lines:
        if line.key not in labels:
            labels[line.key] = destination_label(
                catalog, line.scope, line.chapter_id, line.item_ids, line.task_label
            )
    return labels


def evaluate(ctx: RequestContext, sheet: Timesheet, catalog: Catalog) -> Evaluation:
    """Cinco consultas: líneas, ítems, nombres, jornales y factor."""
    lines = load_lines(ctx, sheet)
    workers = {line.worker_id for line in lines}
    costs = price(
        [(line.worker_id, line.day, line.key, line.fraction) for line in lines],
        wage_histories(ctx, workers),
        factor_history(ctx),
    )
    return Evaluation(
        tuple(lines), tuple(costs), summarize(costs), labels_for(catalog, lines),
        worker_names(ctx, workers),
    )  # fmt: skip


def _store_summary(sheet: Timesheet, evaluation: Evaluation) -> None:
    summary = evaluation.summary
    sheet.summary = summary.snapshot(evaluation.labels)
    sheet.total = summary.total
    sheet.days = summary.days
    sheet.line_count = summary.lines


def _period_errors(
    ctx: RequestContext, project: Project, start: date, end: date, exclude: UUID | None = None
) -> list[str]:
    if end < start:
        return ["La planilla termina antes de empezar."]
    errors: list[str] = []
    if (end - start).days >= MAX_PERIOD_DAYS:
        errors.append(f"Una planilla cubre como mucho {MAX_PERIOD_DAYS} días.")
    if end > timezone.localdate():
        errors.append("La planilla no puede terminar en el futuro.")
    if start < FIRST_DAY:
        errors.append("La planilla no puede empezar antes de 2000.")
    others = (
        scoped_qs(ctx, Timesheet)
        .filter(project=project, starts_on__lte=end, ends_on__gte=start)
        .exclude(status=TimesheetStatus.REVERSED)
    )
    if exclude is not None:
        others = others.exclude(pk=exclude)
    clash = others.order_by("number").first()
    if clash is not None:
        errors.append(
            f"Se cruza con la planilla {clash.code} "
            f"({clash.starts_on:%d/%m/%Y} – {clash.ends_on:%d/%m/%Y})."
        )
    return errors


def create_timesheet(ctx: RequestContext, project: Project, start: date, end: date) -> Timesheet:
    if not can_create_labor(ctx, project):
        raise PermissionDenied
    with transaction.atomic():
        lock_project(ctx, project)
        errors = _period_errors(ctx, project, start, end)
        if errors:
            raise LaborRejected(errors)
        sheet = Timesheet(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, Timesheet, project),
            starts_on=start,
            ends_on=end,
            status=str(TimesheetStatus.DRAFT),
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
        _store_summary(sheet, Evaluation((), (), summarize([]), {}, {}))
        sheet.save()
        audit.record(
            ctx,
            action=AuditAction.TIMESHEET_CREATED,
            target=f"timesheet:{sheet.pk}",
            metadata=timesheet_metadata(sheet),
        )
    return sheet


def _coverage(
    ctx: RequestContext, project: Project, worker_ids: Iterable[UUID]
) -> dict[UUID, list[tuple[date, date | None]]]:
    found: dict[UUID, list[tuple[date, date | None]]] = defaultdict(list)
    for worker_id, start, end in (
        scoped_qs(ctx, ProjectWorker)
        .filter(project=project, worker_id__in=list(set(worker_ids)))
        .values_list("worker_id", "starts_on", "ends_on")
    ):
        found[worker_id].append((start, end))
    return found


def _covered(periods: Sequence[tuple[date, date | None]], day: date) -> bool:
    return any(start <= day and (end is None or day <= end) for start, end in periods)


def _validate(
    ctx: RequestContext,
    project: Project,
    sheet: Timesheet,
    lines: Sequence[LineData],
    replaced: frozenset[date],
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
) -> list[Placed]:
    if len(lines) > MAX_LINES:
        raise LaborRejected([f"Una planilla lleva como mucho {MAX_LINES} líneas."])
    workers = {
        w.pk: w for w in scoped_qs(ctx, Worker).filter(pk__in={line.worker_id for line in lines})
    }
    coverage = _coverage(ctx, project, workers)
    errors: list[str] = []
    merged: dict[tuple[UUID, date, str], Placed] = {}
    for line in lines:
        worker = workers.get(line.worker_id)
        if worker is None:
            errors.append("Hay un trabajador que no está en el registro.")
            continue
        who = f"{worker.label}, {line.day:%d/%m/%Y}"
        if not sheet.starts_on <= line.day <= sheet.ends_on:
            errors.append(f"{who}: el día está fuera del periodo de la planilla.")
        elif line.day not in replaced:
            errors.append(f"{who}: el día no está en lo que se guarda.")
        elif not fraction_ok(line.fraction):
            errors.append(f"{who}: la fracción va de 0,0001 a 1,5 con cuatro decimales.")
        elif not _covered(coverage.get(worker.pk, ()), line.day):
            errors.append(f"{who}: no está asignado al proyecto ese día.")
        else:
            problems, assignment = check_destination(catalog, tasks, line.destination)
            if assignment is None or problems:
                errors.append(f"{who}: {problems[0] if problems else 'el destino no es válido.'}")
            else:
                text = key_text(
                    destination_key(
                        assignment.scope,
                        assignment.chapter_id,
                        assignment.item_ids,
                        assignment.task_id,
                    )
                )
                key = (worker.pk, line.day, text)
                before = merged.get(key)
                fraction = line.fraction + (before.fraction if before else 0)
                if fraction > MAX_FRACTION:
                    errors.append(f"{who}: la misma línea suma {days_text(fraction)} jornales "
                                  "(máximo 1,5).")  # fmt: skip
                else:
                    merged[key] = Placed(
                        worker.pk, line.day, hashed(text), assignment.scope,
                        assignment.chapter_id, tuple(sorted(assignment.item_ids, key=str)),
                        assignment.task_id, fraction,
                    )  # fmt: skip
        if len(errors) >= MAX_ERRORS:
            break
    if errors:
        raise LaborRejected(errors[:MAX_ERRORS])
    return list(merged.values())


def day_totals(
    ctx: RequestContext,
    worker_ids: Iterable[UUID],
    days: Iterable[date],
    *,
    skip: Timesheet | None = None,
    skip_days: Iterable[date] = (),
) -> dict[tuple[UUID, date], Decimal]:
    """Jornales por (trabajador, día) en las planillas que cuentan (no anuladas) de toda la
    organización; `skip`/`skip_days`: las líneas que se van a reemplazar."""
    rows = scoped_qs(ctx, TimesheetLine).filter(
        worker_id__in=list(set(worker_ids)),
        worked_on__in=list(set(days)),
        timesheet__status__in=COUNTED_STATES,
    )
    if skip is not None:
        rows = rows.exclude(timesheet=skip, worked_on__in=list(skip_days))
    return {
        (worker_id, day): total
        for worker_id, day, total in rows.values("worker_id", "worked_on")
        .annotate(total=Sum("fraction"))
        .values_list("worker_id", "worked_on", "total")
    }


def cap_errors(
    ctx: RequestContext, totals: Mapping[tuple[UUID, date], Decimal], current: Timesheet
) -> list[str]:
    """Quién, qué día, cuánto suma y en qué otras planillas (número y proyecto) está el resto."""
    found = over_cap(totals)
    if not found:
        return []
    names = worker_names(ctx, {worker for worker, _, _ in found})
    elsewhere: dict[tuple[UUID, date], set[str]] = defaultdict(set)
    for worker_id, day, number, project in (
        scoped_qs(ctx, TimesheetLine)
        .filter(
            worker_id__in=[w for w, _, _ in found],
            worked_on__in=[d for _, d, _ in found],
            timesheet__status__in=COUNTED_STATES,
        )
        .exclude(timesheet=current)
        .values_list("worker_id", "worked_on", "timesheet__number", "timesheet__project__name")
    ):
        elsewhere[(worker_id, day)].add(f"TA-{number:03d} ({project})")
    errors = []
    for worker_id, day, total in found[:MAX_ERRORS]:
        text = (
            f"{names.get(worker_id, '—')} suma {days_text(total)} jornales el {day:%d/%m/%Y} "
            "(máximo 1,5)"
        )
        others = sorted(elsewhere.get((worker_id, day), ()))
        errors.append(f"{text}; el resto está en {', '.join(others)}." if others else f"{text}.")
    return errors


def _mark_edited(ctx: RequestContext, sheet: Timesheet) -> dict[str, bool]:
    flags = {
        "resubmitted": sheet.status == TimesheetStatus.REJECTED,
        "signed_removed": sheet.attachment_id is not None,
    }
    sheet.status = str(TimesheetStatus.DRAFT)
    sheet.decided_by = None
    sheet.decided_at = None
    sheet.decision_note = ""
    sheet.attachment = None
    sheet.attachment_name = ""
    sheet.updated_by_id = ctx.user_id
    return flags


def _write(ctx: RequestContext, sheet: Timesheet, placed: Sequence[Placed],
           replaced: frozenset[date]) -> None:  # fmt: skip
    scoped_qs(ctx, TimesheetLine).filter(timesheet=sheet, worked_on__in=list(replaced)).delete()
    rows = [
        TimesheetLine(
            org_id=ctx.org_id,
            timesheet=sheet,
            worker_id=p.worker_id,
            worked_on=p.day,
            fraction=p.fraction,
            scope=p.scope,
            chapter_id=p.chapter_id,
            task_id=p.task_id,
            destination_key=p.key,
        )  # fmt: skip
        for p in placed
    ]
    scoped_qs(ctx, TimesheetLine).bulk_create(rows, batch_size=2000)
    scoped_qs(ctx, TimesheetLineItem).bulk_create(
        [
            TimesheetLineItem(org_id=ctx.org_id, line=row, item_id=item_id)
            for row, p in zip(rows, placed, strict=True)
            for item_id in p.item_ids
        ],
        batch_size=2000,
    )


def _defaults(
    ctx: RequestContext,
    project: Project,
    catalog: Catalog | None,
    tasks: Mapping[UUID, Task] | None,
) -> tuple[Catalog, Mapping[UUID, Task]]:
    return (
        catalog if catalog is not None else load_catalog(ctx, project),
        tasks if tasks is not None else assignable_tasks(ctx, project),
    )


def _edit(
    ctx: RequestContext,
    project: Project,
    timesheet_id: UUID,
    build: Callable[[Timesheet], tuple[Sequence[LineData], frozenset[date]] | None],
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    action: str,
) -> Timesheet:
    """Bloquea, valida, escribe las líneas y el resumen y audita. `build` devuelve las líneas y
    los días que reemplazan, o `None` para solo recalcular el resumen."""
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        require_editable(ctx, sheet, can_update_labor)
        built = build(sheet)
        placed: list[Placed] = []
        replaced: frozenset[date] = frozenset()
        if built is not None:
            lines, replaced = built
            outside = [day for day in replaced if not sheet.starts_on <= day <= sheet.ends_on]
            if outside:
                raise LaborRejected(["Hay días fuera del periodo de la planilla."])
            placed = _validate(ctx, project, sheet, lines, replaced, catalog, tasks)
            lock_workers(ctx, {p.worker_id for p in placed})
            totals = day_totals(ctx, {p.worker_id for p in placed}, {p.day for p in placed},
                                skip=sheet, skip_days=replaced)  # fmt: skip
            for p in placed:
                totals[(p.worker_id, p.day)] = totals.get((p.worker_id, p.day), 0) + p.fraction
            errors = cap_errors(ctx, totals, sheet)
            if errors:
                raise LaborRejected(errors)
        flags = _mark_edited(ctx, sheet)
        if built is not None:
            _write(ctx, sheet, placed, replaced)
        _store_summary(sheet, evaluate(ctx, sheet, catalog))
        sheet.save()
        audit.record(
            ctx,
            action=AuditAction.TIMESHEET_IMPORTED if action == "import"
            else AuditAction.TIMESHEET_UPDATED,
            target=f"timesheet:{sheet.pk}",
            metadata={**timesheet_metadata(sheet), **flags, "change": action,
                      "days": len(replaced), "lines": len(placed)},
        )  # fmt: skip
    return sheet


def save_lines(
    ctx: RequestContext,
    project: Project,
    timesheet_id: UUID,
    lines: Sequence[LineData],
    days: Iterable[date],
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> Timesheet:
    """Reemplaza las líneas de `days` por `lines` (la semana de la cuadrícula o un día)."""
    catalog, tasks = _defaults(ctx, project, catalog, tasks)
    replaced = frozenset(days)
    return _edit(ctx, project, timesheet_id, lambda _sheet: (lines, replaced), catalog, tasks,
                 "save")  # fmt: skip


def import_lines(
    ctx: RequestContext,
    project: Project,
    timesheet_id: UUID,
    lines: Sequence[LineData],
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> Timesheet:
    """Agrega líneas a un borrador (spec 2.5): las que coinciden con una existente se suman."""
    catalog, tasks = _defaults(ctx, project, catalog, tasks)

    def build(sheet: Timesheet) -> tuple[Sequence[LineData], frozenset[date]]:
        existing = [
            LineData(line.worker_id, line.day, line.destination, line.fraction)
            for line in load_lines(ctx, sheet)
        ]
        return [*existing, *lines], frozenset(period_days(sheet.starts_on, sheet.ends_on))

    return _edit(ctx, project, timesheet_id, build, catalog, tasks, "import")


def recalculate(
    ctx: RequestContext, project: Project, timesheet_id: UUID, *, catalog: Catalog | None = None
) -> Timesheet:
    """Guarda otra vez el resumen con los jornales y el factor de hoy. Es editar."""
    catalog, tasks = _defaults(ctx, project, catalog, {})
    return _edit(ctx, project, timesheet_id, lambda _sheet: None, catalog, tasks, "recalculate")


def delete_timesheet(ctx: RequestContext, project: Project, timesheet_id: UUID) -> None:
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        require_editable(ctx, sheet, can_delete_labor)
        metadata = timesheet_metadata(sheet)
        target = f"timesheet:{sheet.pk}"
        sheet.delete()
        audit.record(ctx, action=AuditAction.TIMESHEET_DELETED, target=target, metadata=metadata)


def _require_signable(ctx: RequestContext, sheet: Timesheet) -> None:
    if sheet.status != TimesheetStatus.DRAFT:
        raise LaborRejected(["La planilla firmada se sube a una planilla en borrador."])
    if not can_update_labor(ctx, sheet):
        raise PermissionDenied


def attach_signed(
    ctx: RequestContext, project: Project, timesheet_id: UUID, upload: Upload, name: str
) -> Timesheet:
    """Sube la planilla firmada. No es editar: `updated_by` no cambia."""
    current = scoped_qs(ctx, Timesheet).filter(pk=timesheet_id, project=project).first()
    if current is None:
        raise Http404
    _require_signable(ctx, current)
    blob = stored_upload(upload)
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        _require_signable(ctx, sheet)
        sheet.attachment = blob
        sheet.attachment_name = name[:MAX_FILE_NAME]
        sheet.save(update_fields=["attachment", "attachment_name"])
        audit.record(
            ctx,
            action=AuditAction.TIMESHEET_UPDATED,
            target=f"timesheet:{sheet.pk}",
            metadata={**timesheet_metadata(sheet), "signed": True},
        )
    return sheet
