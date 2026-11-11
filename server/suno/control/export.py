"""Libro Excel del control (costos, pieza 5): hojas "Periodos" y "Desglose".

Los nombres (tareas, ítems, ubicaciones) los escribe un usuario: todo pasa por `safe_cell`
contra la inyección de fórmulas. Montos como número (`float`, a centavos) y fechas en ISO.
"""

from __future__ import annotations

from decimal import Decimal

from suno.backoffice.export import safe_cell, to_xlsx_book

from .earned_value import BreakdownRow, ControlResult

PERIOD_HEADER = [
    "periodo", "desde", "hasta", "pv_periodo", "pv_acumulado",
    "ev_periodo", "ev_acumulado", "sv", "spi",
]  # fmt: skip
BREAKDOWN_HEADER = ["desglose", "nombre", "bac", "pv", "ev", "sv", "spi", "estado"]


def _num(value: Decimal | None) -> float | None:
    """`to_xlsx` solo conoce int y float."""
    return None if value is None else float(value)


def _breakdown(kind: str, row: BreakdownRow) -> list[object]:
    measure = row.measure
    return [
        safe_cell(value)
        for value in (
            kind, row.label, _num(row.bac), _num(measure.pv), _num(measure.ev),
            _num(measure.sv), _num(measure.spi), measure.status_label,
        )
    ]  # fmt: skip


def control_workbook(result: ControlResult) -> bytes:
    periods = [
        [
            safe_cell(value)
            for value in (
                row.period.label,
                row.period.start.isoformat(),
                row.period.end.isoformat(),
                _num(row.pv),
                _num(row.pv_cum),
                _num(row.ev),
                _num(row.ev_cum),
                _num(row.sv),
                _num(row.spi),
            )
        ]  # fmt: skip
        for row in result.periods
    ]
    total = BreakdownRow("Proyecto", result.indicators.bac, result.indicators.at_cut)
    breakdown = [_breakdown("proyecto", total)]
    for kind, rows in (
        ("capítulo", result.by_chapter),
        ("tarea", result.by_task),
        ("ubicación", result.by_location),
    ):
        breakdown.extend(_breakdown(kind, row) for row in rows)
    return to_xlsx_book(
        [("Periodos", PERIOD_HEADER, periods), ("Desglose", BREAKDOWN_HEADER, breakdown)]
    )
