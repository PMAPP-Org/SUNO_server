"""Índices del control (SPI y CPI) y porcentajes del BAC. Puro.

Un índice es un cociente a dos decimales (`ROUND_HALF_UP`) y su color se decide sobre el valor
redondeado: verde desde 0,95, ámbar desde 0,85, rojo por debajo (pieza 5, 1.4; pieza 6a, 3.2).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext

from suno.budgets.totals import HUNDRED, WIDE_PRECISION

INDEX_STEP = Decimal("0.01")
SHARE_STEP = Decimal("0.1")
GREEN_FROM = Decimal("0.95")
AMBER_FROM = Decimal("0.85")


def ratio_of(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """`numerator / denominator` a dos decimales; `None` si el denominador no es positivo."""
    if denominator <= 0:
        return None
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return (numerator / denominator).quantize(INDEX_STEP, rounding=ROUND_HALF_UP)


def status_of(index: Decimal | None) -> str:
    """Verde desde 0,95; ámbar desde 0,85; rojo por debajo; "none" sin índice."""
    if index is None:
        return "none"
    if index >= GREEN_FROM:
        return "green"
    if index >= AMBER_FROM:
        return "amber"
    return "red"


def share_of(value: Decimal, total: Decimal) -> Decimal | None:
    """% de `total` a un decimal; `None` si `total` es 0."""
    if total <= 0:
        return None
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return (value * HUNDRED / total).quantize(SHARE_STEP, rounding=ROUND_HALF_UP)
