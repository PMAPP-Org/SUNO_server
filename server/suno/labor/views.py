"""Pestaña "Tareo" del proyecto (costos, pieza 6d, spec 3.2): asignaciones y planillas, y las
piezas que comparten las vistas de la planilla (`views_timesheets`).

Quien no lee Mano de obra en el proyecto recibe 404 (`access.get_labor_project`); quien la lee
pero no tiene la capacidad de una acción, 403. Las reglas viven en los servicios."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.core import audit
from suno.core.context import RequestContext
from suno.core.download import content_disposition
from suno.core.models import Project
from suno.core.policies import (
    can_approve_labor,
    can_create_labor,
    can_export_labor,
    can_reverse_labor,
    can_update_labor,
)
from suno.projects.views import _ctx, _parse_date, _project_context, _render

from . import assignments, timesheets
from .access import get_labor_project
from .assignments import AssignmentData, get_assignment
from .common import LaborRejected
from .forms import AssignmentForm, PeriodForm
from .queries import (
    active_workers,
    assignment_rows,
    pending_count,
    stale_timesheets,
    timesheet_page,
)

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def tab_context(ctx: RequestContext, project: Project) -> dict[str, Any]:
    return {
        **_project_context(ctx, project),
        "can_create_labor": can_create_labor(ctx, project),
        "can_approve_labor": can_approve_labor(ctx, project),
        "can_reverse_labor": can_reverse_labor(ctx, project),
        "can_export_labor": can_export_labor(ctx, project),
        "labor_pending": pending_count(ctx, project),
    }


def page(
    request: HttpRequest, ctx: RequestContext, project: Project, template: str, **context: Any
) -> HttpResponse:
    return _render(request, ctx, template, {**tab_context(ctx, project), **context})


def run(request: HttpRequest, action: Callable[[], Any], success: str) -> bool:
    try:
        action()
    except LaborRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return False
    messages.success(request, success)
    return True


def require_export(ctx: RequestContext, project: Project) -> None:
    """Antes de armar un Excel o un PDF: sin permiso de exportar, 403 sin leer los datos."""
    if not can_export_labor(ctx, project):
        raise PermissionDenied


def download(
    ctx: RequestContext,
    project: Project,
    content: bytes,
    *,
    stem: str,
    suffix: str,
    content_type: str,
    action: str,
    target: str,
    **metadata: Any,
) -> HttpResponse:
    """Un PDF o un Excel auditado."""
    require_export(ctx, project)
    with transaction.atomic():
        audit.record(ctx, action=action, target=target,
                     metadata={"project": str(project.pk), **metadata})  # fmt: skip
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = content_disposition(stem, suffix, fallback=stem)
    return response


@login_required(login_url="platform_login")
@require_GET
def timesheet_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    start, end = timesheets.default_period(timezone.localdate())
    sheets = timesheet_page(ctx, project, page=request.GET.get("pagina"))
    return page(request, ctx, project, "suno/labor/timesheets.html", page=sheets,
                stale=stale_timesheets(ctx, sheets.object_list),
                form=PeriodForm(initial={"starts_on": start, "ends_on": end}))  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def timesheet_create(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    if not can_create_labor(ctx, project):
        raise PermissionDenied
    form = PeriodForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Escribe las fechas de la planilla.")
        return redirect("labor:timesheets", project.pk)
    created: list[Any] = []
    if run(request, lambda: created.append(timesheets.create_timesheet(
            ctx, project, form.cleaned_data["starts_on"], form.cleaned_data["ends_on"])),
           "Planilla creada: llena la cuadrícula y guárdala."):  # fmt: skip
        return redirect("labor:timesheet", project.pk, created[0].pk)
    return redirect("labor:timesheets", project.pk)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def assignment_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    form = AssignmentForm(request.POST or None, workers=list(active_workers(ctx)))
    if request.method == "POST":
        if not can_create_labor(ctx, project):
            raise PermissionDenied
        if form.is_valid() and run(request, lambda: assignments.create_assignment(
                ctx, project, AssignmentData(UUID(form.cleaned_data["worker"]),
                                             form.cleaned_data["starts_on"],
                                             form.cleaned_data.get("ends_on"))),
                "Trabajador asignado."):  # fmt: skip
            return redirect("labor:assignments", project.pk)
    rows = list(assignment_rows(ctx, project))
    return page(request, ctx, project, "suno/labor/assignments.html", form=form,
                rows=[(row, can_update_labor(ctx, row)) for row in rows])  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def assignment_update(request: HttpRequest, project_id: UUID, assignment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    row = get_assignment(ctx, project, assignment_id)
    if request.POST.get("accion") == "borrar":
        run(request, lambda: assignments.delete_assignment(ctx, project, row.pk),
            "Asignación borrada.")  # fmt: skip
    else:
        starts = _parse_date(request.POST.get("starts_on"))
        ends = _parse_date(request.POST.get("ends_on"))  # vacío: sin fin
        if starts is None:
            messages.error(request, "Escribe desde cuándo.")
        else:
            data = AssignmentData(row.worker_id, starts, ends)
            run(request, lambda: assignments.update_assignment(ctx, project, row.pk, data),
                "Asignación actualizada.")  # fmt: skip
    return redirect("labor:assignments", project.pk)
