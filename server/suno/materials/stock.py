"""Promedio ponderado móvil por material y bodega (costos, pieza 6c, spec 2.1). Puro: sin
base de datos (el motor con bloqueo es `kardex`).

- Entrada (compra, sobrante, devolución, traslado que entra): suma cantidad y valor.
- Salida (salida, faltante, traslado que sale): costo unitario = valor / cantidad del saldo;
  valor = cantidad × unitario a centavos, sin pasar del valor del saldo; si se lleva toda la
  cantidad, exactamente el valor restante. Sin inventario negativo.
- Retiro por anulación: por el valor con que entró, entre cero y el valor del saldo.

Cantidades con 4 decimales, valores a centavos, costo unitario con 6 decimales.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

from suno.budgets.totals import ZERO, item_total

UNIT_STEP = Decimal("0.000001")
_WIDE = 60


class Shortage(Exception):
    """El saldo no alcanza; `available` es lo que hay."""

    def __init__(self, available: Decimal) -> None:
        self.available = available
        super().__init__(str(available))


@dataclass(frozen=True)
class Balance:
    quantity: Decimal
    value: Decimal

    @property
    def average(self) -> Decimal | None:
        return unit_cost(self.quantity, self.value) if self.quantity > 0 else None


@dataclass(frozen=True)
class Move:
    """Lo que un movimiento hace al saldo: cantidad y valor con signo, y su costo unitario."""

    quantity: Decimal
    value: Decimal
    unit_cost: Decimal


def unit_cost(quantity: Decimal, value: Decimal) -> Decimal:
    if quantity <= 0:
        return Decimal("0.000000")
    with localcontext() as context:
        context.prec = _WIDE
        return (value / quantity).quantize(UNIT_STEP, rounding=ROUND_HALF_UP)


def apply(balance: Balance, move: Move) -> Balance:
    return Balance(balance.quantity + move.quantity, balance.value + move.value)


def entry(quantity: Decimal, value: Decimal) -> Move:
    return Move(quantity, value, unit_cost(quantity, value))


def exit_at_average(balance: Balance, quantity: Decimal) -> Move:
    if quantity > balance.quantity:
        raise Shortage(balance.quantity)
    unit = unit_cost(balance.quantity, balance.value)
    if quantity == balance.quantity:
        value = balance.value
    else:
        value = min(item_total(quantity, unit), balance.value)
    return Move(-quantity, -value, unit)


def withdraw(balance: Balance, quantity: Decimal, value: Decimal) -> Move:
    if quantity > balance.quantity:
        raise Shortage(balance.quantity)
    taken = balance.value if quantity == balance.quantity else min(max(value, ZERO), balance.value)
    return Move(-quantity, -taken, unit_cost(quantity, taken))


def returned_value(
    issued_quantity: Decimal,
    issued_amount: Decimal,
    unit: Decimal,
    already_quantity: Decimal,
    already_value: Decimal,
    quantity: Decimal,
) -> Decimal:
    """Valor de una devolución al costo unitario de la línea de salida. La que completa lo que
    salió se lleva lo que falta del valor de la línea (sin residuos de centavos)."""
    left = issued_amount - already_value
    if already_quantity + quantity == issued_quantity:
        return left
    return min(item_total(quantity, unit), left)
