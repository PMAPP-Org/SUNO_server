"""Aritmética del presupuesto (pura: sin BD).

Todo el dinero es `Decimal` y se redondea a centavos con `ROUND_HALF_UP` (la
regla comercial: 0,005 sube), nunca con el redondeo bancario por defecto de
Python. Las cantidades se guardan con 4 decimales; los porcentajes de los
cargos, también.

Cargos (§1 de la spec): líneas en orden, cada una con un % sobre una base:

- `direct`: el costo directo (suma de los ítems);
- `line:<n>`: el valor de la línea `n`, que tiene que ser anterior;
- `subtotal`: costo directo + todas las líneas anteriores.

Cada línea se redondea a centavos antes de servir de base a otra: así la
pantalla, la BD y el Excel exportado suman lo mismo.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
QUANTITY_STEP = Decimal("0.0001")
PCT_STEP = Decimal("0.0001")
MAX_PCT = Decimal("1000")
HUNDRED = Decimal(100)
ZERO = Decimal("0.00")

BASE_DIRECT = "direct"
BASE_SUBTOTAL = "subtotal"
LINE_PREFIX = "line:"
MAX_CHARGES = 20
MAX_CHARGE_NAME = 100

_LINE = re.compile(r"line:([1-9][0-9]?)")
_NUMBER_TEXT = re.compile(r"[0-9.,]+")


class ChargeError(ValueError):
    """Los cargos no son válidos; el mensaje es para el usuario."""


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def quantity(value: Decimal) -> Decimal:
    return value.quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP)


def item_total(qty: Decimal, unit_price: Decimal) -> Decimal:
    """Total de un ítem: cantidad × valor unitario, a centavos."""
    return money(qty * unit_price)


def pct_change(old: Decimal, new: Decimal) -> Decimal | None:
    """Variación porcentual de `old` a `new`, con 2 decimales; None si `old` es 0."""
    if old == 0:
        return None
    return money((new - old) / old * HUNDRED)


def pct_text(value: Decimal) -> str:
    """Un porcentaje para leer: `10.0000` → "10", `12.5000` → "12,5"."""
    return format(value.normalize(), "f").replace(".", ",")


def parse_number(value: object) -> Decimal | None:
    """Número de una celda o de un campo de texto; None si está vacío.

    Lanza `ValueError` si no es un número. En texto acepta separadores de miles
    y decimales en los dos estilos ("1.234,56" y "1,234.56"), `$` y espacios.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("booleano")
    if isinstance(value, int | Decimal):
        result = Decimal(value)
    elif isinstance(value, float):
        result = Decimal(repr(value))
    elif isinstance(value, str):
        text = value.replace("$", "").replace(" ", "").replace(" ", "").strip()
        if not text:
            return None
        negative = text.startswith("-")
        if negative:
            text = text[1:]
        if not _NUMBER_TEXT.fullmatch(text):
            raise ValueError(value)
        try:
            result = Decimal(_normalize_separators(text))
        except InvalidOperation as exc:
            raise ValueError(value) from exc
        if negative:
            result = -result
    else:
        raise ValueError(value)
    if not result.is_finite():
        raise ValueError(value)
    return result


def _normalize_separators(text: str) -> str:
    dots, commas = text.count("."), text.count(",")
    if dots and commas:
        decimal_sep = "." if text.rfind(".") > text.rfind(",") else ","
        thousands = "," if decimal_sep == "." else "."
        text = text.replace(thousands, "")
        if text.count(decimal_sep) > 1:
            raise ValueError(text)
        return text.replace(",", ".")
    if commas:
        return text.replace(",", "") if commas > 1 else text.replace(",", ".")
    if dots > 1:
        return text.replace(".", "")
    return text


@dataclass(frozen=True)
class ChargeSpec:
    order: int
    name: str
    pct: Decimal
    base: str


