"""Valor ganado del proyecto (costos, pieza 5). Puro: sin BD.

Diseño: plans/2026-11-11-costos-pieza5-control-design.md, sección 1.

- **PV**: cada par tarea × ítem vale `planeada × precio unitario vigente`. El valor de la
  tarea se reparte parejo entre los días laborables de su ventana (línea base; sin ella, las
  fechas actuales, con aviso). Un hito, o una ventana sin días laborables, carga todo en su fin.
- **EV**: cantidades de registros validados × precio vigente, en la fecha del registro y hasta
  el corte. El acumulado de cada par se acota a `[0, planeada]`; lo que pasa de la planeada es
  sobreejecución y no entra.
- Se suma con precisión amplia y se redondea a centavos al presentar: el valor de un periodo y
  su acumulado se redondean por separado.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from uuid import UUID

from suno.budgets.totals import HUNDRED, WIDE_PRECISION, money
from suno.projects.workdays import WorkCalendar

from .periods import Period, build_periods

ZERO = Decimal(0)
ONE = Decimal(1)
SPI_STEP = Decimal("0.01")
SHARE_STEP = Decimal("0.1")
GREEN_FROM = Decimal("0.95")
AMBER_FROM = Decimal("0.85")
STATUS_LABELS = {
    "green": "Al día",
    "amber": "Atención",
    "red": "Atrasado",
    "none": "Sin valor planeado",
}
NO_CHAPTER = "Sin capítulo"
NO_LOCATION = "Sin ubicación"
UNKNOWN_ITEM = "Ítem desconocido"

Pair = tuple[UUID, UUID]


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskWindow:
    task_id: UUID
    #: "1.2 Descapote" (número de esquema y nombre).
    label: str
    #: Ubicación de la tarea ("Tramo 2 / PR 1"); vacía si no tiene.
    location: str
    start: date | None
    finish: date | None
    #: Las fechas son de la línea base; si no, son las actuales (va a los avisos).
    from_baseline: bool
    milestone: bool
    #: Orden de la fila en el Project, para listar.
    order: int = 0

    @property
    def dated(self) -> bool:
        return self.start is not None and self.finish is not None and self.start <= self.finish


@dataclass(frozen=True)
class PricedItem:
    item_id: UUID
    #: "2.1 Concreto zapatas".
    label: str
    unit: str
    unit_price: Decimal
    #: Capítulo de primer nivel que lo contiene ("2 Estructura"), o `NO_CHAPTER`.
    chapter: str


@dataclass(frozen=True)
class PlanPair:
    task_id: UUID
    item_id: UUID
    planned: Decimal


@dataclass(frozen=True)
class Execution:
    """Una fila de cantidad de un registro validado, en la fecha del registro."""

    task_id: UUID
    item_id: UUID
    day: date
    quantity: Decimal


@dataclass(frozen=True)
class PercentTask:
    """Tarea sin plan en la matriz: se mide por %. `pct`: su último % validado."""

    label: str
    pct: Decimal | None


@dataclass(frozen=True)
class ControlInput:
    cut: date
    kind: str
    calendar: WorkCalendar
    #: Tareas vigentes del cronograma (sin las retiradas).
    tasks: Mapping[UUID, TaskWindow]
    #: Ítems con precio vigente (`item` en la versión vigente de su presupuesto).
    items: Mapping[UUID, PricedItem]
    #: Nombre de cualquier ítem del proyecto, para los avisos.
    item_labels: Mapping[UUID, str]
    plans: Sequence[PlanPair]
    executions: Sequence[Execution]
    percent_tasks: Sequence[PercentTask] = ()
    #: El proyecto tiene al menos una versión de presupuesto aplicada.
    has_budget: bool = True
    #: Suma del costo directo de las versiones contractuales; `None` si no hay.
    contractual_direct_cost: Decimal | None = None


# ---------------------------------------------------------------------------
# Salida
# ---------------------------------------------------------------------------


def spi_of(ev: Decimal, pv: Decimal) -> Decimal | None:
    """EV / PV a dos decimales; `None` sin valor planeado."""
    if pv <= 0:
        return None
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return (ev / pv).quantize(SPI_STEP, rounding=ROUND_HALF_UP)


def status_of(spi: Decimal | None) -> str:
    """Verde desde 0,95; ámbar desde 0,85; rojo por debajo; "none" sin SPI."""
    if spi is None:
        return "none"
    if spi >= GREEN_FROM:
        return "green"
    if spi >= AMBER_FROM:
        return "amber"
    return "red"


def share_of(value: Decimal, total: Decimal) -> Decimal | None:
    """% de `total` a un decimal; `None` si `total` es 0."""
    if total <= 0:
        return None
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return (value * HUNDRED / total).quantize(SHARE_STEP, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Measure:
    """PV y EV a una fecha (a centavos), con su SV, SPI y estado."""

    pv: Decimal
    ev: Decimal

    @property
    def sv(self) -> Decimal:
        return self.ev - self.pv

    @property
    def spi(self) -> Decimal | None:
        return spi_of(self.ev, self.pv)

    @property
    def status(self) -> str:
        return status_of(self.spi)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]


@dataclass(frozen=True)
class PeriodRow:
    period: Period
    pv: Decimal
    pv_cum: Decimal
    #: `None` en los periodos posteriores al corte.
    ev: Decimal | None
    ev_cum: Decimal | None

    @property
    def sv(self) -> Decimal | None:
        return None if self.ev_cum is None else self.ev_cum - self.pv_cum

    @property
    def spi(self) -> Decimal | None:
        return None if self.ev_cum is None else spi_of(self.ev_cum, self.pv_cum)


@dataclass(frozen=True)
class Indicators:
    bac: Decimal
    at_cut: Measure
    contractual_direct_cost: Decimal | None = None

    @property
    def pv_share(self) -> Decimal | None:
        return share_of(self.at_cut.pv, self.bac)

    @property
    def ev_share(self) -> Decimal | None:
        return share_of(self.at_cut.ev, self.bac)


@dataclass(frozen=True)
class BreakdownRow:
    label: str
    bac: Decimal
    measure: Measure
    #: Solo en el desglose por tarea (enlace a su ficha).
    task_id: UUID | None = None


@dataclass(frozen=True)
class UnpricedPair:
    task: str
    item: str
    planned: Decimal
    executed: Decimal


@dataclass(frozen=True)
class Overrun:
    task: str
    item: str
    unit: str
    planned: Decimal
    executed: Decimal
    excess: Decimal
    value: Decimal


@dataclass(frozen=True)
class OutsidePlan:
    task: str
    item: str
    unit: str
    quantity: Decimal
    #: `None` si el ítem no tiene precio vigente.
    value: Decimal | None


@dataclass(frozen=True)
class Warnings:
    no_baseline: tuple[str, ...] = ()
    undated: tuple[str, ...] = ()
    unpriced: tuple[UnpricedPair, ...] = ()
    overrun: tuple[Overrun, ...] = ()
    outside_plan: tuple[OutsidePlan, ...] = ()
    percent_tasks: tuple[PercentTask, ...] = ()

    @property
    def count(self) -> int:
        return (
            len(self.no_baseline)
            + len(self.undated)
            + len(self.unpriced)
            + len(self.overrun)
            + len(self.outside_plan)
            + len(self.percent_tasks)
        )


@dataclass(frozen=True)
class ControlResult:
    cut: date
    kind: str
    has_budget: bool
    #: La matriz tiene al menos una fila (aunque ninguna mida).
    has_plan: bool
    indicators: Indicators
    #: Vacía si ningún par tiene precio vigente y fechas.
    periods: tuple[PeriodRow, ...]
    by_chapter: tuple[BreakdownRow, ...]
    by_task: tuple[BreakdownRow, ...]
    by_location: tuple[BreakdownRow, ...]
    warnings: Warnings


# ---------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Pair:
    task: TaskWindow
    item: PricedItem
    planned: Decimal

    @property
    def value(self) -> Decimal:
        return self.planned * self.item.unit_price


class _Spread:
    """Reparto parejo del valor de una tarea entre los días laborables de su ventana."""

    def __init__(self, task: TaskWindow, calendar: WorkCalendar) -> None:
        if task.start is None or task.finish is None:
            raise ValueError("La tarea no tiene fechas.")
        self.start = task.start
        self.finish = task.finish
        self.calendar = calendar
        self.workdays = 0 if task.milestone else calendar.count_inclusive(self.start, self.finish)

    def share(self, day: date) -> Decimal:
        """Parte (0..1) del valor planeada hasta `day` inclusive."""
        if day >= self.finish:
            return ONE
        if self.workdays == 0 or day < self.start:
            return ZERO
        return Decimal(self.calendar.count_inclusive(self.start, day)) / Decimal(self.workdays)


@dataclass
class _Totals:
    bac: Decimal = ZERO
    pv: Decimal = ZERO
    ev: Decimal = ZERO

    def add(self, bac: Decimal, pv: Decimal, ev: Decimal) -> None:
        self.bac += bac
        self.pv += pv
        self.ev += ev

    def row(self, label: str, task_id: UUID | None = None) -> BreakdownRow:
        return BreakdownRow(
            label, money(self.bac), Measure(money(self.pv), money(self.ev)), task_id
        )


def _worst_first(rows: Iterable[BreakdownRow]) -> tuple[BreakdownRow, ...]:
    def key(row: BreakdownRow) -> tuple[bool, Decimal, str]:
        spi = row.measure.spi
        return (spi is None, ZERO if spi is None else spi, row.label.lower())

    return tuple(sorted(rows, key=key))


def compute(data: ControlInput) -> ControlResult:
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return _compute(data)


def _compute(data: ControlInput) -> ControlResult:
    pairs: dict[Pair, _Pair] = {}
    planned_keys: set[Pair] = set()
    unpriced: list[PlanPair] = []
    undated: dict[UUID, TaskWindow] = {}
    for plan in data.plans:
        task = data.tasks.get(plan.task_id)
        if task is None:  # tarea retirada del cronograma
            continue
        key = (plan.task_id, plan.item_id)
        planned_keys.add(key)
        item = data.items.get(plan.item_id)
        if item is None:
            unpriced.append(plan)
        elif not task.dated:
            undated[task.task_id] = task
        else:
            pairs[key] = _Pair(task, item, plan.planned)

    executions: dict[Pair, list[Execution]] = defaultdict(list)
    for execution in data.executions:
        if execution.day <= data.cut and execution.task_id in data.tasks:
            executions[(execution.task_id, execution.item_id)].append(execution)

    def executed(key: Pair) -> Decimal:
        return sum((e.quantity for e in executions.get(key, ())), ZERO)

    unpriced_rows = tuple(
        UnpricedPair(
            task=data.tasks[p.task_id].label,
            item=data.item_labels.get(p.item_id, UNKNOWN_ITEM),
            planned=p.planned,
            executed=executed((p.task_id, p.item_id)),
        )
        for p in sorted(
            unpriced,
            key=lambda p: (data.tasks[p.task_id].order, data.item_labels.get(p.item_id, "")),
        )
    )
    no_baseline = {p.task.task_id: p.task for p in pairs.values() if not p.task.from_baseline}
    base_warnings = Warnings(
        no_baseline=tuple(t.label for t in sorted(no_baseline.values(), key=lambda t: t.order)),
        undated=tuple(t.label for t in sorted(undated.values(), key=lambda t: t.order)),
        unpriced=unpriced_rows,
        outside_plan=_outside_plan(data, executions, planned_keys),
        percent_tasks=tuple(data.percent_tasks),
    )
    if not pairs:
        return ControlResult(
            cut=data.cut,
            kind=data.kind,
            has_budget=data.has_budget,
            has_plan=bool(data.plans),
            indicators=Indicators(
                money(ZERO), Measure(money(ZERO), money(ZERO)), data.contractual_direct_cost
            ),
            periods=(),
            by_chapter=(),
            by_task=(),
            by_location=(),
            warnings=base_warnings,
        )

    spreads = {p.task.task_id: _Spread(p.task, data.calendar) for p in pairs.values()}
    counted_days = [e.day for key in pairs for e in executions.get(key, ())]
    first = min([s.start for s in spreads.values()] + counted_days)
    last = max(s.finish for s in spreads.values())
    periods = build_periods(first, last, data.cut, data.kind)
    starts = [p.start for p in periods]

    def index(day: date) -> int:
        return bisect_right(starts, day) - 1

    # PV: el valor de cada tarea, repartido por periodos.
    task_value: dict[UUID, Decimal] = defaultdict(Decimal)
    for pair in pairs.values():
        task_value[pair.task.task_id] += pair.value
    planned = [ZERO] * len(periods)
    for task_id, value in task_value.items():
        spread = spreads[task_id]
        before = ZERO
        for i in range(index(spread.start), index(spread.finish) + 1):
            share = spread.share(periods[i].end)
            planned[i] += value * (share - before)
            before = share

    # EV: lo validado de cada par, acotado a [0, planeada], en el periodo del registro.
    earned = [ZERO] * len(periods)
    earned_quantity: dict[Pair, Decimal] = {}
    overruns: list[Overrun] = []
    ordered = sorted(pairs.items(), key=lambda kv: (kv[1].task.order, kv[1].item.label))
    for key, pair in ordered:
        running = capped = ZERO
        for execution in sorted(executions.get(key, ()), key=lambda e: e.day):
            running += execution.quantity
            bounded = min(max(running, ZERO), pair.planned)
            if bounded != capped:
                earned[index(execution.day)] += (bounded - capped) * pair.item.unit_price
                capped = bounded
        earned_quantity[key] = capped
        if running > pair.planned:
            excess = running - pair.planned
            overruns.append(
                Overrun(
                    task=pair.task.label,
                    item=pair.item.label,
                    unit=pair.item.unit,
                    planned=pair.planned,
                    executed=running,
                    excess=excess,
                    value=money(excess * pair.item.unit_price),
                )
            )

    rows: list[PeriodRow] = []
    pv_cum = ev_cum = ZERO
    for i, period in enumerate(periods):
        pv_cum += planned[i]
        if period.measured:
            ev_cum += earned[i]
            rows.append(
                PeriodRow(period, money(planned[i]), money(pv_cum), money(earned[i]), money(ev_cum))
            )
        else:
            rows.append(PeriodRow(period, money(planned[i]), money(pv_cum), None, None))

    # A la fecha de corte: total y desglose.
    shares = {task_id: spread.share(data.cut) for task_id, spread in spreads.items()}
    total = _Totals()
    chapters: dict[str, _Totals] = defaultdict(_Totals)
    tasks: dict[UUID, _Totals] = defaultdict(_Totals)
    locations: dict[str, _Totals] = defaultdict(_Totals)
    for key, pair in pairs.items():
        value = pair.value
        pv = value * shares[pair.task.task_id]
        ev = earned_quantity[key] * pair.item.unit_price
        for bucket in (
            total,
            chapters[pair.item.chapter],
            tasks[pair.task.task_id],
            locations[pair.task.location or NO_LOCATION],
        ):
            bucket.add(value, pv, ev)

    return ControlResult(
        cut=data.cut,
        kind=data.kind,
        has_budget=data.has_budget,
        has_plan=True,
        indicators=Indicators(
            money(total.bac),
            Measure(money(total.pv), money(total.ev)),
            data.contractual_direct_cost,
        ),
        periods=tuple(rows),
        by_chapter=_worst_first(t.row(label) for label, t in chapters.items()),
        by_task=_worst_first(
            t.row(data.tasks[task_id].label, task_id) for task_id, t in tasks.items()
        ),
        by_location=_worst_first(t.row(label) for label, t in locations.items()),
        warnings=Warnings(
            no_baseline=base_warnings.no_baseline,
            undated=base_warnings.undated,
            unpriced=base_warnings.unpriced,
            overrun=tuple(overruns),
            outside_plan=base_warnings.outside_plan,
            percent_tasks=base_warnings.percent_tasks,
        ),
    )


def _outside_plan(
    data: ControlInput, executions: Mapping[Pair, list[Execution]], planned_keys: set[Pair]
) -> tuple[OutsidePlan, ...]:
    """Cantidades netas de ítems que no están en el plan de su tarea (neto 0: no se listan)."""
    found: list[tuple[int, OutsidePlan]] = []
    for (task_id, item_id), rows in executions.items():
        if (task_id, item_id) in planned_keys:
            continue
        quantity = sum((e.quantity for e in rows), ZERO)
        if quantity == 0:
            continue
        task = data.tasks[task_id]
        item = data.items.get(item_id)
        found.append(
            (
                task.order,
                OutsidePlan(
                    task=task.label,
                    item=item.label if item else data.item_labels.get(item_id, UNKNOWN_ITEM),
                    unit=item.unit if item else "",
                    quantity=quantity,
                    value=money(quantity * item.unit_price) if item else None,
                ),
            )
        )
    found.sort(key=lambda entry: (entry[0], entry[1].item))
    return tuple(entry[1] for entry in found)
