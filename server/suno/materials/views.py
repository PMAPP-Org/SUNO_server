"""Pestaña "Materiales" del proyecto (costos, pieza 6c, spec 3.2): inventario, kárdex y
bodegas, y las piezas que comparten las demás vistas (`views_*`).

Quien no lee los costos del proyecto recibe 404 (RD-11; `access.get_materials_project`). Quien
los lee pero no tiene la capacidad de una acción recibe 403. Las reglas viven en los servicios:
aquí solo se traducen a mensajes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.download import content_disposition
from suno.core.models import Project, Warehouse
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_export_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.projects.views import _ctx, _parse_date, _project_context, _render

from . import warehouses
from .access import get_material, get_materials_project, get_warehouse
from .common import MaterialRejected
from .export import inventory_workbook, kardex_workbook
from .forms import WarehouseForm
from .queries import (
    MAX_EXPORT_ROWS,
    InventoryFilters,
    categories,
    inventory_page,
    inventory_rows,
    kardex,
    tab_alerts,
)

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def tab_context(ctx: RequestContext, project: Project) -> dict[str, Any]:
    """Permisos de la pestaña y sus avisos (cuatro consultas)."""
    return {
        **_project_context(ctx, project),
        "can_create_material": can_create_costs(ctx, project),
        "can_decide_material": can_approve_costs(ctx, project),
        "can_void_material": can_reverse_costs(ctx, project),
        "can_export_material": can_export_costs(ctx, project),
        "material_alerts": tab_alerts(ctx, project),
    }


def page(
    request: HttpRequest, ctx: RequestContext, project: Project, template: str, **context: Any
) -> HttpResponse:
    return _render(request, ctx, template, {**tab_context(ctx, project), **context})


def download(url: str) -> HttpResponse:
    """Como el soporte del libro (6a): URL prefirmada; `local://` se arma a mano."""
    scheme = urlsplit(url).scheme
    if scheme == "local":
        response = HttpResponse(status=302)
        response["Location"] = url
        return response
    if scheme == "https" or (scheme == "http" and settings.DEBUG):
        return redirect(url)
    raise Http404


def require_export(ctx: RequestContext, project: Project) -> None:
    """Antes de armar un Excel o un PDF: sin permiso de exportar, 403 sin leer los datos."""
    if not can_export_costs(ctx, project):
        raise PermissionDenied


def xlsx(
    ctx: RequestContext,
    project: Project,
    content: bytes,
    stem: str,
    what: str,
    **metadata: Any,
) -> HttpResponse:
    """Un Excel auditado (`materials.exported`)."""
    if not can_export_costs(ctx, project):
        raise PermissionDenied
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.MATERIALS_EXPORTED,
            target=f"project:{project.pk}",
            metadata={"what": what, **metadata},
        )
    response = HttpResponse(content, content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(stem, ".xlsx", fallback=what)
    return response


def run(request: HttpRequest, action: Callable[[], Any], success: str) -> bool:
    """Ejecuta un servicio y deja el mensaje; `True` si salió bien."""
    try:
        action()
    except MaterialRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return False
    messages.success(request, success)
    return True


@login_required(login_url="platform_login")
@require_GET
def inventory(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    filters = InventoryFilters.from_query(request.GET)
    return page(
        request, ctx, project, "suno/materials/inventory.html",
        filters=filters,
        result=inventory_page(ctx, project, filters),
        warehouses=scoped_qs(ctx, Warehouse).filter(project=project).order_by("name"),
        categories=categories(ctx),
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_GET
def export_inventory(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    require_export(ctx, project)
    filters = InventoryFilters.from_query(request.GET)
    rows = inventory_rows(ctx, project, filters)
    return xlsx(
        ctx, project, inventory_workbook(rows), f"inventario-{project.code or project.name}",
        "inventario", rows=len(rows), filters=filters.query,
    )  # fmt: skip


def _kardex(
    request: HttpRequest,
    project_id: UUID,
    warehouse_id: UUID,
    material_id: UUID,
    limit: int,
    *,
    export: bool = False,
) -> tuple[RequestContext, Project, Any]:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    if export:
        require_export(ctx, project)
    sheet = kardex(
        ctx, project, get_warehouse(ctx, project, warehouse_id), get_material(ctx, material_id),
        start=_parse_date(request.GET.get("desde")), end=_parse_date(request.GET.get("hasta")),
        limit=limit,
    )  # fmt: skip
    return ctx, project, sheet


@login_required(login_url="platform_login")
@require_GET
def kardex_page(
    request: HttpRequest, project_id: UUID, warehouse_id: UUID, material_id: UUID
) -> HttpResponse:
    ctx, project, sheet = _kardex(request, project_id, warehouse_id, material_id, 1000)
    return page(request, ctx, project, "suno/materials/kardex.html", sheet=sheet,
                start=request.GET.get("desde", ""), end=request.GET.get("hasta", ""))  # fmt: skip


@login_required(login_url="platform_login")
@require_GET
def export_kardex(
    request: HttpRequest, project_id: UUID, warehouse_id: UUID, material_id: UUID
) -> HttpResponse:
    ctx, project, sheet = _kardex(
        request, project_id, warehouse_id, material_id, MAX_EXPORT_ROWS, export=True
    )
    return xlsx(
        ctx, project, kardex_workbook(sheet), f"kardex-{sheet.material.code}", "kardex",
        material=str(sheet.material.pk), warehouse=str(sheet.warehouse.pk),
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def warehouse_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    form = WarehouseForm(request.POST or None)
    if request.method == "POST":
        if not can_create_costs(ctx, project):
            raise PermissionDenied
        if form.is_valid() and run(
            request,
            lambda: warehouses.create_warehouse(ctx, project, form.cleaned_data["name"]),
            "Bodega creada.",
        ):
            return redirect("materials:warehouses", project.pk)
    rows = list(scoped_qs(ctx, Warehouse).filter(project=project).order_by("name"))
    return page(
        request, ctx, project, "suno/materials/warehouses.html", form=form,
        rows=[(row, can_update_cost(ctx, row)) for row in rows],
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def warehouse_update(request: HttpRequest, project_id: UUID, warehouse_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    get_warehouse(ctx, project, warehouse_id)
    action = request.POST.get("accion", "")
    name = request.POST.get("name", "")
    if action == "renombrar":
        run(
            request,
            lambda: warehouses.rename_warehouse(ctx, project, warehouse_id, name),
            "Bodega renombrada.",
        )
    elif action in ("desactivar", "activar"):
        active = action == "activar"
        run(
            request,
            lambda: warehouses.set_warehouse_active(ctx, project, warehouse_id, active),
            "Bodega activada." if active else "Bodega desactivada.",
        )
    return redirect("materials:warehouses", project.pk)
