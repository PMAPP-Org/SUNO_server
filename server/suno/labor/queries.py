"""Consultas de mano de obra para la web (costos, pieza 6d, spec 3.1 y 3.2). Número fijo de
consultas: las listas, una o dos; la ficha del trabajador, tres."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.core.paginator import Page, Paginator
from django.db.models import Q, QuerySet

from suno.core.context import RequestContext
from suno.core.models import (
    PayrollImport,
    Project,
    ProjectWorker,
    Timesheet,
    TimesheetStatus,
    Worker,
    WorkerWage,
)
from suno.core.scoping import scoped_qs

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


def pending_count(ctx: RequestContext, project: Project) -> int:
    """Planillas en borrador con la planilla firmada: listas para aprobar (decisión 19)."""
    return (
        scoped_qs(ctx, Timesheet)
        .filter(project=project, status=TimesheetStatus.DRAFT, attachment__isnull=False)
        .count()
    )


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


def payroll_rows(ctx: RequestContext) -> QuerySet[PayrollImport]:
    return scoped_qs(ctx, PayrollImport).select_related("imported_by").order_by("-starts_on")[:200]
