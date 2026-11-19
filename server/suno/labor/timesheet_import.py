"""Importar tareo a una planilla en borrador (costos, pieza 6d, spec 2.5).

Hoja "Tareo" con el encabezado en la fila 1: Tipo de documento, Número de documento, Fecha,
Destino ("Indirecto", "2.1", "Capítulo 2" o "2.1, 2.2"), Tarea (número de esquema, opcional) y
Fracción. La vista previa valida cada fila (trabajador del registro, fecha del periodo,
destino, tarea y fracción) sin guardar; al aplicar se valida todo otra vez, con las
asignaciones y el tope (`timesheets.import_lines`), y una línea igual a una existente se suma.
Todo o nada, incluido el nonce.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction

from suno.backoffice.export import to_xlsx_book
from suno.budgets.excel import ExcelRejected, preview_sheet, read_rows
from suno.core.context import RequestContext
from suno.core.models import LaborImportKind, Project, Task, Timesheet, Worker
from suno.core.policies import can_update_labor
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.materials.common import lock_project, locked

from . import imports, timesheets
from .calc import fraction_ok
from .common import MAX_ERRORS, OPEN_STATES, LaborRejected
from .registry import clean_document

SHEET = "Tareo"
H_TYPE, H_NUMBER, H_DAY = "Tipo de documento", "Número de documento", "Fecha"
H_DESTINATION, H_TASK, H_FRACTION = "Destino", "Tarea", "Fracción"
HEADER = [H_TYPE, H_NUMBER, H_DAY, H_DESTINATION, H_TASK, H_FRACTION]
REQUIRED = (H_TYPE, H_NUMBER, H_DAY, H_FRACTION)
MAX_ROWS = 5000
SALT = "suno.labor.timesheet"


@dataclass(frozen=True)
class TareoRow:
    line: int
    worker_id: UUID
    worker: str
    day: date
    destination: str
    task: str
    fraction: Decimal


@dataclass(frozen=True)
class TareoPreview:
    rows: tuple[TareoRow, ...] = ()
    errors: tuple[str, ...] = ()
    token: str = ""


def template_bytes() -> bytes:
    return to_xlsx_book([(SHEET, HEADER, [])])


def _require_editable(ctx: RequestContext, sheet: Timesheet) -> None:
    if not can_update_labor(ctx, sheet):
        raise PermissionDenied
    if sheet.status not in OPEN_STATES:
        raise LaborRejected(["Solo se importa a una planilla en borrador o rechazada."])


def _parse(
    raw: list[Any],
    sheet: Timesheet,
    workers: Mapping[tuple[str, str], Worker],
    catalog: Catalog,
    outlines: Mapping[str, UUID],
    tasks: Mapping[UUID, Task],
) -> tuple[tuple[Worker, date, str, str, Decimal] | None, list[str]]:
    kind, number, day_value, destination, task, fraction_value = raw
    problems: list[str] = []
    worker = workers.get(
        (imports.cell_text(kind).upper(), clean_document(imports.cell_text(number)))
    )
    if worker is None:
        problems.append("El documento no está en el registro de trabajadores.")
    day = imports.cell_day(day_value)
    if day is None:
        problems.append("La fecha no es una fecha (AAAA-MM-DD o DD/MM/AAAA).")
    elif not sheet.starts_on <= day <= sheet.ends_on:
        problems.append(
            f"La fecha está fuera de la planilla ({sheet.starts_on:%d/%m/%Y} – "
            f"{sheet.ends_on:%d/%m/%Y})."
        )
    try:
        fraction = imports.cell_decimal(fraction_value)
    except ValueError:
        fraction = None
    if not fraction_ok(fraction):
        problems.append("La fracción va de 0,0001 a 1,5 con cuatro decimales.")
    text, task_text = imports.cell_text(destination), imports.cell_text(task)
    data, error = timesheets.read_destination(text, task_text, catalog, outlines)
    if data is None:
        problems.append(error)
    if problems or worker is None or day is None or fraction is None:
        return None, problems
    return (worker, day, text, task_text, fraction), []


def preview(
    ctx: RequestContext,
    project: Project,
    sheet: Timesheet,
    data: bytes,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> TareoPreview:
    _require_editable(ctx, sheet)
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = tasks if tasks is not None else assignable_tasks(ctx, project)
    try:
        name = preview_sheet(data, SHEET, rows=1).sheet
        table = read_rows(data, name, max_rows=MAX_ROWS + 1)
    except ExcelRejected as exc:
        return TareoPreview(errors=(str(exc),))
    if not table:
        return TareoPreview(errors=("El archivo está vacío.",))
    found, missing = imports.columns(table[0], HEADER, REQUIRED)
    if missing:
        return TareoPreview(errors=tuple(missing))
    workers = {(w.document_type, w.document_number): w for w in scoped_qs(ctx, Worker)}
    outlines = timesheets.outline_index(tasks)
    rows: list[TareoRow] = []
    errors: list[str] = []
    for line, values in enumerate(table[1:], start=2):
        raw = imports.row_texts(values, found, HEADER)
        if not any(imports.cell_text(value) for value in raw):
            continue
        parsed, problems = _parse(raw, sheet, workers, catalog, outlines, tasks)
        if parsed is None:
            errors.extend(f"Fila {line}: {problem}" for problem in problems)
        else:
            worker, day, text, task_text, fraction = parsed
            rows.append(TareoRow(line, worker.pk, worker.label, day, text, task_text, fraction))
        if len(errors) >= MAX_ERRORS:
            errors.append(imports.MORE_ERRORS)
            break
    if not rows and not errors:
        errors.append("El archivo no tiene filas de tareo.")
    if errors:
        return TareoPreview(rows=tuple(rows), errors=tuple(errors))
    body = {
        "timesheet": str(sheet.pk),
        "rows": [
            [r.line, str(r.worker_id), r.day.isoformat(), r.destination, r.task, str(r.fraction)]
            for r in rows
        ],
    }
    return TareoPreview(rows=tuple(rows), token=imports.sign(ctx, SALT, body))


def apply(
    ctx: RequestContext,
    project: Project,
    timesheet_id: UUID,
    token: str,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> int:
    payload = imports.load(ctx, SALT, token)
    if payload.get("timesheet") != str(timesheet_id):
        raise LaborRejected([imports.INVALID])
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = tasks if tasks is not None else assignable_tasks(ctx, project)
    outlines = timesheets.outline_index(tasks)
    lines: list[timesheets.LineData] = []
    errors: list[str] = []
    for line, worker_id, day, text, task_text, fraction in payload.get("rows", []):
        data, error = timesheets.read_destination(text, task_text, catalog, outlines)
        if data is None:
            errors.append(f"Fila {line}: {error}")
            continue
        lines.append(timesheets.LineData(UUID(worker_id), date.fromisoformat(day), data,
                                         Decimal(fraction)))  # fmt: skip
    if errors or not lines:
        raise LaborRejected(errors[:MAX_ERRORS] or ["La vista previa no tiene filas."])
    # Orden de bloqueo proyecto → planilla (ADR-0035); `import_lines` vuelve a pedir los mismos
    # bloqueos dentro de la misma transacción, lo que en PostgreSQL no espera.
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        _require_editable(ctx, sheet)
        imports.consume(ctx, str(LaborImportKind.TIMESHEET), payload["nonce"])
        timesheets.import_lines(ctx, project, timesheet_id, lines, catalog=catalog, tasks=tasks)
    return len(lines)
