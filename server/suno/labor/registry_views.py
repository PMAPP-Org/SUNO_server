"""Registro de trabajadores y factor prestacional en la web (costos, pieza 6d, spec 3.1).

Ver pide `can_read_labor_registry` (404 si no); escribir, `can_manage_labor_registry` (403).
Las reglas viven en los servicios: aquí solo se traducen a mensajes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.budgets.excel import MAX_BYTES, ExcelRejected, check_file_name
from suno.core.context import RequestContext
from suno.core.download import content_disposition
from suno.core.models import BenefitFactor, WorkerWage
from suno.core.policies import can_export_labor_registry, can_manage_labor_registry
from suno.core.scoping import scoped_qs
from suno.projects.views import _render

from . import registry, registry_import
from .access import get_worker, registry_ctx, registry_manager
from .common import LaborRejected
from .forms import FactorForm, NewWorkerForm, RetireForm, WageForm, WorkerForm
from .queries import STATES, WorkerFilters, positions, worker_page, worker_sheet

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def page(request: HttpRequest, ctx: RequestContext, template: str, **context: Any) -> HttpResponse:
    return _render(
        request,
        ctx,
        template,
        {
            "sidebar_active": "labor",
            "can_manage_registry": can_manage_labor_registry(ctx, ctx.org_id),
            "can_export_registry": can_export_labor_registry(ctx, ctx.org_id),
            **context,
        },
    )


def run(request: HttpRequest, action: Callable[[], Any], success: str) -> bool:
    try:
        action()
    except LaborRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return False
    messages.success(request, success)
    return True


@login_required(login_url="platform_login")
@require_GET
def worker_list(request: HttpRequest) -> HttpResponse:
    ctx = registry_ctx(request)
    filters = WorkerFilters.from_query(request.GET)
    query = urlencode({k: v for k, v in (("texto", filters.text), ("cargo", filters.position),
                                         ("estado", filters.state)) if v})  # fmt: skip
    return page(request, ctx, "suno/labor/workers.html", filters=filters, states=STATES,
                positions=positions(ctx), query=query,
                page=worker_page(ctx, filters, page=request.GET.get("pagina")))  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def worker_new(request: HttpRequest) -> HttpResponse:
    ctx = registry_manager(request)
    form = NewWorkerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        created: list[Any] = []
        if run(request, lambda: created.append(registry.create_worker(
                ctx, form.worker_data(), form.cleaned_data["daily_wage"])),
               "Trabajador registrado."):  # fmt: skip
            return redirect("labor_registry:worker", created[0].pk)
    return page(request, ctx, "suno/labor/worker_form.html", form=form, worker=None,
                positions=positions(ctx))  # fmt: skip


@login_required(login_url="platform_login")
@require_GET
def worker_detail(request: HttpRequest, worker_id: UUID) -> HttpResponse:
    ctx = registry_ctx(request)
    sheet = worker_sheet(ctx, get_worker(ctx, worker_id))
    wages = [
        (wage, registry.wage_in_use(ctx, wage)) for wage in sheet.wages
    ]  # una consulta por fila: el historial de un trabajador es corto
    return page(request, ctx, "suno/labor/worker_detail.html", sheet=sheet, wages=wages,
                wage_form=WageForm(), retire_form=RetireForm())  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def worker_edit(request: HttpRequest, worker_id: UUID) -> HttpResponse:
    ctx = registry_manager(request)
    worker = get_worker(ctx, worker_id)
    initial = {"document_type": worker.document_type, "document_number": worker.document_number,
               "full_name": worker.full_name, "position": worker.position,
               "hired_on": worker.hired_on}  # fmt: skip
    form = WorkerForm(request.POST or None, initial=initial)
    if (
        request.method == "POST"
        and form.is_valid()
        and run(
            request,
            lambda: registry.update_worker(ctx, worker.pk, form.worker_data()),
            "Trabajador actualizado.",
        )
    ):
        return redirect("labor_registry:worker", worker.pk)
    return page(request, ctx, "suno/labor/worker_form.html", form=form, worker=worker,
                positions=positions(ctx))  # fmt: skip


def _back(worker_id: UUID) -> HttpResponse:
    return redirect("labor_registry:worker", worker_id)


@login_required(login_url="platform_login")
@require_POST
def worker_wage(request: HttpRequest, worker_id: UUID) -> HttpResponse:
    ctx = registry_manager(request)
    form = WageForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        run(
            request,
            lambda: registry.add_wage(ctx, worker_id, data["valid_from"], data["daily_wage"]),
            "Jornal registrado.",
        )
    else:
        messages.error(request, "Escribe la fecha y el jornal.")
    return _back(worker_id)


@login_required(login_url="platform_login")
@require_POST
def wage_update(request: HttpRequest, worker_id: UUID, wage_id: UUID) -> HttpResponse:
    ctx = registry_manager(request)
    found = scoped_qs(ctx, WorkerWage).filter(pk=wage_id, worker_id=worker_id).exists()
    if not found:
        return _back(worker_id)
    if request.POST.get("accion") == "borrar":
        run(request, lambda: registry.delete_wage(ctx, wage_id), "Jornal borrado.")
    else:
        form = WageForm(request.POST)
        if form.is_valid():
            run(
                request,
                lambda: registry.change_wage(ctx, wage_id, form.cleaned_data["daily_wage"]),
                "Jornal corregido.",
            )
    return _back(worker_id)


@login_required(login_url="platform_login")
@require_POST
def worker_retire(request: HttpRequest, worker_id: UUID) -> HttpResponse:
    ctx = registry_manager(request)
    if request.POST.get("accion") == "reincorporar":
        run(request, lambda: registry.reinstate_worker(ctx, worker_id), "Retiro deshecho.")
        return _back(worker_id)
    form = RetireForm(request.POST)
    if form.is_valid():
        run(
            request,
            lambda: registry.retire_worker(ctx, worker_id, form.cleaned_data["retired_on"]),
            "Trabajador retirado.",
        )
    return _back(worker_id)


@login_required(login_url="platform_login")
@require_POST
def worker_delete(request: HttpRequest, worker_id: UUID) -> HttpResponse:
    ctx = registry_manager(request)
    if run(request, lambda: registry.delete_worker(ctx, worker_id), "Trabajador borrado."):
        return redirect("labor_registry:list")
    return _back(worker_id)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_upload(request: HttpRequest) -> HttpResponse:
    ctx = registry_manager(request)
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
            except ExcelRejected as exc:
                errors = [str(exc)]
            else:
                result = registry_import.preview(ctx, upload.read())
    actions = (registry_import.NEW, registry_import.UPDATE, registry_import.SAME)
    counts = [(action, result.count(action)) for action in actions] if result else []
    return page(request, ctx, "suno/labor/workers_import.html", errors=errors, result=result,
                counts=counts)  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def import_apply(request: HttpRequest) -> HttpResponse:
    ctx = registry_manager(request)
    try:
        created, updated = registry_import.apply(ctx, request.POST.get("token", ""))
    except LaborRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return redirect("labor_registry:import")
    messages.success(
        request, f"Trabajadores importados: {created} nuevos y {updated} actualizados."
    )
    return redirect("labor_registry:list")


@login_required(login_url="platform_login")
@require_GET
def import_template(request: HttpRequest) -> HttpResponse:
    registry_manager(request)
    response = HttpResponse(registry_import.template_bytes(), content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        "plantilla-trabajadores", ".xlsx", fallback="plantilla-trabajadores"
    )
    return response


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def factor_list(request: HttpRequest) -> HttpResponse:
    ctx = registry_ctx(request)
    form = FactorForm(request.POST or None)
    if request.method == "POST":
        ctx = registry_manager(request)
        if form.is_valid():
            data = form.cleaned_data
            if run(request, lambda: registry.add_factor(ctx, data["valid_from"], data["factor"],
                                                        data.get("note") or ""),
                   "Factor registrado."):  # fmt: skip
                return redirect("labor_registry:factors")
    rows = [(f, registry.factor_in_use(ctx, f))
            for f in scoped_qs(ctx, BenefitFactor).order_by("-valid_from")]  # fmt: skip
    return page(request, ctx, "suno/labor/factors.html", form=form, rows=rows)


@login_required(login_url="platform_login")
@require_POST
def factor_update(request: HttpRequest, factor_id: UUID) -> HttpResponse:
    ctx = registry_manager(request)
    if request.POST.get("accion") == "borrar":
        run(request, lambda: registry.delete_factor(ctx, factor_id), "Factor borrado.")
    else:
        form = FactorForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            run(request, lambda: registry.change_factor(ctx, factor_id, data["factor"],
                                                        data.get("note") or ""),
                "Factor corregido.")  # fmt: skip
    return redirect("labor_registry:factors")
