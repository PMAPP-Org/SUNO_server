"""Presupuestos de ejemplo para las pruebas (sin BD).

Totales, para no recalcularlos en cada prueba:

- v1: costo directo 9.130.000,00; capítulo 1 = 1.150.000,00; capítulo 2 = 7.980.000,00.
  Con `AIU` (10 / 5 / 5 / IVA 19 % sobre utilidad): total 11.042.735,00.
- v2 frente a v1: 1.1 mayor cantidad (100 → 120), 1.2 eliminado, 2.1 cambio de
  precio (450.000 → 470.000), 2.2 menor cantidad (800 → 700), 2.3 no previsto,
  2.4 sin cambios. Costo directo 8.690.000,00; con los mismos cargos 10.510.555,00.
- v3: la v2 con el 1.2 de vuelta.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from suno.budgets.mapping import ColumnMapping
from suno.budgets.totals import BASE_DIRECT, ChargeSpec, line_base

from .xlsx import HEADER, build_xlsx

SHEET = "Presupuesto"
MAPPING = ColumnMapping(
    sheet=SHEET,
    first_row=2,
    columns={
        "code": "A",
        "description": "B",
        "unit": "C",
        "quantity": "D",
        "unit_price": "E",
        "total": "F",
    },
)

V1_ROWS: list[list[Any]] = [
    ["1", "Preliminares"],
    ["1.1", "Localización y replanteo", "m2", 100, 1500, 150000],
    ["1.2", "Cerramiento provisional", "ml", 50, 20000, 1000000],
    ["2", "Estructura"],
    ["2.1", "Concreto zapatas", "m3", 10, 450000, 4500000],
    ["2.2", "Acero de refuerzo", "kg", 800, 4200, 3360000],
    ["2.4", "Curado", "m2", 40, 3000, 120000],
]
V2_ROWS: list[list[Any]] = [
    ["1", "Preliminares"],
    ["1.1", "Localización y replanteo", "m2", 120, 1500, 180000],
    ["2", "Estructura"],
    ["2.1", "Concreto zapatas", "m3", 10, 470000, 4700000],
    ["2.2", "Acero de refuerzo", "kg", 700, 4200, 2940000],
    ["2.3", "Formaleta", "m2", 30, 25000, 750000],
    ["2.4", "Curado", "m2", 40, 3000, 120000],
]
V3_ROWS: list[list[Any]] = [*V2_ROWS[:2], V1_ROWS[2], *V2_ROWS[2:]]
#: La v1 con el 2.1 partido: pasa a ser un capítulo con los ítems 2.1.1 y 2.1.2.
SPLIT_ROWS: list[list[Any]] = [
    *V1_ROWS[:4],
    ["2.1", "Concreto zapatas"],
    ["2.1.1", "Concreto zapatas eje A", "m3", 6, 450000, 2700000],
    ["2.1.2", "Concreto zapatas eje B", "m3", 4, 450000, 1800000],
    *V1_ROWS[5:],
]
#: El capítulo 2 de la v1 pasa a ser un ítem global (sin hijos).
MERGED_ROWS: list[list[Any]] = [
    *V1_ROWS[:3],
    ["2", "Estructura", "gl", 1, 7980000, 7980000],
]

AIU = [
    ChargeSpec(1, "Administración", Decimal("10"), BASE_DIRECT),
    ChargeSpec(2, "Imprevistos", Decimal("5"), BASE_DIRECT),
    ChargeSpec(3, "Utilidad", Decimal("5"), BASE_DIRECT),
    ChargeSpec(4, "IVA sobre utilidad", Decimal("19"), line_base(3)),
]


def xlsx(rows: list[list[Any]]) -> bytes:
    """El Excel de un presupuesto de ejemplo, con el encabezado en la fila 1."""
    return build_xlsx(rows, sheet=SHEET)


def sheet_rows(rows: list[list[Any]]) -> list[list[Any]]:
    """Las mismas filas, como las devuelve `excel.read_rows` (encabezado incluido)."""
    return [HEADER, *rows]
