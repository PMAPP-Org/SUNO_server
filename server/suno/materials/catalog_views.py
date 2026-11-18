"""Catálogo de materiales de la organización en la web (costos, pieza 6c, spec 3.1).

Lo ve cualquier rol de Costos (`can_read_material_catalog`; si no, 404). Crear, editar e
importar piden `can_manage_material_catalog` (si no, 403)."""

from __future__ import annotations

from typing import cast
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.budgets.excel import MAX_BYTES, ExcelRejected, check_file_name
from suno.core.context import Principal, RequestContext, resolve_request_context
from suno.core.download import content_disposition
from suno.core.models import AppUser, Material
from suno.core.policies import can_manage_material_catalog, can_read_material_catalog
from suno.core.scoping import scoped_qs
from suno.projects.views import _render

from . import catalog_import
from .catalog_services import create_material, update_material
from .common import MaterialRejected
from .forms import CatalogMaterialForm
from .queries import categories

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
STATES = {"activos": "Activos", "inactivos": "Inactivos", "todos": "Todos"}


def _ctx(request: HttpRequest) -> RequestContext:
    user = cast(AppUser, request.user)
    ctx = resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))
    if not can_read_material_catalog(ctx, ctx.org_id):
        raise Http404
    return ctx


def _manager(request: HttpRequest) -> RequestContext:
    ctx = _ctx(request)
    if not can_manage_material_catalog(ctx, ctx.org_id):
        raise PermissionDenied
    return ctx


def _page(
    request: HttpRequest, ctx: RequestContext, template: str, **context: object
) -> HttpResponse:
    return _render(
        request,
        ctx,
        template,
        {
            "sidebar_active": "materials",
            "can_manage_catalog": can_manage_material_catalog(ctx, ctx.org_id),
            **context,
        },
    )


@login_required(login_url="platform_login")
@require_GET
def catalog_list(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    text = (request.GET.get("texto") or "").strip()[:100]
    category = (request.GET.get("categoria") or "").strip()[:80]
    state = request.GET.get("estado") or "activos"
    state = state if state in STATES else "activos"
    rows = scoped_qs(ctx, Material).order_by("code")
    if text:
        rows = rows.filter(Q(code__icontains=text) | Q(name__icontains=text))
    if category:
        rows = rows.filter(category__iexact=category)
    if state != "todos":
        rows = rows.filter(active=state == "activos")
    page = Paginator(rows, 100).get_page(request.GET.get("pagina"))
    query = urlencode({k: v for k, v in (("texto", text), ("categoria", category),
                                         ("estado", state)) if v})  # fmt: skip
    return _page(
        request, ctx, "suno/materials/catalog.html", page=page, text=text, category=category,
        state=state, states=STATES, categories=categories(ctx), query=query,
    )  # fmt: skip


def _form(request: HttpRequest, material: Material | None) -> HttpResponse:
    ctx = _manager(request)
    errors: list[str] = []
    if request.method == "POST":
        form = CatalogMaterialForm(request.POST)
        if form.is_valid():
            try:
                if material is None:
                    create_material(ctx, form.material_data())
                else:
                    update_material(ctx, material.pk, form.material_data())
            except MaterialRejected as exc:
                errors = exc.errors
            else:
                messages.success(request, "Material guardado.")
                return redirect("materials_catalog:list")
    elif material is None:
        form = CatalogMaterialForm(initial={"active": True})
    else:
        form = CatalogMaterialForm(
            initial={
                "code": material.code,
                "name": material.name,
                "unit": material.unit,
                "category": material.category,
                "active": material.active,
            }
        )
    return _page(
        request, ctx, "suno/materials/catalog_form.html", form=form, material=material,
        errors=errors,
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def catalog_new(request: HttpRequest) -> HttpResponse:
    return _form(request, None)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def catalog_edit(request: HttpRequest, material_id: UUID) -> HttpResponse:
    material = scoped_qs(_ctx(request), Material).filter(pk=material_id).first()
    if material is None:
        raise Http404
    return _form(request, material)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def catalog_import_upload(request: HttpRequest) -> HttpResponse:
    ctx = _manager(request)
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
                result = catalog_import.preview(ctx, upload.read())
    actions = (catalog_import.NEW, catalog_import.UPDATE, catalog_import.SAME)
    counts = [(action, result.count(action)) for action in actions] if result else []
    return _page(
        request, ctx, "suno/materials/catalog_import.html", errors=errors, result=result,
        counts=counts,
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def catalog_import_apply(request: HttpRequest) -> HttpResponse:
    ctx = _manager(request)
    try:
        created, updated = catalog_import.apply(ctx, request.POST.get("token", ""))
    except MaterialRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return redirect("materials_catalog:import")
    messages.success(request, f"Catálogo importado: {created} nuevos y {updated} actualizados.")
    return redirect("materials_catalog:list")


@login_required(login_url="platform_login")
@require_GET
def catalog_template(request: HttpRequest) -> HttpResponse:
    _manager(request)
    response = HttpResponse(catalog_import.template_bytes(), content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        "plantilla-materiales", ".xlsx", fallback="plantilla-materiales"
    )
    return response
