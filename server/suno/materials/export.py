"""Excel de materiales (costos, pieza 6c, spec 3.4). Todo texto de usuario pasa por
`safe_cell`; los valores van como número y las fechas en ISO."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from suno.backoffice.export import safe_cell, to_xlsx

from .queries import InventoryRow, Kardex

INVENTORY_HEADER = [
    "bodega", "codigo", "material", "unidad", "categoria", "cantidad", "promedio", "valor",
]  # fmt: skip
KARDEX_HEADER = [
    "secuencia", "fecha", "tipo", "documento", "entrada", "salida", "costo_unitario", "valor",
    "saldo", "valor_saldo", "promedio",
]  # fmt: skip


def number(value: Decimal | None) -> float | str:
    return "" if value is None else float(value)


def inventory_workbook(rows: Sequence[InventoryRow]) -> bytes:
    return to_xlsx(
        INVENTORY_HEADER,
        [
            [
                safe_cell(row.balance.warehouse.name),
                safe_cell(row.balance.material.code),
                safe_cell(row.balance.material.name),
                safe_cell(row.balance.material.unit),
                safe_cell(row.balance.material.category),
                number(row.balance.quantity),
                number(row.average),
                number(row.balance.value),
            ]
            for row in rows
        ],
        sheet_name="Inventario",
    )


def kardex_workbook(sheet: Kardex) -> bytes:
    return to_xlsx(
        KARDEX_HEADER,
        [
            [
                row.movement.sequence,
                row.movement.moved_on.isoformat(),
                row.movement.get_kind_display(),
                safe_cell(row.document),
                number(row.entry),
                number(row.exit),
                number(row.movement.unit_cost),
                number(row.movement.value),
                number(row.balance_quantity),
                number(row.balance_value),
                number(row.average),
            ]
            for row in sheet.rows
        ],
        sheet_name="Kárdex",
    )
