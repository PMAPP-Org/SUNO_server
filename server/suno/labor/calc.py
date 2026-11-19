"""Costo estimado del tareo (costos, pieza 6d, spec 2.1 y 2.2). Puro: sin BD.

- Vigente un día: el valor de mayor "desde" que no pasa de ese día (jornal y factor).
- Costo de una línea = fracción × jornal vigente × factor vigente, a centavos (ROUND_HALF_UP).
  El de un destino es la suma de sus líneas ya redondeadas: la planilla y el libro cuadran al
  peso.
- Tope: nadie suma más de 1,5 jornales un mismo día entre todas las planillas no anuladas.
- La huella (`Summary.fingerprint`) es lo que se compara al aprobar: líneas, jornales, total y,
  por destino y por trabajador, jornales y costo (decisión 14 del plan).
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Any
from uuid import UUID

from suno.budgets.totals import WIDE_PRECISION, ZERO, money, quantity
from suno.core.models.labor import MAX_FRACTION

DAY_CAP = MAX_FRACTION
NO_DAYS = Decimal("0.0000")


@dataclass(frozen=True)
class History:
    """Valores con vigencia, ordenados por su "desde"."""

    starts: tuple[date, ...] = ()
    values: tuple[Decimal, ...] = ()

    @classmethod
    def of(cls, rows: Iterable[tuple[date, Decimal]]) -> History:
        ordered = sorted(rows, key=lambda row: row[0])
        return cls(tuple(row[0] for row in ordered), tuple(row[1] for row in ordered))

    def on(self, day: date) -> Decimal | None:
        index = bisect_right(self.starts, day)
        return self.values[index - 1] if index else None


def fraction_ok(value: Decimal | None) -> bool:
    """0 < fracción ≤ 1,5, con 4 decimales como mucho."""
    return (
        value is not None
        and value.is_finite()
        and 0 < value <= MAX_FRACTION
        and quantity(value) == value
    )


def line_amount(fraction: Decimal, wage: Decimal, factor: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return money(fraction * wage * factor)


@dataclass(frozen=True)
class LineCost:
    worker_id: UUID
    day: date
    #: Identifica el destino (en la BD, `TimesheetLine.destination_key`).
    key: str
    fraction: Decimal
    wage: Decimal | None
    factor: Decimal | None

    @property
    def amount(self) -> Decimal | None:
        if self.wage is None or self.factor is None:
            return None
        return line_amount(self.fraction, self.wage, self.factor)


def price(
    lines: Iterable[tuple[UUID, date, str, Decimal]],
    wages: Mapping[UUID, History],
    factors: History,
) -> list[LineCost]:
    """Cada línea (trabajador, día, destino, fracción) con el jornal y el factor de su día."""
    empty = History()
    return [
        LineCost(worker, day, key, fraction, wages.get(worker, empty).on(day), factors.on(day))
        for worker, day, key, fraction in lines
    ]


def problems(costs: Sequence[LineCost], names: Mapping[UUID, str]) -> list[str]:
    """Lo que impide aprobar: falta de jornal o de factor vigente, o una línea en $0."""
    found: list[str] = []
    for cost in sorted(costs, key=lambda c: (c.day, names.get(c.worker_id, ""))):
        name = names.get(cost.worker_id, "—")
        if cost.wage is None:
            text = f"{name} no tiene jornal vigente el {cost.day:%d/%m/%Y}."
        elif cost.factor is None:
            text = f"No hay factor prestacional vigente el {cost.day:%d/%m/%Y}."
        elif cost.amount == ZERO:
            text = f"La línea de {name} del {cost.day:%d/%m/%Y} cuesta $0: revisa la fracción."
        else:
            continue
        if text not in found:
            found.append(text)
    return found


@dataclass(frozen=True)
class Totals:
    days: Decimal
    amount: Decimal


@dataclass(frozen=True)
class Summary:
    lines: int
    days: Decimal
    total: Decimal
    #: Todas las líneas tienen jornal y factor y cuestan más de $0.
    complete: bool
    groups: Mapping[str, Totals]
    workers: Mapping[str, Totals]

    def fingerprint(self) -> dict[str, Any]:
        return {
            "lines": self.lines,
            "days": str(self.days),
            "total": str(self.total),
            "complete": self.complete,
            "groups": {k: [str(v.days), str(v.amount)] for k, v in sorted(self.groups.items())},
            "workers": {k: [str(v.days), str(v.amount)] for k, v in sorted(self.workers.items())},
        }

    def snapshot(self, labels: Mapping[str, str]) -> dict[str, Any]:
        """La huella y el resumen por destino que imprime el PDF (`Timesheet.summary`)."""
        groups = [
            {"label": labels.get(key, "—"), "days": str(v.days), "total": str(v.amount)}
            for key, v in self.groups.items()
        ]
        groups.sort(key=lambda group: group["label"])
        return {"fingerprint": self.fingerprint(), "groups": groups}


def _add(bucket: dict[str, list[Decimal]], key: str, days: Decimal, amount: Decimal) -> None:
    row = bucket.setdefault(key, [NO_DAYS, ZERO])
    row[0] += days
    row[1] += amount


def summarize(costs: Sequence[LineCost]) -> Summary:
    groups: dict[str, list[Decimal]] = {}
    workers: dict[str, list[Decimal]] = {}
    for cost in costs:
        amount = cost.amount if cost.amount is not None else ZERO
        _add(groups, cost.key, cost.fraction, amount)
        _add(workers, str(cost.worker_id), cost.fraction, amount)
    return Summary(
        lines=len(costs),
        days=quantity(sum((cost.fraction for cost in costs), NO_DAYS)),
        total=sum((row[1] for row in groups.values()), ZERO),
        complete=all(cost.amount is not None and cost.amount > 0 for cost in costs),
        groups={key: Totals(quantity(d), a) for key, (d, a) in groups.items()},
        workers={key: Totals(quantity(d), a) for key, (d, a) in workers.items()},
    )


def over_cap(totals: Mapping[tuple[UUID, date], Decimal]) -> list[tuple[UUID, date, Decimal]]:
    """Los (trabajador, día, jornales) que pasan del tope, por día y trabajador."""
    found = [(worker, day, total) for (worker, day), total in totals.items() if total > DAY_CAP]
    found.sort(key=lambda row: (row[1], str(row[0])))
    return found


def days_text(value: Decimal) -> str:
    """ "1,75": jornales con coma decimal y sin ceros de sobra."""
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


def totals_by_day(rows: Iterable[tuple[UUID, date, Decimal]]) -> dict[tuple[UUID, date], Decimal]:
    totals: dict[tuple[UUID, date], Decimal] = defaultdict(lambda: NO_DAYS)
    for worker, day, fraction in rows:
        totals[(worker, day)] += fraction
    return dict(totals)
