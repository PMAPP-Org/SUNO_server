"""Una planilla de tareo: cuadrícula semanal y vista por día (spec 3.2), decisión, documentos e
importación (Task 15)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.attachments import validation
from suno.attachments.storage import get_blob_store
from suno.budgets.excel import MAX_BYTES, ExcelRejected, check_file_name
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.download import content_disposition
from suno.core.models import Project, Timesheet
from suno.core.policies import (
    can_approve_labor,
    can_delete_labor,
    can_reverse_labor,
    can_update_labor,
)
from suno.core.scoping import blob_registry
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.materials.destination import destination_options
from suno.materials.views import download as materials_download
from suno.projects.views import _ctx, _parse_date

from . import approval, timesheet_import, timesheets
from .access import get_labor_project, get_timesheet
from .calc import NO_DAYS
from .common import OPEN_STATES, LaborRejected
from .export import timesheet_workbook
from .forms import PostedRow, cell_name, read_rows, repeat_previous, split_row, to_lines
from .grid import Grid, GridRow, build_grid, week_of
from .pdf import timesheet_pdf
from .views import XLSX_TYPE, download, page, require_export, run


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
        request,
        ctx,
        project,
        "suno/labor/timesheet_detail.html",
        sheet=sheet,
        grid=built,
        editable=_editable(ctx, sheet),
        unsaved=posted is not None,
        inputs=[
            {
                "index": i,
                "row": row,
                "cells": [(cell_name(i, d), v) for d, v in zip(days, row.cells, strict=True)],
            }
            for i, row in enumerate(rows)
        ],
        day=chosen,
        day_inputs=[
            {"index": i, "row": row, "name": cell_name(i, chosen), "value": row.cells[position]}
            for i, row in enumerate(rows)
        ],
        destinations=destination_options(catalog),
        tasks=list(tasks.values()),
        can_delete=sheet.status in OPEN_STATES and can_delete_labor(ctx, sheet),
        can_attach=sheet.status == "draft" and can_update_labor(ctx, sheet),
        can_decide=sheet.status == "draft"
        and can_approve_labor(ctx, project)
        and ctx.user_id not in (sheet.created_by_id, sheet.updated_by_id),
        can_reverse=sheet.status == "approved" and can_reverse_labor(ctx, project),
        needs="" if sheet.attachment_id else "Falta la planilla firmada",
        urls={
            "save": url + "guardar/",
            "self": url,
            "approve": url + "aprobar/",
            "reject": url + "rechazar/",
            "void": url + "anular/",
            "upload": url + "firmada/",
            "view": url + "firmada/ver/",
            "pdf": url + "planilla.pdf",
            "xlsx": url + "planilla.xlsx",
            "import": url + "importar/",
            "delete": url + "borrar/",
        },
    )


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
    try:
        rows = read_rows(request.POST, days)
    except LaborRejected as exc:
        for error in exc.errors:
            messages.error(request, error)
        return redirect(back)
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


def _detail(project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    return redirect("labor:timesheet", project_id, timesheet_id)


@login_required(login_url="platform_login")
@require_POST
def timesheet_delete(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    if run(request, lambda: timesheets.delete_timesheet(ctx, project, timesheet_id),
           "Planilla borrada."):  # fmt: skip
        return redirect("labor:timesheets", project.pk)
    return _detail(project.pk, timesheet_id)


@login_required(login_url="platform_login")
@require_POST
def timesheet_signed(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    upload = request.FILES.get("file")
    if upload is not None:
        name = upload.name or "planilla-firmada.pdf"
        run(request, lambda: timesheets.attach_signed(ctx, project, timesheet_id, upload, name),
            "Planilla firmada adjunta; ya se puede aprobar.")  # fmt: skip
    return _detail(project.pk, timesheet_id)


@login_required(login_url="platform_login")
@require_GET
def timesheet_signed_file(
    request: HttpRequest, project_id: UUID, timesheet_id: UUID
) -> HttpResponse:
    """La planilla firmada, por URL prefirmada, después de comprobar que se lee la pestaña (el
    `Blob` no tiene organización), como `materials.common.attachment_url`."""
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    sheet = get_timesheet(ctx, project, timesheet_id)
    blob = blob_registry().filter(pk=sheet.attachment_id).first() if sheet.attachment_id else None
    if blob is None:
        raise Http404
    url = get_blob_store().presign_download(
        sha256=blob.sha256,
        expires_in=int(settings.ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS),
        mime=blob.mime,
        filename=validation.generated_filename(blob.mime),
    )
    return materials_download(url)


@login_required(login_url="platform_login")
@require_POST
def timesheet_approve(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    run(request, lambda: approval.approve_timesheet(ctx, project, timesheet_id),
        "Planilla aprobada: su costo de mano de obra está en el libro.")  # fmt: skip
    return _detail(project.pk, timesheet_id)


@login_required(login_url="platform_login")
@require_POST
def timesheet_reject(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: approval.reject_timesheet(ctx, project, timesheet_id, note),
        "Planilla rechazada; su autor la verá con el motivo.")  # fmt: skip
    return _detail(project.pk, timesheet_id)


@login_required(login_url="platform_login")
@require_POST
def timesheet_reverse(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: approval.reverse_timesheet(ctx, project, timesheet_id, note),
        "Planilla anulada: sus reversos están en el libro. Para corregir, crea otra con el mismo "
        "periodo.")  # fmt: skip
    return _detail(project.pk, timesheet_id)


@login_required(login_url="platform_login")
@require_GET
def timesheet_document(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    require_export(ctx, project)
    sheet = get_timesheet(ctx, project, timesheet_id)
    content = timesheet_pdf(ctx, project, sheet, generated_on=timezone.localdate())
    return download(
        ctx,
        project,
        content,
        stem=f"planilla-{sheet.code}",
        suffix=".pdf",
        content_type="application/pdf",
        action=AuditAction.TIMESHEET_EXPORTED,
        target=f"timesheet:{sheet.pk}",
        number=sheet.number,
        status=sheet.status,
    )


@login_required(login_url="platform_login")
@require_GET
def timesheet_sheet(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    require_export(ctx, project)
    sheet = get_timesheet(ctx, project, timesheet_id)
    content = timesheet_workbook(ctx, sheet, load_catalog(ctx, project))
    return download(ctx, project, content, stem=f"planilla-{sheet.code}", suffix=".xlsx",
                    content_type=XLSX_TYPE, action=AuditAction.LABOR_EXPORTED,
                    target=f"timesheet:{sheet.pk}", what="planilla")  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def timesheet_import_upload(
    request: HttpRequest, project_id: UUID, timesheet_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    sheet = get_timesheet(ctx, project, timesheet_id)
    if not can_update_labor(ctx, sheet):
        raise PermissionDenied
    errors: list[str] = []
    result = None
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            errors = ["Elige un archivo .xlsx."]
        elif upload.size is not None and upload.size > MAX_BYTES:
            errors = ["El archivo supera 20 MB."]
        else:
            try:
                check_file_name(upload.name or "")
                result = timesheet_import.preview(ctx, project, sheet, upload.read())
            except ExcelRejected as exc:
                errors = [str(exc)]
            except LaborRejected as exc:
                errors = exc.errors
    return page(request, ctx, project, "suno/labor/timesheet_import.html", sheet=sheet,
                errors=errors, result=result)  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def timesheet_import_apply(
    request: HttpRequest, project_id: UUID, timesheet_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    added: list[int] = []
    if run(
        request,
        lambda: added.append(
            timesheet_import.apply(ctx, project, timesheet_id, request.POST.get("token", ""))
        ),
        "Tareo importado.",
    ):
        return _detail(project.pk, timesheet_id)
    return redirect("labor:import_timesheet", project.pk, timesheet_id)


@login_required(login_url="platform_login")
@require_GET
def timesheet_import_template(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    get_labor_project(ctx, project_id)
    response = HttpResponse(timesheet_import.template_bytes(), content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        "plantilla-tareo", ".xlsx", fallback="plantilla-tareo"
    )
    return response