@dataclass(frozen=True)
class ChargeLine:
    order: int
    name: str
    pct: Decimal
    base: str
    base_amount: Decimal
    amount: Decimal


@dataclass(frozen=True)
class ChargeResult:
    direct_cost: Decimal
    lines: tuple[ChargeLine, ...]
    total: Decimal


def line_base(order: int) -> str:
    return f"{LINE_PREFIX}{order}"


def base_line_order(base: str) -> int | None:
    """`line:3` → 3; None si la base no es una línea."""
    match = _LINE.fullmatch(base)
    return int(match.group(1)) if match else None


def describe_base(base: str, names: Mapping[int, str]) -> str:
    """La base en palabras: "costo directo", "subtotal" o "línea 3 (Utilidad)"."""
    if base == BASE_DIRECT:
        return "costo directo"
    if base == BASE_SUBTOTAL:
        return "subtotal"
    order = base_line_order(base)
    if order is None:
        return base
    name = names.get(order)
    return f"línea {order} ({name})" if name else f"línea {order}"


def default_charges() -> list[ChargeSpec]:
    """Plantilla "AIU + IVA sobre utilidad", con los porcentajes en 0."""
    return [
        ChargeSpec(1, "Administración", Decimal("0"), BASE_DIRECT),
        ChargeSpec(2, "Imprevistos", Decimal("0"), BASE_DIRECT),
        ChargeSpec(3, "Utilidad", Decimal("0"), BASE_DIRECT),
        ChargeSpec(4, "IVA sobre utilidad", Decimal("0"), line_base(3)),
    ]


def validate_charges(charges: Sequence[ChargeSpec]) -> None:
    if len(charges) > MAX_CHARGES:
        raise ChargeError(f"Máximo {MAX_CHARGES} líneas de cargos.")
    for expected, charge in enumerate(charges, start=1):
        if charge.order != expected:
            raise ChargeError("Las líneas de cargos deben numerarse 1, 2, 3… en orden.")
        name = charge.name.strip()
        if not name or len(name) > MAX_CHARGE_NAME:
            raise ChargeError(
                f"Línea {expected}: el nombre es obligatorio y tiene máximo "
                f"{MAX_CHARGE_NAME} caracteres."
            )
        pct = charge.pct
        if (
            not pct.is_finite()
            or pct < 0
            or pct >= MAX_PCT
            or pct != pct.quantize(PCT_STEP, rounding=ROUND_HALF_UP)
        ):
            raise ChargeError(
                f"Línea {expected}: el porcentaje debe estar entre 0 y 999,9999, "
                "con máximo 4 decimales."
            )
        if charge.base in (BASE_DIRECT, BASE_SUBTOTAL):
            continue
        ref = base_line_order(charge.base)
        if ref is None or ref >= expected:
            raise ChargeError(
                f"Línea {expected}: la base debe ser el costo directo, el subtotal "
                "o una línea anterior."
            )


def compute_charges(direct_cost: Decimal, charges: Sequence[ChargeSpec]) -> ChargeResult:
    """Valor de cada línea, en orden, y el total de la versión."""
    validate_charges(charges)
    amounts: dict[int, Decimal] = {}
    lines: list[ChargeLine] = []
    for charge in charges:
        if charge.base == BASE_DIRECT:
            base_amount = direct_cost
        elif charge.base == BASE_SUBTOTAL:
            base_amount = direct_cost + sum(amounts.values(), ZERO)
        else:
            base_amount = amounts[base_line_order(charge.base) or 0]
        amount = money(base_amount * charge.pct / HUNDRED)
        amounts[charge.order] = amount
        lines.append(
            ChargeLine(
                order=charge.order,
                name=charge.name.strip(),
                pct=charge.pct,
                base=charge.base,
                base_amount=base_amount,
                amount=amount,
            )
        )
    total = money(direct_cost + sum(amounts.values(), ZERO))
    return ChargeResult(direct_cost=direct_cost, lines=tuple(lines), total=total)
