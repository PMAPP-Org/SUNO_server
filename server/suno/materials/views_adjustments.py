"""Ajustes de inventario en la pestaña "Materiales" (costos, pieza 6c, spec 3.2 "Ajustes").

El formulario de conteo, sin JavaScript: "Traer el inventario de la bodega" llena las filas con
los materiales que tienen saldo; "Recalcular" muestra lo esperado y la diferencia sin guardar."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import prefetch_related_objects
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.core.models import InventoryAdjustment, StockBalance, Warehouse
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.costs.catalog import assignable_tasks, load_catalog
from suno.projects.views import _ctx

from . import adjustments
from .access import get_document, get_materials_project
from .common import MaterialRejected, attachment_url
from .destination import destination_label, destination_options, task_label
from .forms import (
    NEW_ROWS,
    AdjustmentForm,
    AdjustmentLineFormSet,
    SupportForm,
    adjustment_initial,
    material_index,
    more_rows,
    plain,
    read_adjustment_lines,
)
from .queries import _uuid, adjustment_rows
from .views import download, page, run
from .views_purchases import back


def _bring(data: QueryDict, ctx: Any, project: Any) -> QueryDict:
    """El POST con una fila por material con saldo en la bodega elegida."""
    copy = data.copy()
    # Un id que no es UUID no trae filas: el formulario lo marca como bodega no válida.
    warehouse_id = _uuid(data.get("warehouse"))
    balances = (
        scoped_qs(ctx, StockBalance)
        .filter(project=project, warehouse_id=warehouse_id, quantity__gt=0)
        .select_related("material")
        .order_by("material__code")
    )
    rows = list(balances)
    copy["lineas-TOTAL_FORMS"] = str(len(rows) or 1)
    for n, balance in enumerate(rows):
        copy[f"lineas-{n}-material"] = balance.material.label
        copy[f"lineas-{n}-counted"] = plain(balance.quantity)
        copy[f"lineas-{n}-unit_cost"] = ""
    return copy


@login_required(login_url="platform_login")
@require_GET
def adjustment_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    rows, more = adjustment_rows(ctx, project)
    return page(request, ctx, project, "suno/materials/adjustments.html", rows=rows, more=more)


def _form(request: HttpRequest, project_id: UUID, adjustment_id: UUID | None) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    adjustment = (
        get_document(ctx, InventoryAdjustment, project, adjustment_id) if adjustment_id else None
    )
    if adjustment is None and not can_create_costs(ctx, project):
        raise PermissionDenied
    if adjustment is not None and (
        adjustment.status not in OPEN_STATES or not can_update_cost(ctx, adjustment)
    ):
        raise PermissionDenied
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    current = adjustments.adjustment_lines(ctx, adjustment) if adjustment else []
    index = material_index(ctx, [line.material_id for line in current])
    warehouses = list(scoped_qs(ctx, Warehouse).filter(project=project, active=True))
    action = request.POST.get("accion", "guardar") if request.method == "POST" else ""
    errors: list[str] = []
    counted: list[adjustments.CountLine] = []
    if request.method == "POST":
        data = request.POST
        if action == "traer":
            data = _bring(data, ctx, project)
        data = more_rows(data, action) or data
        form = AdjustmentForm(data, warehouses=warehouses, tasks=tasks)
        lines = AdjustmentLineFormSet(data, prefix="lineas")
        if action in ("guardar", "calcular") and form.is_valid() and lines.is_valid():
            rows, errors = read_adjustment_lines(lines, index)
            values, error = form.adjustment_data(rows, catalog)
            if error:
                errors.append(error)
            if not errors and values is not None and action == "calcular":
                warehouse = next((w for w in warehouses if w.pk == values.warehouse_id), None)
                if warehouse is None:
                    errors.append("Elige una bodega activa del proyecto.")
                else:
                    errors, counted = adjustments.count_lines(ctx, warehouse, values.lines)
            elif not errors and values is not None:
                try:
                    if adjustment is None:
                        saved = adjustments.create_adjustment(
                            ctx, project, values, catalog=catalog, tasks=tasks
                        )
                    else:
                        saved = adjustments.update_adjustment(
                            ctx, project, adjustment.pk, values, catalog=catalog, tasks=tasks
                        )
                except MaterialRejected as exc:
                    errors = exc.errors
                else:
                    return redirect("materials:adjustment", project.pk, saved.pk)
    elif adjustment is None:
        form = AdjustmentForm(initial={"counted_on": timezone.localdate()},
                              warehouses=warehouses, tasks=tasks)  # fmt: skip
        lines = AdjustmentLineFormSet(prefix="lineas", initial=[{}] * NEW_ROWS)
    else:
        prefetch_related_objects([adjustment], "item_links")
        header, rows_initial = adjustment_initial(adjustment, current, catalog)
        form = AdjustmentForm(initial=header, warehouses=warehouses, tasks=tasks)
        lines = AdjustmentLineFormSet(prefix="lineas", initial=rows_initial)
    return page(
        request, ctx, project, "suno/materials/adjustment_form.html", form=form, lines=lines,
        adjustment=adjustment, errors=errors, counted=counted, material_index=index,
        destination_options=destination_options(catalog),
        show_errors=action in ("guardar", "calcular", "traer"),
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def adjustment_new(request: HttpRequest, project_id: UUID) -> HttpResponse:
    return _form(request, project_id, None)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def adjustment_edit(request: HttpRequest, project_id: UUID, adjustment_id: UUID) -> HttpResponse:
    return _form(request, project_id, adjustment_id)


@login_required(login_url="platform_login")
@require_GET
def adjustment_detail(request: HttpRequest, project_id: UUID, adjustment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    adjustment = get_document(
        ctx, InventoryAdjustment, project, adjustment_id,
        "warehouse", "task", "created_by", "updated_by", "decided_by", "voided_by",
    )  # fmt: skip
    prefetch_related_objects([adjustment], "item_links")
    catalog = load_catalog(ctx, project)
    is_open = adjustment.status in OPEN_STATES
    mine = ctx.user_id in (adjustment.created_by_id, adjustment.updated_by_id)
    url = reverse("materials:adjustment", args=[project.pk, adjustment.pk])
    return page(
        request, ctx, project, "suno/materials/adjustment_detail.html",
        adjustment=adjustment, lines=adjustments.adjustment_lines(ctx, adjustment),
        label=destination_label(
            catalog, adjustment.scope, adjustment.chapter_id,
            [link.item_id for link in adjustment.item_links.all()], task_label(adjustment.task),
        ),
        can_edit=is_open and can_update_cost(ctx, adjustment)
        and (adjustment.status == "draft" or adjustment.created_by_id == ctx.user_id),
        can_delete=is_open and can_delete_cost(ctx, adjustment),
        can_attach=adjustment.status == "draft" and can_update_cost(ctx, adjustment),
        can_decide=adjustment.status == "draft" and can_approve_costs(ctx, project) and not mine,
        can_void=adjustment.status == "approved" and can_reverse_costs(ctx, project),
        needs="" if adjustment.attachment_id else "Falta el acta de conteo firmada",
        urls={"approve": url + "aprobar/", "reject": url + "rechazar/", "void": url + "anular/",
              "upload": url + "soporte/", "view": url + "soporte/ver/"},
    )  # fmt: skip


def _detail(project_id: UUID, adjustment_id: UUID) -> HttpResponse:
    return redirect("materials:adjustment", project_id, adjustment_id)


@login_required(login_url="platform_login")
@require_POST
def adjustment_delete(request: HttpRequest, project_id: UUID, adjustment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    if run(request, lambda: adjustments.delete_adjustment(ctx, project, adjustment_id),
           "Borrador del ajuste borrado."):  # fmt: skip
        return redirect("materials:adjustments", project.pk)
    return _detail(project.pk, adjustment_id)


@login_required(login_url="platform_login")
@require_POST
def adjustment_support(request: HttpRequest, project_id: UUID, adjustment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    form = SupportForm(request.POST, request.FILES)
    if form.is_valid():
        upload = form.cleaned_data["file"]
        name = upload.name or "acta-de-conteo.pdf"
        run(request, lambda: adjustments.attach_count(ctx, project, adjustment_id, upload, name),
            "Acta de conteo adjunta; ya se puede aprobar.")  # fmt: skip
    return _detail(project.pk, adjustment_id)


@login_required(login_url="platform_login")
@require_GET
def adjustment_support_file(
    request: HttpRequest, project_id: UUID, adjustment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    adjustment = get_document(ctx, InventoryAdjustment, project, adjustment_id)
    return download(attachment_url(ctx, project, adjustment))


@login_required(login_url="platform_login")
@require_POST
def adjustment_approve(request: HttpRequest, project_id: UUID, adjustment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    run(request, lambda: adjustments.approve_adjustment(ctx, project, adjustment_id),
        "Ajuste aprobado.")  # fmt: skip
    return back(request, project.pk, _detail(project.pk, adjustment_id))


@login_required(login_url="platform_login")
@require_POST
def adjustment_reject(request: HttpRequest, project_id: UUID, adjustment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: adjustments.reject_adjustment(ctx, project, adjustment_id, note),
        "Ajuste rechazado; su autor lo verá con el motivo.")  # fmt: skip
    return back(request, project.pk, _detail(project.pk, adjustment_id))


@login_required(login_url="platform_login")
@require_POST
def adjustment_void(request: HttpRequest, project_id: UUID, adjustment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: adjustments.void_adjustment(ctx, project, adjustment_id, note),
        "Ajuste anulado: el inventario y el libro quedaron como antes del ajuste.")  # fmt: skip
    return _detail(project.pk, adjustment_id)
