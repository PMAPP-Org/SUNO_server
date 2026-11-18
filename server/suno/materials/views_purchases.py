"""Compras en la pestaña "Materiales" (costos, pieza 6c, spec 3.2 "Compras")."""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.core.models import MaterialPurchase, MaterialStatus, PurchaseKind, Warehouse
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

from . import purchases
from .access import get_document, get_materials_project
from .common import MaterialRejected, attachment_url
from .destination import destination_label, destination_options, task_label
from .forms import (
    NEW_ROWS,
    PurchaseForm,
    PurchaseLineFormSet,
    SupportForm,
    material_index,
    more_rows,
    outline_index,
    purchase_data,
    purchase_initial,
    read_purchase_lines,
)
from .queries import DocumentFilters, purchase_rows
from .views import download, page, run


def _detail(project_id: UUID, purchase_id: UUID) -> HttpResponse:
    return redirect("materials:purchase", project_id, purchase_id)


def back(request: HttpRequest, project_id: UUID, detail: HttpResponse) -> HttpResponse:
    """A la bandeja "Por aprobar" de Costos si de allá vino; si no, a la ficha."""
    if request.POST.get("volver") == "bandeja":
        return redirect("costs:inbox", project_id)
    return detail


@login_required(login_url="platform_login")
@require_GET
def purchase_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    filters = DocumentFilters.from_query(request.GET)
    rows, more = purchase_rows(ctx, project, filters)
    return page(
        request, ctx, project, "suno/materials/purchases.html", filters=filters, rows=rows,
        more=more, kinds=PurchaseKind.choices, statuses=MaterialStatus.choices,
    )  # fmt: skip


