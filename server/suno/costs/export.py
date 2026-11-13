"""Excel del libro de costos (costos, pieza 6a, spec 2.7).

Descripciones, terceros y documentos los escribe un usuario: todo pasa por `safe_cell`
contra la inyección de fórmulas. Valores como número; fechas en ISO.
"""

from __future__ import annotations

from collections.abc import Sequence

from suno.backoffice.export import safe_cell, to_xlsx

from .ledger import ORIGIN_LABELS, LedgerLine

SHEET = "Libro de costos"
HEADER = [
    "fecha", "origen", "tipo", "descripcion", "asignacion", "tercero", "documento",
    "valor", "estado", "registro", "aprobo",
]  # fmt: skip


def ledger_workbook(lines: Sequence[LedgerLine]) -> bytes:
    rows = [
        [
            safe_cell(value)
            for value in (
                line.day.isoformat(),
                ORIGIN_LABELS[line.origin],
                line.kind,
                line.description,
                line.assignment,
                line.third_party,
                line.document,
                float(line.amount),
                line.status_label,
                line.created_by,
                line.decided_by,
            )
        ]
        for line in lines
    ]
    return to_xlsx(HEADER, rows, sheet_name=SHEET)
