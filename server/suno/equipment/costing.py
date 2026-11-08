"""Aritmética del costeo por hora de equipos (pura: sin BD).

Mismo criterio de dinero que el presupuesto: `Decimal` y redondeo comercial a
centavos (`ROUND_HALF_UP`).
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from suno.budgets.totals import money

HOURS_STEP = Decimal("0.01")
SECONDS_PER_HOUR = Decimal(3600)


def usage_hours(start: time, end: time) -> Decimal:
    """Horas de una jornada (fin > inicio), con dos decimales."""

    anchor = date(2000, 1, 1)
    seconds = (datetime.combine(anchor, end) - datetime.combine(anchor, start)).total_seconds()
    return (Decimal(str(seconds)) / SECONDS_PER_HOUR).quantize(HOURS_STEP)


def usage_cost(hours: Decimal, hourly_rate: Decimal) -> Decimal:
    return money(hours * hourly_rate)


def pct_of(part: Decimal, whole: Decimal) -> Decimal | None:
    """Porcentaje con un decimal; `None` si no hay base contra la que comparar."""

    if whole == 0:
        return None
    return (part / whole * Decimal(100)).quantize(Decimal("0.1"))


DAILY_AVAILABLE_HOURS = Decimal(8)


def weekdays_between(start: date, end: date) -> int:
    """Días de lunes a viernes entre dos fechas, ambas incluidas."""

    if end < start:
        return 0
    days = (end - start).days + 1
    full_weeks, extra = divmod(days, 7)
    weekdays = full_weeks * 5
    first = start.weekday()
    weekdays += sum(1 for offset in range(extra) if (first + offset) % 7 < 5)
    return weekdays


def utilization_pct(hours: Decimal, start: date, end: date) -> Decimal | None:
    """Horas usadas sobre horas disponibles (días hábiles × jornada estándar)."""

    available = weekdays_between(start, end) * DAILY_AVAILABLE_HOURS
    return pct_of(hours, available) if available else None
