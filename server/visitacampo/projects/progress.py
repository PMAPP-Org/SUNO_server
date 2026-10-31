"""% programado y atrasos por tarea, con las dos fuentes de avance (spec, sección 3).

Puro: recibe tareas, el % SUNO por tarea, el calendario y la fecha de corte.
No se guarda nada; se calcula al consultar. Cada indicador sale dos veces,
una con el % SUNO (avance de campo validado) y otra con el % del Project;
una fuente sin dato vale 0.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from .workdays import WorkCalendar


@dataclass(frozen=True)
class TaskInput:
    key: str
    parent_key: str | None
    is_milestone: bool
    baseline_start: date | None
    baseline_finish: date | None
    finish: date | None
    actual_finish: date | None
    project_pct: float | None


@dataclass(frozen=True)
class SourceIndicators:
    actual_pct: float
    delay_pct: float
    delay_days: int | None


@dataclass(frozen=True)
class TaskIndicators:
    planned_pct: float
    #: Días hábiles de la línea base: el peso en la consolidación.
    weight: int
    suno: SourceIndicators
    project: SourceIndicators


def compute_indicators(
    tasks: Sequence[TaskInput],
    suno_pct: Mapping[str, float],
    calendar: WorkCalendar,
    cut: date,
) -> dict[str, TaskIndicators | None]:
    children: dict[str, list[str]] = defaultdict(list)
    for t in tasks:
        if t.parent_key is not None:
            children[t.parent_key].append(t.key)
    by_key = {t.key: t for t in tasks}
    result: dict[str, TaskIndicators | None] = {}

    def visit(key: str) -> TaskIndicators | None:
        if key in result:
            return result[key]
        task = by_key[key]
        if children.get(key):
            own = rollup([i for c in children[key] if (i := visit(c)) is not None])
            if own is not None and task.baseline_start and task.baseline_finish:
                own = replace(
                    own, weight=calendar.count_inclusive(task.baseline_start, task.baseline_finish)
                )
            result[key] = own
        else:
            result[key] = _leaf(task, suno_pct.get(key, 0.0), calendar, cut)
        return result[key]

    for t in tasks:
        visit(t.key)
    return result


def rollup(children: Sequence[TaskIndicators]) -> TaskIndicators | None:
    if not children:
        return None
    total = sum(c.weight for c in children)

    def avg(values: list[float]) -> float:
        if total == 0:
            return sum(values) / len(values)
        return sum(v * c.weight for v, c in zip(values, children, strict=True)) / total

    planned = avg([c.planned_pct for c in children])

    def source(name: str) -> SourceIndicators:
        parts = [getattr(c, name) for c in children]
        actual = avg([p.actual_pct for p in parts])
        delays = [p.delay_days for p in parts if p.delay_days is not None]
        return SourceIndicators(
            actual_pct=round(actual, 1),
            delay_pct=round(planned - actual, 1),
            delay_days=max(delays) if delays else None,
        )

    return TaskIndicators(
        planned_pct=round(planned, 1),
        weight=total,
        suno=source("suno"),
        project=source("project"),
    )


def _leaf(task: TaskInput, suno: float, calendar: WorkCalendar, cut: date) -> TaskIndicators | None:
    start, finish = task.baseline_start, task.baseline_finish
    if start is None or finish is None:
        return None
    weight = 0 if task.is_milestone else calendar.count_inclusive(start, finish)
    if weight == 0:
        planned = 100.0 if cut >= finish else 0.0
    elif cut < start:
        planned = 0.0
    else:
        planned = min(100.0, 100.0 * calendar.count_inclusive(start, min(cut, finish)) / weight)
    return TaskIndicators(
        planned_pct=round(planned, 1),
        weight=weight,
        suno=_source(task, planned, suno, calendar, cut),
        project=_source(task, planned, task.project_pct or 0.0, calendar, cut),
    )


def _source(
    task: TaskInput, planned: float, actual: float, calendar: WorkCalendar, cut: date
) -> SourceIndicators:
    if task.baseline_finish is None:  # `_leaf` solo llama con línea base
        raise ValueError("La tarea no tiene línea base.")
    if actual >= 100:
        projected = task.actual_finish or task.finish
    else:
        projected = task.finish
        if cut > task.baseline_finish and (projected is None or projected < cut):
            projected = cut
    delay_days = (
        calendar.signed_gap(task.baseline_finish, projected) if projected is not None else None
    )
    return SourceIndicators(
        actual_pct=round(actual, 1),
        delay_pct=round(planned - actual, 1),
        delay_days=delay_days,
    )
