"""Periodos del control con valor ganado (costos, pieza 5). Puro: sin BD.

Semanas de lunes a domingo o meses calendario. La serie va de `min(primer inicio, corte)` a
`max(último fin, corte)`. El periodo que contiene la fecha de corte termina en ella (es el
último con valor ganado); si la serie sigue, el resto de ese periodo es una fila aparte.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from suno.equipment.charts import MONTHS_ES, month_label

WEEK = "semana"
MONTH = "mes"
PERIOD_KINDS = {MONTH: "Mensual", WEEK: "Semanal"}
DEFAULT_KIND = MONTH
#: Tope de filas: una fecha absurda en el XML no puede generar miles de periodos.
MAX_PERIODS = 1000
ONE_DAY = timedelta(days=1)


class PeriodRangeError(ValueError):
    """El rango de fechas da demasiados periodos; el mensaje es para el usuario."""


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str
    #: Termina en la fecha de corte o antes: lleva valor ganado.
    measured: bool


def period_start(day: date, kind: str) -> date:
    if kind == WEEK:
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def next_start(start: date, kind: str) -> date:
    if kind == WEEK:
        return start + timedelta(days=7)
    return date(start.year + start.month // 12, start.month % 12 + 1, 1)


def period_label(start: date, kind: str) -> str:
    """Semana: `09-mar-2026` (su lunes). Mes: `Mar-2026`."""
    if kind == WEEK:
        return f"{start.day:02d}-{MONTHS_ES[start.month - 1]}-{start.year}"
    return month_label(start)


def build_periods(first: date, last: date, cut: date, kind: str) -> list[Period]:
    if kind not in PERIOD_KINDS:
        raise ValueError(f"Periodo desconocido: {kind!r}.")
    stop = max(last, cut)
    start = period_start(min(first, cut), kind)
    periods: list[Period] = []
    while start <= stop:
        following = next_start(start, kind)
        end = following - ONE_DAY
        label = period_label(start, kind)
        if start <= cut < end:
            periods.append(Period(start, cut, label, measured=True))
            if cut < stop:
                periods.append(
                    Period(cut + ONE_DAY, end, f"{label} (tras el corte)", measured=False)
                )
        else:
            periods.append(Period(start, end, label, measured=end <= cut))
        if len(periods) > MAX_PERIODS:
            raise PeriodRangeError(
                f"Las fechas del cronograma dan más de {MAX_PERIODS} periodos. Revisa las "
                "fechas de las tareas o usa periodos mensuales."
            )
        start = following
    return periods
