"""Una planilla de tareo: cuadrícula semanal y vista por día (spec 3.2), decisión, documentos e
importación (Task 15)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from suno.core.context import RequestContext
from suno.core.models import Project, Timesheet
from suno.core.policies import can_delete_labor, can_update_labor
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.materials.destination import destination_options
from suno.projects.views import _ctx, _parse_date

from . import timesheets
from .access import get_labor_project, get_timesheet
from .calc import NO_DAYS
from .common import OPEN_STATES
from .forms import PostedRow, cell_name, read_rows, repeat_previous, split_row, to_lines
from .grid import Grid, GridRow, build_grid, week_of
from .views import page, run


def _editable(ctx: RequestContext, sheet: Timesheet) -> bool:
    return (
        sheet.status in OPEN_STATES
        and can_update_labor(ctx, sheet)
        and (sheet.status == "draft" or sheet.created_by_id == ctx.user_id)
    )


def _posted_grid_rows(
    rows: Sequence[PostedRow], names: dict[UUID, str], days: Sequence[date]
) -> list[GridRow]:
    return [
        GridRow(row.worker_id, names.get(row.worker_id, "—"), "", row.destination, row.task, "",
                tuple(row.cells.get(day, "") for day in days), NO_DAYS, None)
        for row in rows
    ]  # fmt: skip


def _render_sheet(
    request: HttpRequest,
    ctx: RequestContext,
    project: Project,
    sheet: Timesheet,
    *,
    week_start: date | None,
    day: date | None,
    posted: Sequence[PostedRow] | None = None,
    catalog: Catalog | None = None,
) -> HttpResponse:
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    built: Grid = build_grid(ctx, project, sheet, catalog, tasks, week_start)
    days = built.week.days
    names = {row.worker_id: row.worker for row in built.rows}
    rows = list(built.rows) if posted is None else _posted_grid_rows(posted, names, days)
    chosen = day if day in days else days[0]
    position = days.index(chosen)
    url = reverse("labor:timesheet", args=[project.pk, sheet.pk])
    return page(
        request, ctx, project, "suno/labor/timesheet_detail.html",
        sheet=sheet, grid=built, editable=_editable(ctx, sheet), unsaved=posted is not None,
        inputs=[{"index": i, "row": row,
                 "cells": [(cell_name(i, d), v) for d, v in zip(days, row.cells, strict=True)]}
                for i, row in enumerate(rows)],
        day=chosen,
        day_inputs=[{"index": i, "row": row, "name": cell_name(i, chosen),
                     "value": row.cells[position]} for i, row in enumerate(rows)],
        destinations=destination_options(catalog), tasks=list(tasks.values()),
        can_delete=sheet.status in OPEN_STATES and can_delete_labor(ctx, sheet),
        urls={"save": url + "guardar/", "self": url},
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_GET
def timesheet_detail(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    sheet = get_timesheet(ctx, project, timesheet_id, "created_by", "updated_by", "decided_by",
                          "reversed_by")  # fmt: skip
    return _render_sheet(request, ctx, project, sheet,
                         week_start=_parse_date(request.GET.get("semana")),
                         day=_parse_date(request.GET.get("dia")))  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def timesheet_save(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    """Guardar la semana (`guardar`) o el día (`guardar-dia`); `repetir-AAAA-MM-DD` y
    `partir-<fila>` vuelven a pintar sin guardar; `recalcular` guarda el resumen otra vez."""
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    sheet = get_timesheet(ctx, project, timesheet_id)
    if not can_update_labor(ctx, sheet):
        raise PermissionDenied
    action = request.POST.get("accion", "guardar")
    week = week_of(sheet, _parse_date(request.POST.get("semana")))
    back = f"{reverse('labor:timesheet', args=[project.pk, sheet.pk])}?semana={week.start}"
    if action == "recalcular":
        run(request, lambda: timesheets.recalculate(ctx, project, sheet.pk),
            "Resumen recalculado: vuelve a imprimir la planilla y a cargarla firmada.")  # fmt: skip
        return redirect(back)
    day = _parse_date(request.POST.get("dia"))
    by_day = action == "guardar-dia" and day is not None and day in week.days
    days = [day] if by_day and day is not None else list(week.days)
    rows = read_rows(request.POST, days)
    if action.startswith("repetir-"):
        target = _parse_date(action.removeprefix("repetir-"))
        rows = repeat_previous(rows, target, week.days) if target else rows
        return _render_sheet(
            request, ctx, project, sheet, week_start=week.start, day=day, posted=rows
        )
    if action.startswith("partir-"):
        try:
            index = int(action.removeprefix("partir-"))
        except ValueError:
            index = -1
        rows = split_row(rows, index, week.days)
        return _render_sheet(
            request, ctx, project, sheet, week_start=week.start, day=day, posted=rows
        )
    catalog = load_catalog(ctx, project)
    outlines = timesheets.outline_index(assignable_tasks(ctx, project))
    names = timesheets.worker_names(ctx, {row.worker_id for row in rows})
    lines, errors = to_lines(rows, catalog, outlines, names)
    if not errors and run(request, lambda: timesheets.save_lines(ctx, project, sheet.pk, lines,
                                                                 days, catalog=catalog),
                          "Día guardado." if by_day else "Semana guardada."):  # fmt: skip
        return redirect(f"{back}&dia={day}" if by_day else back)
    for error in errors[:5]:
        messages.error(request, error)
    if by_day:
        return redirect(f"{back}&dia={day}")
    sheet.refresh_from_db()
    return _render_sheet(request, ctx, project, sheet, week_start=week.start, day=day,
                         posted=rows, catalog=catalog)  # fmt: skip