def _form(request: HttpRequest, project_id: UUID, purchase_id: UUID | None) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    purchase = get_document(ctx, MaterialPurchase, project, purchase_id) if purchase_id else None
    if purchase is None and not can_create_costs(ctx, project):
        raise PermissionDenied
    if purchase is not None and (
        purchase.status not in OPEN_STATES or not can_update_cost(ctx, purchase)
    ):
        raise PermissionDenied
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    current = purchases.purchase_lines(ctx, purchase) if purchase else []
    index = material_index(ctx, [line.material_id for line in current])
    warehouses = list(scoped_qs(ctx, Warehouse).filter(project=project, active=True))
    action = request.POST.get("accion", "guardar") if request.method == "POST" else ""
    errors: list[str] = []
    total = None
    if request.method == "POST":
        data = more_rows(request.POST, action) or request.POST
        form = PurchaseForm(data, request.FILES, warehouses=warehouses)
        lines = PurchaseLineFormSet(data, prefix="lineas")
        if action in ("guardar", "calcular") and form.is_valid() and lines.is_valid():
            direct = form.cleaned_data["kind"] == PurchaseKind.DIRECT
            rows, errors = read_purchase_lines(
                lines, index, catalog, outline_index(tasks), direct=direct
            )
            values = purchase_data(form, rows)
            if not errors and action == "calcular":
                errors, checked = purchases.purchase_errors(
                    ctx, project, values, catalog, tasks, today=timezone.localdate()
                )
                total = sum((line.amount for line in checked), start=0)
            elif not errors:
                upload = form.cleaned_data.get("support")
                try:
                    if purchase is None:
                        saved = purchases.create_purchase(
                            ctx, project, values, upload=upload,
                            name=upload.name if upload else "", catalog=catalog, tasks=tasks,
                        )  # fmt: skip
                    else:
                        saved = purchases.update_purchase(
                            ctx, project, purchase.pk, values, catalog=catalog, tasks=tasks
                        )
                except MaterialRejected as exc:
                    errors = exc.errors
                else:
                    return _detail(project.pk, saved.pk)
    elif purchase is None:
        form = PurchaseForm(
            initial={"kind": PurchaseKind.WAREHOUSE, "purchased_on": timezone.localdate(),
                     "document_type": "invoice"},
            warehouses=warehouses,
        )  # fmt: skip
        lines = PurchaseLineFormSet(prefix="lineas", initial=[{}] * NEW_ROWS)
    else:
        header, rows_initial = purchase_initial(purchase, current, catalog)
        form = PurchaseForm(initial=header, warehouses=warehouses)
        lines = PurchaseLineFormSet(prefix="lineas", initial=rows_initial)
    return page(
        request, ctx, project, "suno/materials/purchase_form.html", form=form, lines=lines,
        purchase=purchase, errors=errors, total=total, material_index=index,
        destination_options=destination_options(catalog), show_errors=action != "lineas",
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def purchase_new(request: HttpRequest, project_id: UUID) -> HttpResponse:
    return _form(request, project_id, None)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def purchase_edit(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    return _form(request, project_id, purchase_id)


@login_required(login_url="platform_login")
@require_GET
def purchase_detail(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    purchase = get_document(
        ctx, MaterialPurchase, project, purchase_id,
        "warehouse", "created_by", "updated_by", "decided_by", "voided_by",
    )  # fmt: skip
    catalog = load_catalog(ctx, project)
    lines = [
        (
            line,
            destination_label(
                catalog,
                line.scope,
                line.chapter_id,
                [link.item_id for link in line.item_links.all()],
                task_label(line.task),
            )
            if line.scope
            else "",
        )  # fmt: skip
        for line in purchases.purchase_lines(ctx, purchase)
    ]
    is_open = purchase.status in OPEN_STATES
    mine = ctx.user_id in (purchase.created_by_id, purchase.updated_by_id)
    url = reverse("materials:purchase", args=[project.pk, purchase.pk])
    return page(
        request, ctx, project, "suno/materials/purchase_detail.html",
        purchase=purchase, lines=lines,
        can_edit=is_open and can_update_cost(ctx, purchase)
        and (purchase.status == "draft" or purchase.created_by_id == ctx.user_id),
        can_delete=is_open and can_delete_cost(ctx, purchase),
        can_attach=is_open and can_update_cost(ctx, purchase),
        can_decide=purchase.status == "draft" and can_approve_costs(ctx, project) and not mine,
        can_void=purchase.status == "approved" and can_reverse_costs(ctx, project),
        needs="" if purchase.attachment_id else "Falta la factura",
        urls={"approve": url + "aprobar/", "reject": url + "rechazar/", "void": url + "anular/",
              "upload": url + "soporte/", "view": url + "soporte/ver/"},
        support_form=SupportForm(),
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def purchase_delete(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    if run(request, lambda: purchases.delete_purchase(ctx, project, purchase_id),
           "Borrador de la compra borrado."):  # fmt: skip
        return redirect("materials:purchases", project.pk)
    return _detail(project.pk, purchase_id)


@login_required(login_url="platform_login")
@require_POST
def purchase_support(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    form = SupportForm(request.POST, request.FILES)
    if form.is_valid():
        upload = form.cleaned_data["file"]
        run(request, lambda: purchases.attach_support(
            ctx, project, purchase_id, upload, upload.name or "factura.pdf"),
            "Factura adjunta; ya se puede aprobar.")  # fmt: skip
    return _detail(project.pk, purchase_id)


@login_required(login_url="platform_login")
@require_GET
def purchase_support_file(
    request: HttpRequest, project_id: UUID, purchase_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    return download(attachment_url(ctx, project, get_document(ctx, MaterialPurchase, project,
                                                              purchase_id)))  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def purchase_approve(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    run(request, lambda: purchases.approve_purchase(ctx, project, purchase_id),
        "Compra aprobada.")  # fmt: skip
    return back(request, project.pk, _detail(project.pk, purchase_id))


@login_required(login_url="platform_login")
@require_POST
def purchase_reject(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: purchases.reject_purchase(ctx, project, purchase_id, note),
        "Compra rechazada; su autor la verá con el motivo.")  # fmt: skip
    return back(request, project.pk, _detail(project.pk, purchase_id))


@login_required(login_url="platform_login")
@require_POST
def purchase_void(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: purchases.void_purchase(ctx, project, purchase_id, note),
        "Compra anulada.")  # fmt: skip
    return _detail(project.pk, purchase_id)
