"""Cálculo de contratos y actas (costos, pieza 6b, spec 3). Puro: sin BD.

Todo es `Decimal` con el redondeo comercial a centavos de `budgets.totals`, línea por línea:

1. Bruto directo = Σ (cantidad × precio del contrato), cada línea a centavos.
2. Cargos: los del contrato sobre sus bases (`budgets.totals.compute_charges`).
3. Bruto = bruto directo + cargos: el costo que va al libro.
4. Amortización = bruto × anticipo ÷ valor vigente, sin pasar del saldo (en la liquidación,
   todo el saldo).
5. Retención de garantía = bruto × % (en la liquidación no se retiene y se devuelve lo retenido).
6. Descuentos: cada uno sobre su base: `direct` (bruto directo), `gross` (bruto) o `line:<n>`
   (la línea `n` de los cargos del acta).
7. Neto = bruto − amortización − retención − descuentos + devolución.

El valor vigente de un contrato es el bruto directo de sus cantidades vigentes más sus cargos.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any
from uuid import UUID

from suno.budgets.totals import (
    BASE_DIRECT,
    HUNDRED,
    MAX_CHARGE_NAME,
    MAX_CHARGES,
    PCT_STEP,
    WIDE_PRECISION,
    ZERO,
    ChargeError,
    ChargeLine,
    ChargeResult,
    ChargeSpec,
    base_line_order,
    compute_charges,
    default_charges,
    item_total,
    line_base,
    money,
)

BASE_GROSS = "gross"
#: Tarifa general del IVA en Colombia (plantillas).
VAT = Decimal("19")
CHARGE_TEMPLATES = {
    "aiu": "AIU con IVA sobre utilidad",
    "iva": "IVA pleno",
    "ninguno": "Sin cargos",
}
DEDUCTION_TEMPLATES = {"retenciones": "Retenciones típicas", "ninguno": "Sin descuentos"}


@dataclass(frozen=True)
class LineInput:
    line_id: UUID
    order: int
    quantity: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class LineAmount:
    line_id: UUID
    order: int
    quantity: Decimal
    amount: Decimal


def scaled(value: Decimal, numerator: Decimal, denominator: Decimal) -> Decimal:
    """`value × numerator ÷ denominator` a centavos, sin perder dígitos en el producto."""
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return money(value * numerator / denominator)


def line_amounts(lines: Sequence[LineInput]) -> tuple[LineAmount, ...]:
    return tuple(
        LineAmount(
            line.line_id, line.order, line.quantity, item_total(line.quantity, line.unit_price)
        )
        for line in lines
    )


def contract_value(lines: Sequence[LineInput], charges: Sequence[ChargeSpec]) -> ChargeResult:
    """Bruto directo de las cantidades dadas (las vigentes) y sus cargos."""
    direct = sum((amount.amount for amount in line_amounts(lines)), ZERO)
    return compute_charges(direct, charges)


def validate_deductions(deductions: Sequence[ChargeSpec], charge_count: int) -> None:
    if len(deductions) > MAX_CHARGES:
        raise ChargeError(f"Máximo {MAX_CHARGES} descuentos.")
    for expected, deduction in enumerate(deductions, start=1):
        if deduction.order != expected:
            raise ChargeError("Los descuentos deben numerarse 1, 2, 3… en orden.")
        name = deduction.name.strip()
        if not name or len(name) > MAX_CHARGE_NAME:
            raise ChargeError(
                f"Descuento {expected}: el nombre es obligatorio y tiene máximo "
                f"{MAX_CHARGE_NAME} caracteres."
            )
        pct = deduction.pct
        if (
            not pct.is_finite()
            or pct < 0
            or pct > HUNDRED
            or pct != pct.quantize(PCT_STEP, rounding=ROUND_HALF_UP)
        ):
            raise ChargeError(
                f"Descuento {expected}: el porcentaje va entre 0 y 100, con máximo 4 decimales."
            )
        if deduction.base in (BASE_DIRECT, BASE_GROSS):
            continue
        ref = base_line_order(deduction.base)
        if ref is None or ref > charge_count:
            raise ChargeError(
                f"Descuento {expected}: la base es el bruto directo, el bruto o un cargo "
                "del contrato."
            )


def compute_deductions(
    direct: Decimal, charges: ChargeResult, deductions: Sequence[ChargeSpec]
) -> tuple[ChargeLine, ...]:
    validate_deductions(deductions, len(charges.lines))
    by_order = {line.order: line.amount for line in charges.lines}
    result: list[ChargeLine] = []
    for deduction in deductions:
        if deduction.base == BASE_DIRECT:
            base_amount = direct
        elif deduction.base == BASE_GROSS:
            base_amount = charges.total
        else:
            base_amount = by_order[base_line_order(deduction.base) or 0]
        result.append(
            ChargeLine(
                order=deduction.order,
                name=deduction.name.strip(),
                pct=deduction.pct,
                base=deduction.base,
                base_amount=base_amount,
                amount=scaled(base_amount, deduction.pct, HUNDRED),
            )
        )
    return tuple(result)


@dataclass(frozen=True)
class Balances:
    """Lo que ya pasó en el contrato antes de un acta (sus actas aprobadas)."""

    current_value: Decimal
    advance_amount: Decimal
    amortized: Decimal = ZERO
    retained: Decimal = ZERO
    returned: Decimal = ZERO

    @property
    def advance_balance(self) -> Decimal:
        return max(self.advance_amount - self.amortized, ZERO)

    @property
    def retained_balance(self) -> Decimal:
        return max(self.retained - self.returned, ZERO)


def _line_json(line: ChargeLine) -> dict[str, Any]:
    return {
        "order": line.order,
        "name": line.name,
        "pct": str(line.pct),
        "base": line.base,
        "base_amount": str(line.base_amount),
        "amount": str(line.amount),
    }


@dataclass(frozen=True)
class CertificateTotals:
    lines: tuple[LineAmount, ...]
    charges: ChargeResult
    deductions: tuple[ChargeLine, ...]
    amortization: Decimal
    retention: Decimal
    retention_returned: Decimal

    @property
    def direct(self) -> Decimal:
        return self.charges.direct_cost

    @property
    def charges_total(self) -> Decimal:
        return money(self.charges.total - self.charges.direct_cost)

    @property
    def gross(self) -> Decimal:
        return self.charges.total

    @property
    def deductions_total(self) -> Decimal:
        return money(sum((line.amount for line in self.deductions), ZERO))

    @property
    def net(self) -> Decimal:
        return money(
            self.gross
            - self.amortization
            - self.retention
            - self.deductions_total
            + self.retention_returned
        )

    def stored(self) -> dict[str, Decimal]:
        """Los campos de `ContractCertificate` con los totales."""
        return {
            "direct": self.direct,
            "charges": self.charges_total,
            "gross": self.gross,
            "amortization": self.amortization,
            "retention": self.retention,
            "deductions": self.deductions_total,
            "retention_returned": self.retention_returned,
            "net": self.net,
        }

    def breakdown(self) -> dict[str, Any]:
        return {
            "charges": [_line_json(line) for line in self.charges.lines],
            "deductions": [_line_json(line) for line in self.deductions],
        }


def certificate_totals(
    lines: Sequence[LineInput],
    charges: Sequence[ChargeSpec],
    deductions: Sequence[ChargeSpec],
    *,
    retention_pct: Decimal,
    balances: Balances,
    settlement: bool = False,
) -> CertificateTotals:
    amounts = line_amounts(lines)
    direct = sum((amount.amount for amount in amounts), ZERO)
    charge_result = compute_charges(direct, charges)
    gross = charge_result.total
    if settlement:
        amortization = balances.advance_balance
        retention = ZERO
        returned = balances.retained_balance
    else:
        share = (
            scaled(gross, balances.advance_amount, balances.current_value)
            if balances.current_value > 0
            else ZERO
        )
        amortization = min(share, balances.advance_balance)
        retention = scaled(gross, retention_pct, HUNDRED)
        returned = ZERO
    return CertificateTotals(
        lines=amounts,
        charges=charge_result,
        deductions=compute_deductions(direct, charge_result, deductions),
        amortization=amortization,
        retention=retention,
        retention_returned=returned,
    )


def ledger_split(amounts: Sequence[LineAmount], charges_total: Decimal) -> dict[UUID, Decimal]:
    """Valor de cada línea en el libro: su directo más su parte de los cargos, en proporción al
    directo. El ajuste de redondeo va a la línea de mayor valor (empate: la de menor orden), así
    la suma es exactamente el bruto. Las líneas en cero no van."""
    priced = [amount for amount in amounts if amount.amount > 0]
    if not priced:
        return {}
    direct = sum((amount.amount for amount in priced), ZERO)
    values = {
        amount.line_id: amount.amount + scaled(charges_total, amount.amount, direct)
        for amount in priced
    }
    biggest = min(priced, key=lambda amount: (-amount.amount, amount.order)).line_id
    values[biggest] += direct + charges_total - sum(values.values(), ZERO)
    return values


def _folded(text: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text.strip().casefold())
        if not unicodedata.combining(char)
    )


def charge_template(name: str) -> list[ChargeSpec]:
    """ "AIU con IVA sobre utilidad" (A, I y U en 0 % para llenar), "IVA pleno" o "Sin cargos"."""
    if name == "aiu":
        aiu = default_charges()
        return [*aiu[:3], ChargeSpec(4, aiu[3].name, VAT, aiu[3].base)]
    if name == "iva":
        return [ChargeSpec(1, "IVA", VAT, BASE_DIRECT)]
    return []


def withholding_template(charges: Sequence[ChargeSpec]) -> list[ChargeSpec]:
    """ "Retenciones típicas", en 0 % para llenar. ReteIVA va sobre la primera línea de cargos
    cuyo nombre empieza por "IVA" (o sobre el bruto si no hay)."""
    vat = next((c.order for c in charges if _folded(c.name).startswith("iva")), None)
    return [
        ChargeSpec(1, "Retención en la fuente", Decimal("0"), BASE_DIRECT),
        ChargeSpec(2, "ReteICA", Decimal("0"), BASE_DIRECT),
        ChargeSpec(3, "ReteIVA", Decimal("0"), line_base(vat) if vat else BASE_GROSS),
        ChargeSpec(4, "Estampillas", Decimal("0"), BASE_GROSS),
    ]
