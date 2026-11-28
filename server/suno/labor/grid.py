"""Cuadrícula semanal de una planilla y vista por día (costos, pieza 6d, spec 3.2).

- Una fila por trabajador y destino, una columna por día de la semana (lunes a domingo,
  recortada al periodo) y la fracción en cada celda. Los trabajadores asignados en el periodo
  sin tareo aparecen con una fila vacía (destino Indirecto).
- Totales por día (de la semana) y, del periodo completo, por persona y por destino, con el
  costo estimado de hoy (en una aprobada o anulada, el congelado); `changed` avisa si un
  borrador difiere del resumen guardado (lo que se firmó).
- Seis consultas: las cinco de `timesheets.evaluate` y las asignaciones del periodo.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q

from suno.budgets.totals import ZERO
from suno.core.context import RequestContext
from suno.core.models import Project, ProjectWorker, Task, Timesheet
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog
from suno.materials.destination import (
    INDIRECT_LABEL,
    check_destination,
    destination_key,
    destination_text,
    key_text,
)

from .calc import NO_DAYS, days_text, is_stale
from .calc import problems as find_problems
from .common import FROZEN_STATES
from .timesheets import StoredLine, evaluate, hashed, outline_index, read_destination


@dataclass(frozen=True)
class Week:
    days: tuple[date, ...]
    start: date
    previous: date | None
    next: date | None


@dataclass(frozen=True)
class GridRow:
    worker_id: UUID
    #: "Pedro Pérez · CC ***5678"
    worker: str
    position: str
    #: Lo que va en el campo "Destino" ("" = Indirecto) y en "Tarea" (número de esquema).
    destination: str
    task: str
    #: La etiqueta del destino ("Indirecto (proyecto)", "2.1 Concreto · tarea 2.1 Columnas").
    label: str
    #: Una por día de la semana: "1", "0,5" o "".
    cells: tuple[str, ...]
    days: Decimal
    cost: Decimal | None
    #: El destino guardado ya no se puede escribir con el presupuesto vigente (se borró un ítem
    #: o un capítulo, o la tarea dejó de estar): la fila lo muestra por su etiqueta, sin
    #: cambiarlo, y no se guarda con días (`forms.to_lines`).
    lost: bool = False


@dataclass(frozen=True)
class Grid:
    sheet: Timesheet
    week: Week
    rows: tuple[GridRow, ...]
    day_totals: tuple[Decimal, ...]
    #: Del periodo completo: (etiqueta, jornales, costo).
    by_worker: tuple[tuple[str, Decimal, Decimal], ...]
    by_destination: tuple[tuple[str, Decimal, Decimal], ...]
    days: Decimal
    total: Decimal
    problems: tuple[str, ...]
    #: Lo de hoy difiere del resumen guardado (cambió un jornal o el factor).
    changed: bool


def cell_text(value: Decimal | None) -> str:
    return days_text(value) if value else ""


def week_of(sheet: Timesheet, start: date | None) -> Week:
    """La semana (lunes a domingo) que contiene `start`, recortada al periodo; si la semana de
    `start` no toca el periodo, la primera."""
    if start is None or not sheet.starts_on - timedelta(days=6) <= start <= sheet.ends_on:
        start = sheet.starts_on
    monday = start - timedelta(days=start.weekday())
    if monday + timedelta(days=6) < sheet.starts_on:
        monday = sheet.starts_on - timedelta(days=sheet.starts_on.weekday())
    first = max(monday, sheet.starts_on)
    last = min(monday + timedelta(days=6), sheet.ends_on)
    days = tuple(first + timedelta(days=n) for n in range((last - first).days + 1))
    previous = monday - timedelta(days=7) if monday > sheet.starts_on else None
    following = monday + timedelta(days=7)
    return Week(days, monday, previous, following if following <= sheet.ends_on else None)


def _writable(
    catalog: Catalog,
    tasks: dict[UUID, Task],
    outlines: dict[str, UUID],
    line: StoredLine,
    text: str,
) -> bool:
    """Volver a leer `text` (y la tarea) da el mismo destino que el guardado."""
    destination, _error = read_destination(text, line.task_outline, catalog, outlines)
    if destination is None:
        return False
    problems, assignment = check_destination(catalog, tasks, destination)
    if assignment is None or problems:
        return False
    key = destination_key(assignment.scope, assignment.chapter_id, assignment.item_ids,
                          assignment.task_id)  # fmt: skip
    return hashed(key_text(key)) == line.key


def build_grid(
    ctx: RequestContext,
    project: Project,
    sheet: Timesheet,
    catalog: Catalog,
    tasks: dict[UUID, Task],
    start: date | None,
) -> Grid:
    week = week_of(sheet, start)
    evaluation = evaluate(ctx, sheet, catalog)
    names = dict(evaluation.names)
    positions: dict[UUID, str] = {}
    for row in (
        scoped_qs(ctx, ProjectWorker)
        .filter(project=project, starts_on__lte=sheet.ends_on)
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=sheet.starts_on))
        .select_related("worker")
    ):
        names.setdefault(row.worker_id, row.worker.label)
        positions[row.worker_id] = row.worker.position
    index = {day: n for n, day in enumerate(week.days)}
    grouped: dict[tuple[UUID, str], dict[str, Any]] = {}
    by_worker: dict[UUID, list[Decimal]] = defaultdict(lambda: [NO_DAYS, ZERO])
    by_key: dict[str, list[Decimal]] = defaultdict(lambda: [NO_DAYS, ZERO])
    day_totals = [NO_DAYS] * len(week.days)
    for line, cost in zip(evaluation.lines, evaluation.costs, strict=True):
        amount = cost.amount or ZERO
        by_worker[line.worker_id][0] += line.fraction
        by_worker[line.worker_id][1] += amount
        by_key[line.key][0] += line.fraction
        by_key[line.key][1] += amount
        entry = grouped.setdefault((line.worker_id, line.key), {
            "line": line, "cells": [None] * len(week.days), "days": NO_DAYS, "cost": ZERO,
            "complete": True,
        })  # fmt: skip
        if line.day in index:
            entry["cells"][index[line.day]] = line.fraction
            entry["days"] += line.fraction
            entry["cost"] += amount
            entry["complete"] &= cost.amount is not None
            day_totals[index[line.day]] += line.fraction
    rows: list[GridRow] = []
    outlines = outline_index(tasks)
    for (worker_id, key), entry in grouped.items():
        line = entry["line"]
        label = evaluation.labels.get(key, "—")
        text = destination_text(catalog, line.scope, line.chapter_id, line.item_ids)
        lost = not _writable(catalog, tasks, outlines, line, text)
        rows.append(GridRow(
            worker_id, names.get(worker_id, "—"), positions.get(worker_id, ""),
            label if lost else text, line.task_outline, label,
            tuple(cell_text(value) for value in entry["cells"]), entry["days"],
            entry["cost"] if entry["complete"] else None, lost,
        ))  # fmt: skip
    with_rows = {row.worker_id for row in rows}
    for worker_id in positions:
        if worker_id not in with_rows:
            rows.append(GridRow(worker_id, names[worker_id], positions[worker_id], "", "",
                                INDIRECT_LABEL, ("",) * len(week.days), NO_DAYS, ZERO))  # fmt: skip
    rows.sort(key=lambda row: (row.worker, row.destination != "", row.label))
    return Grid(
        sheet=sheet,
        week=week,
        rows=tuple(rows),
        day_totals=tuple(day_totals),
        by_worker=tuple(sorted(((names.get(w, "—"), d, a) for w, (d, a) in by_worker.items()),
                               key=lambda r: r[0])),
        by_destination=tuple(sorted(((evaluation.labels.get(k, "—"), d, a)
                                     for k, (d, a) in by_key.items()), key=lambda r: r[0])),
        days=evaluation.summary.days,
        total=evaluation.summary.total,
        problems=tuple(find_problems(evaluation.costs, evaluation.names)),
        changed=sheet.status not in FROZEN_STATES and is_stale(evaluation.summary, sheet.summary),
    )  # fmt: skip


def day_rows(built: Grid, day: date) -> list[tuple[GridRow, str]]:
    """La vista por día (390 px): cada fila con la fracción de ese día."""
    if day not in built.week.days:
        return []
    position = built.week.days.index(day)
    return [(row, row.cells[position]) for row in built.rows]


@dataclass(frozen=True)
class PeriodTable:
    """La cuadrícula del periodo completo (PDF y Excel): una fila por persona y destino."""

    days: tuple[date, ...]
    #: (trabajador, destino, celdas por día, jornales, costo)
    rows: tuple[tuple[str, str, tuple[str, ...], Decimal, Decimal], ...]
    day_totals: tuple[Decimal, ...]


def period_table(ctx: RequestContext, sheet: Timesheet, catalog: Catalog) -> PeriodTable:
    """Cinco consultas (`evaluate`)."""
    evaluation = evaluate(ctx, sheet, catalog)
    days = tuple(sheet.starts_on + timedelta(days=n)
                 for n in range((sheet.ends_on - sheet.starts_on).days + 1))  # fmt: skip
    index = {day: n for n, day in enumerate(days)}
    grouped: dict[tuple[UUID, str], list[Any]] = {}
    totals = [NO_DAYS] * len(days)
    for line, cost in zip(evaluation.lines, evaluation.costs, strict=True):
        entry = grouped.setdefault((line.worker_id, line.key), [[None] * len(days), NO_DAYS, ZERO])
        entry[0][index[line.day]] = line.fraction
        entry[1] += line.fraction
        entry[2] += cost.amount or ZERO
        totals[index[line.day]] += line.fraction
    rows = [
        (evaluation.names.get(worker, "—"), evaluation.labels.get(key, "—"),
         tuple(cell_text(v) for v in cells), days_total, amount)
        for (worker, key), (cells, days_total, amount) in grouped.items()
    ]  # fmt: skip
    rows.sort(key=lambda row: (row[0], row[1]))
    return PeriodTable(days, tuple(rows), tuple(totals))
