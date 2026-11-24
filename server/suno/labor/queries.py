"""Consultas de mano de obra para la web (costos, pieza 6d, spec 3.1 y 3.2). Número fijo de
consultas: las listas, una o dos; la ficha del trabajador, tres."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.paginator import Page, Paginator
from django.db.models import Q, QuerySet

from suno.core.context import RequestContext
from suno.core.models import (
    PayrollImport,
    Project,
    ProjectWorker,
    Timesheet,
    TimesheetLine,
    TimesheetStatus,
    Worker,
    WorkerWage,
)
from suno.core.policies import can_approve_labor
from suno.core.scoping import scoped_qs

from .calc import price, summarize
from .common import factor_history, wage_histories

PAGE_SIZE = 100
STATES = {"activos": "Activos", "retirados": "Retirados", "todos": "Todos"}


def timesheet_page(ctx: RequestContext, project: Project, *, page: int | str | None) -> Page[Any]:
    """Las planillas del proyecto, de la más nueva a la más vieja (borradores por aprobar
    primero no: el orden es el número; la cabecera dice cuántas hay por aprobar)."""
    rows = (
        scoped_qs(ctx, Timesheet)
        .filter(project=project)
        .select_related("created_by")
        .order_by("-number")
    )
    return Paginator(rows, PAGE_SIZE).get_page(page)


def stale_timesheets(ctx: RequestContext, sheets: Iterable[Timesheet]) -> set[UUID]:
    """Los borradores cuyo resumen guardado (lo que se imprimió) ya no es el recálculo de hoy
    por un cambio de jornal o de factor (#106): la comparación de la aprobación
    (`approval.approve_timesheet`). Tres consultas para toda la página (líneas, jornales y
    factor), ninguna si no hay borradores."""
    drafts = {sheet.pk: sheet for sheet in sheets if sheet.status == TimesheetStatus.DRAFT}
    if not drafts:
        return set()
    lines: dict[UUID, list[tuple[UUID, date, str, Decimal]]] = defaultdict(list)
    for sheet_id, worker_id, day, key, fraction in (
        scoped_qs(ctx, TimesheetLine)
        .filter(timesheet_id__in=list(drafts))
        .values_list("timesheet_id", "worker_id", "worked_on", "destination_key", "fraction")
    ):
        lines[sheet_id].append((worker_id, day, key, fraction))
    wages = wage_histories(ctx, (line[0] for found in lines.values() for line in found))
    factors = factor_history(ctx)
    return {
        pk
        for pk, sheet in drafts.items()
        if summarize(price(lines.get(pk, []), wages, factors)).fingerprint()
        != (sheet.summary or {}).get("fingerprint")
    }


def pending_count(ctx: RequestContext, project: Project) -> int:
    """Planillas en borrador con la planilla firmada: listas para aprobar (decisión 19)."""
    return (
        scoped_qs(ctx, Timesheet)
        .filter(project=project, status=TimesheetStatus.DRAFT, attachment__isnull=False)
        .count()
    )


def _awaiting(ctx: RequestContext, project: Project) -> QuerySet[Timesheet]:
    """Borradores firmados (listos para aprobar, decisión 19) que no preparó ni editó quien
    mira: nadie aprueba lo suyo."""
    return (
        scoped_qs(ctx, Timesheet)
        .filter(project=project, status=TimesheetStatus.DRAFT, attachment__isnull=False)
        .exclude(created_by_id=ctx.user_id)
        .exclude(updated_by_id=ctx.user_id)
    )


def inbox_timesheets(ctx: RequestContext, project: Project) -> list[Timesheet]:
    """Para la bandeja "Por aprobar" de Costos (#107): solo a quien aprueba tareo en el
    proyecto; la plantilla muestra número, periodo, proyecto y total, sin trabajadores ni
    jornales. Una consulta (ninguna sin el permiso)."""
    if not can_approve_labor(ctx, project):
        return []
    return list(_awaiting(ctx, project).order_by("starts_on", "number"))


def inbox_timesheet_count(ctx: RequestContext, project: Project) -> int:
    """Para el contador de "Por aprobar" de Costos. Una consulta (ninguna sin el permiso)."""
    return _awaiting(ctx, project).count() if can_approve_labor(ctx, project) else 0


def assignment_rows(ctx: RequestContext, project: Project) -> QuerySet[ProjectWorker]:
    return (
        scoped_qs(ctx, ProjectWorker)
        .filter(project=project)
        .select_related("worker")
        .order_by("worker__full_name", "starts_on")
    )


def active_workers(ctx: RequestContext) -> QuerySet[Worker]:
    """Los que se pueden asignar (el formulario muestra nombre y documento enmascarado)."""
    return scoped_qs(ctx, Worker).filter(is_active=True).order_by("full_name")


@dataclass(frozen=True)
class WorkerFilters:
    text: str = ""
    position: str = ""
    state: str = "activos"

    @classmethod
    def from_query(cls, query: Mapping[str, str]) -> WorkerFilters:
        state = query.get("estado") or "activos"
        return cls(
            text=(query.get("texto") or "").strip()[:100],
            position=(query.get("cargo") or "").strip()[:80],
            state=state if state in STATES else "activos",
        )


def worker_page(
    ctx: RequestContext, filters: WorkerFilters, *, page: int | str | None
) -> Page[Any]:
    rows = scoped_qs(ctx, Worker).order_by("full_name")
    if filters.text:
        rows = rows.filter(Q(full_name__icontains=filters.text)
                           | Q(document_number__icontains=filters.text))  # fmt: skip
    if filters.position:
        rows = rows.filter(position__iexact=filters.position)
    if filters.state != "todos":
        rows = rows.filter(is_active=filters.state == "activos")
    return Paginator(rows, PAGE_SIZE).get_page(page)


def positions(ctx: RequestContext) -> list[str]:
    """Los cargos ya usados (sugerencias del `datalist`)."""
    return list(
        scoped_qs(ctx, Worker)
        .exclude(position="")
        .order_by("position")
        .values_list("position", flat=True)
        .distinct()
    )


@dataclass(frozen=True)
class WorkerSheet:
    worker: Worker
    wages: tuple[WorkerWage, ...]
    assignments: tuple[ProjectWorker, ...]


def worker_sheet(ctx: RequestContext, worker: Worker) -> WorkerSheet:
    return WorkerSheet(
        worker,
        tuple(scoped_qs(ctx, WorkerWage).filter(worker=worker).order_by("-valid_from")),
        tuple(
            scoped_qs(ctx, ProjectWorker)
            .filter(worker=worker)
            .select_related("project")
            .order_by("-starts_on")
        ),
    )


def payroll_rows(ctx: RequestContext, *, page: int | str | None = None) -> Page[Any]:
    """Las nóminas importadas, de la más nueva a la más vieja, de a 100 (decisión 26)."""
    rows = (
        scoped_qs(ctx, PayrollImport)
        .select_related("imported_by")
        .order_by("-starts_on", "-imported_at")
    )
    return Paginator(rows, PAGE_SIZE).get_page(page)
