"""Curva S del control (costos, pieza 5). Puro: coordenadas y etiquetas ya calculadas.

Mismo estilo que los gráficos de costos de equipos (`suno.equipment.charts`): SVG con
`viewBox` 0-100 y `preserveAspectRatio="none"`, marcas y etiquetas en HTML. Los valores salen
de `Decimal` calculados, nunca de texto del usuario, así que los estilos en línea son seguros.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from suno.equipment.charts import Axis, money_axis, short_money

from .earned_value import PeriodRow

HUNDRED = Decimal(100)
HALF = Decimal("0.5")
COORD = Decimal("0.01")
#: Etiquetas del eje X como mucho: con cien semanas no caben todas.
MAX_X_LABELS = 8


#: La parte entera de un número de más de tres cifras (`29630048`).
_LONG_INTEGER = re.compile(r"\d{4,}")


def _grouped(text: str) -> str:
    """Puntos de miles en las etiquetas cortas de `suno.equipment.charts`, que no los ponen:
    con montos de miles de millones, `40000000` se lee mal; `40.000.000`, no."""
    return _LONG_INTEGER.sub(lambda m: f"{int(m.group()):,}".replace(",", "."), text)


@dataclass(frozen=True)
class SCurve:
    axis: Axis
    #: Puntos `x,y` del viewBox (y hacia abajo), desde el origen.
    pv_points: str
    #: Vacío si ningún periodo está medido.
    ev_points: str
    #: Posición (%) del fin del último periodo medido (la fecha de corte).
    cut_x: Decimal | None
    #: (posición % del centro del periodo, etiqueta).
    x_labels: tuple[tuple[Decimal, str], ...]
    pv_last: str
    ev_last: str
    #: Largo de la etiqueta más larga del eje Y: la plantilla le da ese ancho a la columna.
    tick_chars: int = 0


def build_s_curve(rows: Sequence[PeriodRow], bac: Decimal) -> SCurve | None:
    if not rows:
        return None
    measured: list[tuple[int, Decimal]] = []
    for index, period_row in enumerate(rows):
        if period_row.ev_cum is not None:
            measured.append((index, period_row.ev_cum))
    raw = money_axis(max([bac, *(r.pv_cum for r in rows), *(v for _, v in measured)]))
    axis = Axis(raw.maximum, raw.unit, [(pos, _grouped(label)) for pos, label in raw.ticks])
    # Eje X en el tiempo (días desde el inicio del primer periodo), no por periodo: un periodo
    # partido por el corte puede durar un día y no debe ocupar lo mismo que un mes.
    origin = rows[0].period.start
    span = Decimal((rows[-1].period.end - origin).days + 1)

    def at(days: Decimal) -> Decimal:
        return (days / span * HUNDRED).quantize(COORD)

    def x(index: int) -> Decimal:
        return at(Decimal((rows[index].period.end - origin).days + 1))

    def y(value: Decimal) -> Decimal:
        return (HUNDRED - value / axis.maximum * HUNDRED).quantize(COORD)

    def center(index: int) -> Decimal:
        period = rows[index].period
        length = Decimal((period.end - period.start).days + 1)
        return at(Decimal((period.start - origin).days) + length * HALF)

    pv_points = " ".join(["0,100", *(f"{x(i)},{y(r.pv_cum)}" for i, r in enumerate(rows))])
    ev_points = " ".join(["0,100", *(f"{x(i)},{y(v)}" for i, v in measured)]) if measured else ""
    step = -(-len(rows) // MAX_X_LABELS)  # división hacia arriba
    x_labels = tuple((center(i), rows[i].period.label) for i in range(0, len(rows), step))
    return SCurve(
        axis=axis,
        pv_points=pv_points,
        ev_points=ev_points,
        cut_x=x(measured[-1][0]) if measured else None,
        x_labels=x_labels,
        pv_last=_grouped(short_money(rows[-1].pv_cum)),
        ev_last=_grouped(short_money(measured[-1][1])) if measured else "",
        tick_chars=max(len(label) for _, label in axis.ticks),
    )
