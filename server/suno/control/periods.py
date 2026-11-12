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


OUT_OF_CALENDAR = "fuera_de_calendario"
TOO_MANY_PERIODS = "demasiados_periodos"
#: Textos para el usuario. Las vistas muestran estos textos fijos, nunca `str(exc)`.
RANGE_MESSAGES = {
    OUT_OF_CALENDAR: (
        "Las fechas del cronograma o la fecha de corte están fuera del calendario. "
        "Revisa las fechas."
    ),
    TOO_MANY_PERIODS: (
        f"Las fechas del cronograma dan más de {MAX_PERIODS} periodos. Revisa las "
        "fechas de las tareas o usa periodos mensuales."
    ),
}


class PeriodRangeError(ValueError):
    """El rango de fechas da demasiados periodos o sale del calendario.

    `reason` es una llave de `RANGE_MESSAGES`, donde está el texto para el usuario.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(RANGE_MESSAGES[reason])
        self.reason = reason


def range_message(exc: PeriodRangeError) -> str:
    """Texto fijo para el usuario; no se devuelve el texto de la excepción."""
    return RANGE_MESSAGES.get(exc.reason, RANGE_MESSAGES[OUT_OF_CALENDAR])


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
    """Inicio del periodo siguiente. Pasado el año 9999 es un error de rango, no un 500."""
    try:
        if kind == WEEK:
            return start + timedelta(days=7)
        return date(start.year + start.month // 12, start.month % 12 + 1, 1)
    except (OverflowError, ValueError) as exc:
        raise PeriodRangeError(OUT_OF_CALENDAR) from exc


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
            raise PeriodRangeError(TOO_MANY_PERIODS)
        start = following
    return periods
