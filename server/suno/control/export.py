"""Libro Excel del control (costos, piezas 5 y 6a): "Periodos", "Desglose" e "Indicadores".

Los nombres (tareas, ítems, ubicaciones) los escribe un usuario: todo pasa por `safe_cell`
contra la inyección de fórmulas. Montos como número (`float`, a centavos) y fechas en ISO.
Las columnas y filas de costo solo salen si el resultado trae costo (quien lee costos).
"""

from __future__ import annotations

from decimal import Decimal

from suno.backoffice.export import safe_cell, to_xlsx_book

from .actual_cost import CostMeasure
from .earned_value import BreakdownRow, ControlResult

PERIOD_HEADER = [
    "periodo", "desde", "hasta", "pv_periodo", "pv_acumulado",
    "ev_periodo", "ev_acumulado", "sv", "spi",
]  # fmt: skip
COST_PERIOD_HEADER = ["ac_periodo", "ac_acumulado"]
BREAKDOWN_HEADER = ["desglose", "nombre", "bac", "pv", "ev", "sv", "spi", "estado"]
COST_BREAKDOWN_HEADER = ["ac", "cv", "cpi", "estado_costo", "indice_del_grupo"]
INDICATOR_HEADER = ["indicador", "valor"]


def _num(value: Decimal | None) -> float | None:
    """`to_xlsx` solo conoce int y float."""
    return None if value is None else float(value)


def _breakdown(kind: str, row: BreakdownRow, with_cost: bool) -> list[object]:
    measure = row.measure
    cells = [
        kind, row.label, _num(row.bac), _num(measure.pv), _num(measure.ev),
        _num(measure.sv), _num(measure.spi), measure.status_label,
    ]  # fmt: skip
    if with_cost:
        cost = row.cost
        cells += (
            [_num(cost.ac), _num(cost.cv), _num(cost.cpi), cost.status_label, cost.group]
            if cost is not None
            else [None, None, None, "", ""]
        )
    return [safe_cell(value) for value in cells]


def _indicators(result: ControlResult) -> list[list[object]]:
    indicators, at_cut = result.indicators, result.indicators.at_cut
    rows: list[tuple[str, Decimal | None]] = [
        ("BAC vigente", indicators.bac),
        ("Valor planeado (PV)", at_cut.pv),
        ("Valor ganado (EV)", at_cut.ev),
        ("Variación del cronograma (SV)", at_cut.sv),
        ("SPI", at_cut.spi),
    ]
    cost = result.cost
    if cost is not None:
        rows += [
            ("Costo real directo (AC)", cost.ac_direct),
            ("AC asignado", cost.ac_assigned),
            ("AC sin asignar", cost.ac_unassigned),
            ("AC indirecto", cost.ac_indirect),
            ("Variación del costo (CV)", cost.cv),
            ("CPI", cost.cpi),
            ("Estimado al terminar (EAC)", cost.eac),
            ("Variación al terminar (VAC)", cost.vac),
            ("TCPI", cost.tcpi),
            ("Administración ganada", cost.admin_earned),
            ("CPI de administración", cost.admin_cpi),
            ("CPI total", cost.total_cpi),
        ]
    return [[safe_cell(label), safe_cell(_num(value))] for label, value in rows]


def control_workbook(result: ControlResult) -> bytes:
    with_cost = result.cost is not None
    periods = []
    for row in result.periods:
        cells = [
            row.period.label, row.period.start.isoformat(), row.period.end.isoformat(),
            _num(row.pv), _num(row.pv_cum), _num(row.ev), _num(row.ev_cum),
            _num(row.sv), _num(row.spi),
        ]  # fmt: skip
        if with_cost:
            cells += [_num(row.ac), _num(row.ac_cum)]
        periods.append([safe_cell(value) for value in cells])
    total = BreakdownRow(
        "Proyecto",
        result.indicators.bac,
        result.indicators.at_cut,
        cost=CostMeasure(result.cost.ac_direct, result.cost.ev) if result.cost else None,
    )
    breakdown = [_breakdown("proyecto", total, with_cost)]
    for kind, rows in (
        ("capítulo", result.by_chapter),
        ("ítem", result.by_item),
        ("tarea", result.by_task),
        ("ubicación", result.by_location),
    ):
        breakdown.extend(_breakdown(kind, row, with_cost) for row in rows)
    return to_xlsx_book(
        [
            ("Periodos", PERIOD_HEADER + (COST_PERIOD_HEADER if with_cost else []), periods),
            (
                "Desglose",
                BREAKDOWN_HEADER + (COST_BREAKDOWN_HEADER if with_cost else []),
                breakdown,
            ),
            ("Indicadores", INDICATOR_HEADER, _indicators(result)),
        ]
    )
