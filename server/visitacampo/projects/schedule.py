"""El cronograma vigente de un proyecto, listo para mostrar o exportar."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.utils import timezone

from visitacampo.core.context import RequestContext
from visitacampo.core.models import AppUser, Project, Task, TaskResource
from visitacampo.core.scoping import scoped_qs

from .access import effective_responsibles
from .progress import TaskIndicators, TaskInput, compute_indicators, rollup
from .workdays import WorkCalendar


@dataclass(frozen=True)
class ScheduleRow:
    task: Task
    responsibles: list[AppUser]
    executors: list[str]
    indicators: TaskIndicators | None


@dataclass(frozen=True)
class Schedule:
    project: Project
    cut: date
    rows: list[ScheduleRow]
    stages: list[Task]
    total: TaskIndicators | None


def build_schedule(
    ctx: RequestContext,
    project: Project,
    *,
    cut: date | None = None,
    suno_pct: Mapping[UUID, float] | None = None,
) -> Schedule:
    """Tareas vigentes en el orden del Project, con sus indicadores.

    `suno_pct`: % SUNO por id de tarea (avance de campo validado). Lo
    alimenta la parte de campo de la etapa 1; sin él, todo vale 0.
    """
    cut = cut or project.status_date or timezone.localdate()
    tasks = list(
        scoped_qs(ctx, Task).filter(project=project, retired_in__isnull=True).order_by("sort_order")
    )
    executors: dict[UUID, list[str]] = defaultdict(list)
    for task_id, name in (
        scoped_qs(ctx, TaskResource)
        .filter(task__project=project)
        .order_by("resource__name")
        .values_list("task_id", "resource__name")
    ):
        executors[task_id].append(name)
    responsibles = effective_responsibles(ctx, project)

    inputs = [
        TaskInput(
            key=str(t.pk),
            parent_key=str(t.parent_id) if t.parent_id else None,
            is_milestone=t.is_milestone,
            baseline_start=t.baseline_start,
            baseline_finish=t.baseline_finish,
            finish=t.finish,
            actual_finish=t.actual_finish,
            project_pct=float(t.project_pct) if t.project_pct is not None else None,
        )
        for t in tasks
    ]
    suno = {str(k): v for k, v in (suno_pct or {}).items()}
    indicators = compute_indicators(inputs, suno, WorkCalendar.from_json(project.calendar), cut)
    rows = [
        ScheduleRow(
            task=t,
            responsibles=responsibles.get(t.pk, []),
            executors=executors.get(t.pk, []),
            indicators=indicators[str(t.pk)],
        )
        for t in tasks
    ]
    stages = [t for t in tasks if t.outline_level == 1]
    total = rollup([i for s in stages if (i := indicators[str(s.pk)]) is not None])
    return Schedule(project=project, cut=cut, rows=rows, stages=stages, total=total)


def _is_delayed(row: ScheduleRow, source: str | None) -> bool:
    if row.indicators is None:
        return False
    sources = [source] if source else ["suno", "project"]
    for name in sources:
        part = getattr(row.indicators, name)
        if part.delay_pct > 0 or (part.delay_days or 0) > 0:
            return True
    return False


def filter_rows(
    rows: Sequence[ScheduleRow],
    *,
    stage_id: UUID | None = None,
    responsible_id: UUID | None = None,
    critical_only: bool = False,
    delayed_only: bool = False,
    source: str | None = None,
) -> list[ScheduleRow]:
    """`source` = "suno" o "project" limita "atrasadas" a esa fuente; sin él, cualquiera."""
    out = []
    for row in rows:
        if stage_id and row.task.stage_id != stage_id:
            continue
        if responsible_id and responsible_id not in {u.pk for u in row.responsibles}:
            continue
        if critical_only and not row.task.is_critical:
            continue
        if delayed_only and not _is_delayed(row, source):
            continue
        out.append(row)
    return out
