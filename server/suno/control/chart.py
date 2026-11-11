"""Curva S del control (costos, pieza 5). Puro: coordenadas y etiquetas ya calculadas.

Mismo estilo que los gráficos de costos de equipos (`suno.equipment.charts`): SVG con
`viewBox` 0-100 y `preserveAspectRatio="none"`, marcas y etiquetas en HTML. Los valores salen
de `Decimal` calculados, nunca de texto del usuario, así que los estilos en línea son seguros.
"""

from __future__ import annotations

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


def build_s_curve(rows: Sequence[PeriodRow], bac: Decimal) -> SCurve | None:
    if not rows:
        return None
    measured: list[tuple[int, Decimal]] = []
    for index, period_row in enumerate(rows):
        if period_row.ev_cum is not None:
            measured.append((index, period_row.ev_cum))
    axis = money_axis(max([bac, *(r.pv_cum for r in rows), *(v for _, v in measured)]))
    count = Decimal(len(rows))

    def x(index: int) -> Decimal:
        return (Decimal(index + 1) / count * HUNDRED).quantize(COORD)

    def y(value: Decimal) -> Decimal:
        return (HUNDRED - value / axis.maximum * HUNDRED).quantize(COORD)

    pv_points = " ".join(["0,100", *(f"{x(i)},{y(r.pv_cum)}" for i, r in enumerate(rows))])
    ev_points = " ".join(["0,100", *(f"{x(i)},{y(v)}" for i, v in measured)]) if measured else ""
    step = -(-len(rows) // MAX_X_LABELS)  # división hacia arriba
    x_labels = tuple(
        (((Decimal(i) + HALF) / count * HUNDRED).quantize(COORD), rows[i].period.label)
        for i in range(0, len(rows), step)
    )
    return SCurve(
        axis=axis,
        pv_points=pv_points,
        ev_points=ev_points,
        cut_x=x(measured[-1][0]) if measured else None,
        x_labels=x_labels,
        pv_last=short_money(rows[-1].pv_cum),
        ev_last=short_money(measured[-1][1]) if measured else "",
    )
