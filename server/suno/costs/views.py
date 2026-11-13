"""Pestaña "Costos" del proyecto (costos, pieza 6a): el libro de costos.

Quien no lee los costos del proyecto recibe 404 (RD-11; `access.get_costs_project`). Quien los
lee pero no tiene la capacidad de una acción recibe 403. Las reglas de estado y de
autoaprobación viven en `services`: aquí solo se traducen a mensajes.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.core.context import RequestContext
from suno.core.models import CostEntry, CostKind, CostScope, CostStatus, Project
from suno.core.policies import (
    can_create_costs,
    can_delete_cost,
    can_export_costs,
    can_import_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.projects.views import _ctx, _parse_uuid, _project_context, _render

from . import services
from .access import get_costs_project, get_entry
from .catalog import assignable_tasks, load_catalog
from .forms import EntryForm, initial_from, read_step
from .ledger import ORIGINS, LedgerFilters, assignment_text, ledger_page
from .services import APPROVED_IS_FINAL, CostRejected


def _context(ctx: RequestContext, project: Project) -> dict[str, Any]:
    return {
        **_project_context(ctx, project),
        "can_create_cost": can_create_costs(ctx, project),
        "can_import_cost": can_import_costs(ctx, project),
        "can_export_cost": can_export_costs(ctx, project),
    }


def _page(value: str | None) -> int:
    try:
        return int(value or 1)
    except ValueError:
        return 1


def _editable(ctx: RequestContext, entry: CostEntry, delete: bool = False) -> bool:
    """Lo mismo que exige el servicio, para mostrar u ocultar los botones."""
    if entry.status == CostStatus.APPROVED:
        return False
    if entry.status == CostStatus.REJECTED and entry.created_by_id != ctx.user_id:
        return False
    return can_delete_cost(ctx, entry) if delete else can_update_cost(ctx, entry)


@login_required(login_url="platform_login")
@require_GET
def ledger_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    catalog = load_catalog(ctx, project)
    filters = LedgerFilters.from_query(request.GET)
    page = ledger_page(
        ctx, project, filters, page=_page(request.GET.get("pagina")), catalog=catalog
    )
    return _render(
        request,
        ctx,
        "suno/costs/list.html",
        {
            **_context(ctx, project),
            "filters": filters,
            "page": page,
            "catalog": catalog,
            "statuses": CostStatus.choices,
            "kinds": CostKind.choices,
            "origins": ORIGINS,
        },
    )


def _entry_form(
    request: HttpRequest,
    ctx: RequestContext,
    project: Project,
    entry: CostEntry | None,
) -> HttpResponse:
    """Formulario nuevo (con `?copiar=` opcional) o de edición."""
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    source = entry
    copy_id = _parse_uuid(request.GET.get("copiar")) if entry is None else None
    if copy_id is not None:
        source = get_entry(ctx, project, copy_id)
    step = read_step(request.POST if request.method == "POST" else request.GET, source)
    initial: dict[str, Any] = (
        initial_from(source, catalog, copy=entry is None)
        if source is not None
        else {"incurred_on": timezone.localdate()}
    )
    form = EntryForm(
        request.POST or None,
        request.FILES or None,
        catalog=catalog,
        tasks=tasks,
        step=step,
        initial=initial,
    )
    errors: list[str] = []
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data.get("support")
        name = upload.name if upload is not None else ""
        try:
            if entry is None:
                saved = services.create_draft(
                    ctx, project, form.entry_data(), upload=upload, name=name,
                    catalog=catalog, tasks=tasks,
                )  # fmt: skip
            else:
                saved = services.update_draft(
                    ctx, project, entry.pk, form.entry_data(), upload=upload, name=name,
                    catalog=catalog, tasks=tasks,
                )  # fmt: skip
        except CostRejected as exc:
            errors = exc.errors
        else:
            messages.success(request, "Costo guardado en borrador.")
            return redirect("costs:detail", project.pk, saved.pk)
    return _render(
        request,
        ctx,
        "suno/costs/form.html",
        {
            **_context(ctx, project),
            "form": form,
            "step": step,
            "scopes": CostScope.choices,
            "catalog": catalog,
            "errors": errors,
            "entry": entry,
            "copy_id": copy_id,
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def new(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    return _entry_form(request, ctx, project, None)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def edit(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    entry = get_entry(ctx, project, entry_id)
    if entry.status == CostStatus.APPROVED:
        messages.error(request, APPROVED_IS_FINAL)
        return redirect("costs:detail", project.pk, entry.pk)
    if not _editable(ctx, entry):
        raise PermissionDenied
    return _entry_form(request, ctx, project, entry)


@login_required(login_url="platform_login")
@require_GET
def detail(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    entry = get_entry(ctx, project, entry_id)
    catalog = load_catalog(ctx, project)
    return _render(
        request,
        ctx,
        "suno/costs/detail.html",
        {
            **_context(ctx, project),
            "entry": entry,
            "assignment": assignment_text(entry, catalog),
            "reversal": scoped_qs(ctx, CostEntry).filter(reverses=entry).first(),
            "can_edit": _editable(ctx, entry),
            "can_delete": _editable(ctx, entry, delete=True),
        },
    )


@login_required(login_url="platform_login")
@require_POST
def delete(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    try:
        services.delete_draft(ctx, project, entry_id)
    except CostRejected as exc:
        messages.error(request, exc.errors[0])
        return redirect("costs:detail", project.pk, entry_id)
    messages.success(request, "Borrador borrado.")
    return redirect("costs:list", project.pk)


@login_required(login_url="platform_login")
@require_GET
def support(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    url = services.support_url(ctx, project, get_entry(ctx, project, entry_id))
    # A mano y no con `redirect`: `HttpResponseRedirect` rechaza el esquema `local://` del
    # adaptador local (desarrollo y pruebas). La URL la firma el almacén, no el usuario.
    response = HttpResponse(status=302)
    response["Location"] = url
    return response
