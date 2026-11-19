"""Importar trabajadores desde Excel (costos, pieza 6d, spec 3.1; decisión 8 del plan).

Hoja "Trabajadores" con el encabezado en la fila 1: Tipo de documento, Número de documento,
Nombre, Cargo, Fecha de ingreso, Jornal y Jornal desde (opcional). Un documento nuevo crea al
trabajador con su jornal desde el ingreso; uno existente actualiza nombre y cargo y, si el
jornal difiere del vigente hoy, agrega una fila desde "Jornal desde" (sin ella, desde hoy).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from suno.backoffice.export import to_xlsx_book
from suno.budgets.excel import ExcelRejected, preview_sheet, read_rows
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import LaborImportKind, TimesheetLine, Worker, WorkerWage
from suno.core.policies import can_manage_labor_registry
from suno.core.scoping import scoped_qs

from . import imports
from .calc import History
from .common import FROZEN_STATES, MAX_ERRORS, LaborRejected
from .registry import WorkerData, clean_document, frozen_message, wage_ok, worker_errors

SHEET = "Trabajadores"
H_TYPE, H_NUMBER, H_NAME, H_POSITION = "Tipo de documento", "Número de documento", "Nombre", "Cargo"
H_HIRED, H_WAGE, H_FROM = "Fecha de ingreso", "Jornal", "Jornal desde"
HEADER = [H_TYPE, H_NUMBER, H_NAME, H_POSITION, H_HIRED, H_WAGE, H_FROM]
REQUIRED = (H_TYPE, H_NUMBER, H_NAME, H_HIRED, H_WAGE)
MAX_ROWS = 2000
SALT = "suno.labor.workers"
NEW, UPDATE, SAME = "nuevo", "actualiza", "sin cambios"


@dataclass(frozen=True)
class WorkerRow:
    line: int
    data: WorkerData
    wage: Decimal
    wage_from: date | None
    action: str


@dataclass(frozen=True)
class WorkerPreview:
    rows: tuple[WorkerRow, ...] = ()
    errors: tuple[str, ...] = ()
    token: str = ""

    def count(self, action: str) -> int:
        return sum(1 for row in self.rows if row.action == action)


def template_bytes() -> bytes:
    return to_xlsx_book([(SHEET, HEADER, [])])


def _require_manager(ctx: RequestContext) -> None:
    if not can_manage_labor_registry(ctx, ctx.org_id):
        raise PermissionDenied


def _existing(ctx: RequestContext) -> dict[tuple[str, str], Worker]:
    return {(w.document_type, w.document_number): w for w in scoped_qs(ctx, Worker)}


def _wages(ctx: RequestContext) -> dict[Any, History]:
    rows: dict[Any, list[Any]] = {}
    for worker_id, valid_from, wage in scoped_qs(ctx, WorkerWage).values_list(
        "worker_id", "valid_from", "daily_wage"
    ):
        rows.setdefault(worker_id, []).append((valid_from, wage))
    return {key: History.of(value) for key, value in rows.items()}


def _frozen(ctx: RequestContext) -> dict[Any, date]:
    """El último día congelado (planillas aprobadas o anuladas) de cada trabajador."""
    return dict(
        scoped_qs(ctx, TimesheetLine)
        .filter(timesheet__status__in=FROZEN_STATES)
        .values("worker_id")
        .annotate(last=Max("worked_on"))
        .values_list("worker_id", "last")
    )


def _classify(
    row: WorkerRow,
    current: Worker | None,
    wages: dict[Any, History],
    frozen: dict[Any, date],
) -> tuple[str, str]:
    if current is None:
        return NEW, ""
    today = timezone.localdate()
    wage_from = row.wage_from or today
    same_wage = wages.get(current.pk, History()).on(wage_from) == row.wage
    if not same_wage and wage_from < current.hired_on:
        return "", "El jornal no puede regir antes del ingreso."
    last = frozen.get(current.pk)
    if not same_wage and last is not None and wage_from <= last:
        # Decisión 7 del plan, como `registry._require_after_frozen`.
        return "", frozen_message(last)
    same = (current.full_name, current.position) == (row.data.full_name.strip(),
                                                     row.data.position.strip())  # fmt: skip
    return (SAME if same and same_wage else UPDATE), ""


def _parse(line: int, raw: list[Any]) -> tuple[WorkerRow | None, list[str]]:
    kind, number, name, position, hired, wage_text, wage_from = raw
    problems: list[str] = []
    hired_on = imports.cell_day(hired)
    if hired_on is None:
        problems.append("La fecha de ingreso no es una fecha (AAAA-MM-DD o DD/MM/AAAA).")
    try:
        wage = imports.cell_decimal(wage_text)
    except ValueError:
        wage = None
    if not wage_ok(wage):
        problems.append("El jornal va en pesos, mayor que cero y a centavos.")
    starts = imports.cell_day(wage_from)
    if imports.cell_text(wage_from) and starts is None:
        problems.append("«Jornal desde» no es una fecha.")
    data = WorkerData(
        document_type=imports.cell_text(kind).upper(),
        document_number=imports.cell_text(number),
        full_name=imports.cell_text(name),
        position=imports.cell_text(position),
        hired_on=hired_on or date(2000, 1, 1),
    )
    problems += (
        worker_errors(data)
        if hired_on is not None
        else [p for p in worker_errors(data) if "ingreso" not in p]
    )
    if problems or wage is None:
        return None, problems
    return WorkerRow(line, data, wage, starts, ""), []


def preview(ctx: RequestContext, data: bytes) -> WorkerPreview:
    _require_manager(ctx)
    try:
        sheet = preview_sheet(data, SHEET, rows=1).sheet
        table = read_rows(data, sheet, max_rows=MAX_ROWS + 1)
    except ExcelRejected as exc:
        return WorkerPreview(errors=(str(exc),))
    if not table:
        return WorkerPreview(errors=("El archivo está vacío.",))
    found, missing = imports.columns(table[0], HEADER, REQUIRED)
    if missing:
        return WorkerPreview(errors=tuple(missing))
    existing, wages, frozen = _existing(ctx), _wages(ctx), _frozen(ctx)
    rows: list[WorkerRow] = []
    errors: list[str] = []
    seen: dict[tuple[str, str], int] = {}
    for line, values in enumerate(table[1:], start=2):
        raw = imports.row_texts(values, found, HEADER)
        if not any(imports.cell_text(value) for value in raw):
            continue
        row, problems = _parse(line, raw)
        if row is not None:
            key = (row.data.document_type, clean_document(row.data.document_number))
            if key in seen:
                problems.append(f"El documento se repite (fila {seen[key]}).")
            seen.setdefault(key, line)
            action, problem = _classify(row, existing.get(key), wages, frozen)
            if problem:
                problems.append(problem)
            row = WorkerRow(row.line, row.data, row.wage, row.wage_from, action)
        else:
            number = clean_document(imports.cell_text(raw[1]))
            key = (imports.cell_text(raw[0]).upper(), number)
            if number and key in seen:
                problems.append(f"El documento se repite (fila {seen[key]}).")
            seen.setdefault(key, line)
        if problems:
            errors.extend(f"Fila {line}: {problem}" for problem in problems)
        elif row is not None:
            rows.append(row)
        if len(errors) >= MAX_ERRORS:
            errors.append(imports.MORE_ERRORS)
            break
    if not rows and not errors:
        errors.append("El archivo no tiene filas de trabajadores.")
    if errors:
        return WorkerPreview(rows=tuple(rows), errors=tuple(errors))
    body = {
        "rows": [
            [r.line, r.data.document_type, r.data.document_number, r.data.full_name,
             r.data.position, r.data.hired_on.isoformat(), str(r.wage),
             r.wage_from.isoformat() if r.wage_from else ""]
            for r in rows
        ]
    }  # fmt: skip
    return WorkerPreview(rows=tuple(rows), token=imports.sign(ctx, SALT, body))


def apply(ctx: RequestContext, token: str) -> tuple[int, int]:
    """(creados, actualizados). Todo o nada; los trabajadores existentes se bloquean en orden de
    `pk` (ADR-0035)."""
    _require_manager(ctx)
    payload = imports.load(ctx, SALT, token)
    parsed = [
        WorkerRow(int(line), WorkerData(kind, number, name, position, date.fromisoformat(hired)),
                  Decimal(wage), date.fromisoformat(since) if since else None, "")
        for line, kind, number, name, position, hired, wage, since in payload.get("rows", [])
    ]  # fmt: skip
    if not parsed:
        raise LaborRejected(["La vista previa no tiene filas."])
    today = timezone.localdate()
    with transaction.atomic():
        imports.consume(ctx, str(LaborImportKind.WORKERS), payload["nonce"])
        existing = {
            (w.document_type, w.document_number): w
            for w in scoped_qs(ctx, Worker).select_for_update(no_key=True).order_by("pk")
        }
        wages, frozen = _wages(ctx), _frozen(ctx)
        created: list[Worker] = []
        changed: list[Worker] = []
        new_wages: list[WorkerWage] = []
        problems: list[str] = []
        for row in parsed:
            key = (row.data.document_type, clean_document(row.data.document_number))
            current = existing.get(key)
            errors = worker_errors(row.data)
            action, problem = _classify(row, current, wages, frozen)
            if problem:
                errors.append(problem)
            if errors:
                problems += [f"Fila {row.line}: {error}" for error in errors]
                continue
            if current is None:
                worker = Worker(
                    org_id=ctx.org_id, document_type=key[0], document_number=key[1],
                    full_name=row.data.full_name.strip(), position=row.data.position.strip(),
                    hired_on=row.data.hired_on,
                )  # fmt: skip
                created.append(worker)
                new_wages.append(WorkerWage(org_id=ctx.org_id, worker=worker,
                                            valid_from=row.data.hired_on, daily_wage=row.wage,
                                            created_by_id=ctx.user_id))  # fmt: skip
            elif action == UPDATE:
                current.full_name = row.data.full_name.strip()
                current.position = row.data.position.strip()
                changed.append(current)
                since = row.wage_from or today
                if wages.get(current.pk, History()).on(since) != row.wage:
                    new_wages.append(WorkerWage(org_id=ctx.org_id, worker=current,
                                                valid_from=since, daily_wage=row.wage,
                                                created_by_id=ctx.user_id))  # fmt: skip
        if problems:
            raise LaborRejected(problems[:MAX_ERRORS])
        scoped_qs(ctx, Worker).bulk_create(created)
        scoped_qs(ctx, Worker).bulk_update(changed, ["full_name", "position"])
        clash = {(w.worker_id, w.valid_from) for w in new_wages} & set(
            scoped_qs(ctx, WorkerWage)
            .filter(worker_id__in=[w.worker_id for w in new_wages])
            .values_list("worker_id", "valid_from")
        )
        if clash:
            raise LaborRejected(["Ya hay un jornal con esa fecha: cambia «Jornal desde»."])
        scoped_qs(ctx, WorkerWage).bulk_create(new_wages)
        audit.record(
            ctx,
            action=AuditAction.WORKERS_IMPORTED,
            target=f"organization:{ctx.org_id}",
            metadata={"created": len(created), "updated": len(changed)},
        )
    return len(created), len(changed)
