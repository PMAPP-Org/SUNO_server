"""Excel de contratos (costos, pieza 6b, spec 2.8): la lista con saldos y el detalle de un
contrato con sus actas. Todo texto de usuario pasa por `safe_cell`; los valores van como
número (`to_xlsx` solo conoce int y float) y las fechas en ISO."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from suno.backoffice.export import safe_cell, to_xlsx, to_xlsx_book

from .queries import ContractRow, ContractSheet

LIST_HEADER = [
    "numero", "tipo", "contratista", "objeto", "inicio", "fin", "valor_vigente", "ejecutado",
    "porcentaje", "anticipo", "anticipo_por_amortizar", "retenido", "estado",
]  # fmt: skip


def _n(value: Decimal | None) -> float | str:
    return "" if value is None else float(value)


def contracts_workbook(rows: Sequence[ContractRow]) -> bytes:
    return to_xlsx(
        LIST_HEADER,
        [
            [
                safe_cell(row.contract.number),
                row.contract.get_kind_display(),
                safe_cell(row.contract.contractor),
                safe_cell(row.contract.purpose),
                row.contract.starts_on.isoformat(),
                row.contract.ends_on.isoformat(),
                _n(row.contract.current_value),
                _n(row.progress.executed),
                _n(row.executed_pct),
                _n(row.contract.advance_amount),
                _n(row.advance_balance),
                _n(row.retained),
                row.contract.get_status_display(),
            ]
            for row in rows
        ],
        sheet_name="Contratos",
    )


def contract_workbook(sheet: ContractSheet) -> bytes:
    contract = sheet.contract
    facts: list[list[Any]] = [
        ["numero", safe_cell(contract.number)],
        ["tipo", contract.get_kind_display()],
        ["contratista", safe_cell(contract.contractor)],
        ["objeto", safe_cell(contract.purpose)],
        ["inicio", contract.starts_on.isoformat()],
        ["fin", contract.ends_on.isoformat()],
        ["valor_vigente", _n(contract.current_value)],
        ["anticipo", _n(contract.advance_amount)],
        ["anticipo_por_amortizar", _n(sheet.advance_balance)],
        ["retencion_pct", _n(contract.retention_pct)],
        ["ejecutado", _n(sheet.progress.executed)],
        ["estado", contract.get_status_display()],
    ]
    lines = [
        [
            line.line.order,
            safe_cell(line.label),
            safe_cell(line.tasks),
            safe_cell(line.line.unit),
            _n(line.line.unit_price),
            _n(line.contracted),
            _n(line.paid),
            _n(line.balance),
            _n(line.contracted_value),
            _n(line.balance_value),
        ]
        for line in sheet.lines
    ]
    certificates = [
        [
            c.number,
            "liquidación" if c.is_settlement else "avance",
            c.period_start.isoformat(),
            c.period_end.isoformat(),
            _n(c.direct),
            _n(c.charges),
            _n(c.gross),
            _n(c.amortization),
            _n(c.retention),
            _n(c.deductions),
            _n(c.retention_returned),
            _n(c.net),
            c.get_status_display(),
        ]
        for c in sheet.certificates
    ]
    amendments = [
        [
            a.number,
            a.signed_on.isoformat(),
            safe_cell(a.reason),
            a.new_ends_on.isoformat() if a.new_ends_on else "",
            _n(a.value_after),
            a.get_status_display(),
        ]
        for a in sheet.amendments
    ]
    return to_xlsx_book(
        [
            ("Contrato", ["campo", "valor"], facts),
            ("Líneas", ["orden", "item", "tareas", "unidad", "precio", "contratado", "pagado",
                        "saldo", "valor_contratado", "valor_saldo"], lines),
            ("Actas", ["numero", "tipo", "desde", "hasta", "bruto_directo", "cargos", "bruto",
                       "amortizacion", "retencion", "descuentos", "devolucion", "neto",
                       "estado"], certificates),
            ("Otrosíes", ["numero", "fecha", "motivo", "nuevo_fin", "valor_despues", "estado"],
             amendments),
        ]
    )  # fmt: skip
