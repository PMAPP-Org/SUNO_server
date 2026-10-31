"""Calendario laboral del Project: qué días se trabajan y cuántos hay entre dos fechas.

Los atrasos se cuentan en días hábiles de este calendario (spec, sección 3).
Contar día a día sería O(días) por tarea; con miles de tareas y proyectos de
varios años eso se nota en cada carga de página. Por eso el conteo es
aritmético: semanas completas × días laborales de la semana, más el resto, y
luego se corrige con las excepciones (festivos o días extra) que caen en el
rango, buscadas con `bisect` sobre una lista ordenada con sumas prefijas.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

ONE_DAY = timedelta(days=1)
#: Una excepción más larga que esto es un error del archivo, no un calendario.
MAX_EXCEPTION_DAYS = 3660
#: Tope de la suma de días de todas las excepciones (acota memoria y tiempo de carga).
MAX_TOTAL_EXCEPTION_DAYS = 20000
DEFAULT_WEEKDAYS = frozenset(range(5))  # lunes (0) a viernes (4)


@dataclass(frozen=True)
class CalendarException:
    """Rango de días (inclusivo) que se trabajan o no, contra la semana normal."""

    start: date
    end: date
    working: bool


class WorkCalendar:
    def __init__(
        self,
        working_weekdays: Iterable[int] = DEFAULT_WEEKDAYS,
        exceptions: Iterable[CalendarException] = (),
    ) -> None:
        self.working_weekdays = frozenset(working_weekdays)
        self.exceptions = tuple(exceptions)
        overrides: dict[date, bool] = {}
        total_days = 0
        for exc in self.exceptions:
            if (exc.end - exc.start).days > MAX_EXCEPTION_DAYS:
                raise ValueError(
                    f"Excepción de calendario demasiado larga: {exc.start} a {exc.end}."
                )
            total_days += (exc.end - exc.start).days + 1
            if total_days > MAX_TOTAL_EXCEPTION_DAYS:
                raise ValueError(
                    f"Demasiados días de excepciones de calendario (máximo "
                    f"{MAX_TOTAL_EXCEPTION_DAYS})."
                )
            day = exc.start
            while day <= exc.end:
                overrides[day] = exc.working  # la posterior gana si se solapan
                day += ONE_DAY
        deltas = sorted(
            (day, int(working) - int(day.weekday() in self.working_weekdays))
            for day, working in overrides.items()
        )
        deltas = [(day, delta) for day, delta in deltas if delta != 0]
        self._delta_days = [day for day, _ in deltas]
        self._prefix = [0]
        for _, delta in deltas:
            self._prefix.append(self._prefix[-1] + delta)
        self._overrides = overrides

    def is_working(self, day: date) -> bool:
        if day in self._overrides:
            return self._overrides[day]
        return day.weekday() in self.working_weekdays

    def count_inclusive(self, start: date, end: date) -> int:
        """Días hábiles en [start, end]; 0 si end < start."""
        if end < start:
            return 0
        days = (end - start).days + 1
        full_weeks, rest = divmod(days, 7)
        count = full_weeks * len(self.working_weekdays)
        first = start.weekday()
        count += sum(1 for i in range(rest) if (first + i) % 7 in self.working_weekdays)
        lo = bisect_left(self._delta_days, start)
        hi = bisect_right(self._delta_days, end)
        return count + self._prefix[hi] - self._prefix[lo]

    def signed_gap(self, frm: date, to: date) -> int:
        """Días hábiles de `frm` a `to`: positivo si `to` es posterior.

        Cuenta los días en (frm, to]: de un viernes al lunes siguiente hay 1.
        """
        if to > frm:
            return self.count_inclusive(frm + ONE_DAY, to)
        if to < frm:
            return -self.count_inclusive(to + ONE_DAY, frm)
        return 0

    def to_json(self) -> dict[str, Any]:
        return {
            "working_weekdays": sorted(self.working_weekdays),
            "exceptions": [
                {"start": e.start.isoformat(), "end": e.end.isoformat(), "working": e.working}
                for e in self.exceptions
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> WorkCalendar:
        if not data:
            return cls()
        return cls(
            working_weekdays=data.get("working_weekdays", DEFAULT_WEEKDAYS),
            exceptions=[
                CalendarException(
                    start=date.fromisoformat(e["start"]),
                    end=date.fromisoformat(e["end"]),
                    working=bool(e["working"]),
                )
                for e in data.get("exceptions", [])
            ],
        )
