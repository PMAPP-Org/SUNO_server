"""Importes con signo para las plantillas (costos, pieza 5).

`floatformat` deja el menos entre el símbolo y el número (`$-3.150.000,00`); aquí el signo va
antes del símbolo y es el signo menos tipográfico (U+2212): `−$3.150.000,00`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import template
from django.template.defaultfilters import floatformat

register = template.Library()

MINUS = "−"
CENT = Decimal("0.01")


def _parts(value: Any) -> tuple[str, str] | None:
    if value is None or value == "":
        return None
    amount = Decimal(value).quantize(CENT)
    return (MINUS if amount < 0 else "", floatformat(abs(amount), "2g"))


@register.filter
def money(value: Any) -> str:
    """`$1.234,50` o `−$1.234,50`; vacío si no hay valor."""
    parts = _parts(value)
    return "" if parts is None else f"{parts[0]}${parts[1]}"


@register.filter
def signed(value: Any) -> str:
    """El importe sin símbolo, con el signo menos tipográfico: `−1.234,50`."""
    parts = _parts(value)
    return "" if parts is None else f"{parts[0]}{parts[1]}"
