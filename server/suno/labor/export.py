"""Excel de mano de obra (costos, pieza 6d, spec 3.4): planilla y conciliación. Todo texto de
usuario pasa por `safe_cell`; valores como número y fechas en ISO; documento enmascarado."""

from __future__ import annotations

from decimal import Decimal

from suno.backoffice.export import safe_cell, to_xlsx
from suno.core.context import RequestContext
from suno.core.models import Timesheet, TimesheetLine
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog

from .payroll import Reconciliation
from .timesheets import evaluate

TIMESHEET_HEADER = [
    "trabajador", "cargo", "fecha", "destino", "fraccion", "jornal", "factor", "valor",
]  # fmt: skip
RECONCILIATION_HEADER = [
    "trabajador", "cargo", "jornales", "costo_tareado", "pagado", "diferencia", "porcentaje",
    "marca",
]  # fmt: skip


def number(value: Decimal | None) -> float | str:
    return "" if value is None else float(value)


def timesheet_workbook(ctx: RequestContext, sheet: Timesheet, catalog: Catalog) -> bytes:
    """Una fila por línea. En una aprobada o anulada, los valores congelados; en un borrador,
    el costo estimado de hoy."""
    evaluation = evaluate(ctx, sheet, catalog)
    frozen = {
        pk: (wage, factor, amount)
        for pk, wage, factor, amount in scoped_qs(ctx, TimesheetLine)
        .filter(timesheet=sheet, amount__isnull=False)
        .values_list("pk", "daily_wage", "factor", "amount")
    }
    positions = dict(
        scoped_qs(ctx, TimesheetLine).filter(timesheet=sheet)
        .values_list("worker_id", "worker__position").distinct()
    )  # fmt: skip
    rows = []
    for line, cost in zip(evaluation.lines, evaluation.costs, strict=True):
        wage, factor, amount = frozen.get(line.pk, (cost.wage, cost.factor, cost.amount))
        rows.append([
            safe_cell(evaluation.names.get(line.worker_id, "—")),
            safe_cell(positions.get(line.worker_id, "")),
            line.day.isoformat(),
            safe_cell(evaluation.labels.get(line.key, "—")),
            number(line.fraction), number(wage), number(factor), number(amount),
        ])  # fmt: skip
    return to_xlsx(TIMESHEET_HEADER, rows, sheet_name=sheet.code)


def reconciliation_workbook(result: Reconciliation) -> bytes:
    rows: list[list[object]] = [
        [safe_cell(r.label), safe_cell(r.position), number(r.days), number(r.estimated),
         number(r.paid), number(r.difference), number(r.pct), r.flag]
        for r in result.rows
    ]  # fmt: skip
    rows += [
        [safe_cell(f"Fuera del registro: {u.document} (fila {u.row})"), "", "", "", number(u.paid),
         "", "", "Documento desconocido"]
        for u in result.unknown
    ]  # fmt: skip
    return to_xlsx(RECONCILIATION_HEADER, rows, sheet_name="Conciliación")
