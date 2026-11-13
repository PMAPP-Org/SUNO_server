"""Pestaña "Costos" del proyecto (costos, pieza 6a): el libro de costos.

Quien no lee los costos del proyecto recibe 404 (RD-11; `access.get_costs_project`). Quien los
lee pero no tiene la capacidad de una acción recibe 403. Las reglas de estado y de
autoaprobación viven en `services`: aquí solo se traducen a mensajes.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.budgets.excel import MAX_BYTES, ExcelRejected, check_file_name
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.download import content_disposition
from suno.core.models import CostEntry, CostKind, CostScope, CostStatus, Project
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_export_costs,
    can_import_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.projects.views import _ctx, _parse_uuid, _project_context, _render

from . import importer, services
from .access import get_costs_project, get_entry
from .catalog import assignable_tasks, load_catalog
from .export import ledger_workbook
from .forms import EntryForm, initial_from, read_step
from .ledger import (
    MAX_EXPORT_LINES,
    ORIGINS,
    LedgerFilters,
    LedgerTooLarge,
    assignment_text,
    export_lines,
    ledger_page,
)
from .services import APPROVED_IS_FINAL, CostRejected

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _context(ctx: RequestContext, project: Project) -> dict[str, Any]:
    """Permisos de la pestaña y los contadores de "Por aprobar" y "Mis rechazados"."""
    can_decide = can_approve_costs(ctx, project)
    entries = scoped_qs(ctx, CostEntry).filter(project=project)
    pending = (
        entries.filter(status=CostStatus.DRAFT)
        .exclude(created_by_id=ctx.user_id)
        .exclude(updated_by_id=ctx.user_id)
        .count()
        if can_decide
        else 0
    )
    return {
        **_project_context(ctx, project),
        "can_create_cost": can_create_costs(ctx, project),
        "can_import_cost": can_import_costs(ctx, project),
        "can_export_cost": can_export_costs(ctx, project),
        "can_decide_cost": can_decide,
        "pending_count": pending,
        "rejected_count": entries.filter(
            status=CostStatus.REJECTED, created_by_id=ctx.user_id
        ).count(),
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
    reversal = scoped_qs(ctx, CostEntry).filter(reverses=entry).first()
    return _render(
        request,
        ctx,
        "suno/costs/detail.html",
        {
            **_context(ctx, project),
            "entry": entry,
            "assignment": assignment_text(entry, catalog),
            "reversal": reversal,
            "can_decide": entry.status == CostStatus.DRAFT
            and can_approve_costs(ctx, project)
            and ctx.user_id not in (entry.created_by_id, entry.updated_by_id),
            "can_reverse": entry.status == CostStatus.APPROVED
            and entry.reverses_id is None
            and reversal is None
            and can_reverse_costs(ctx, project),
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
    scheme = urlsplit(url).scheme
    if scheme == "local":
        # El adaptador local (desarrollo y pruebas): `HttpResponseRedirect` no acepta su
        # esquema, así que la respuesta se arma a mano. La URL la firma el almacén.
        response = HttpResponse(status=302)
        response["Location"] = url
        return response
    if scheme == "https" or (scheme == "http" and settings.DEBUG):
        return redirect(url)
    raise Http404


def _back(request: HttpRequest, project: Project, entry_id: UUID) -> HttpResponse:
    if request.POST.get("volver") == "bandeja":
        return redirect("costs:inbox", project.pk)
    return redirect("costs:detail", project.pk, entry_id)


@login_required(login_url="platform_login")
@require_GET
def inbox(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """Borradores de otras personas (ni registrados ni modificados por quien mira)."""
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    if not can_approve_costs(ctx, project):
        raise PermissionDenied
    catalog = load_catalog(ctx, project)
    entries = (
        scoped_qs(ctx, CostEntry)
        .filter(project=project, status=CostStatus.DRAFT)
        .exclude(created_by_id=ctx.user_id)
        .exclude(updated_by_id=ctx.user_id)
        .select_related("created_by", "chapter", "task")
        .prefetch_related("item_links__item")
        .order_by("incurred_on", "created_at")
    )
    return _render(
        request,
        ctx,
        "suno/costs/inbox.html",
        {
            **_context(ctx, project),
            "rows": [(entry, assignment_text(entry, catalog)) for entry in entries],
        },
    )


@login_required(login_url="platform_login")
@require_GET
def rejected(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """Los rechazados de quien mira, con su motivo, para corregirlos y reenviarlos."""
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    catalog = load_catalog(ctx, project)
    entries = (
        scoped_qs(ctx, CostEntry)
        .filter(project=project, status=CostStatus.REJECTED, created_by_id=ctx.user_id)
        .select_related("decided_by", "chapter", "task")
        .prefetch_related("item_links__item")
        .order_by("-decided_at")
    )
    return _render(
        request,
        ctx,
        "suno/costs/rejected.html",
        {
            **_context(ctx, project),
            "rows": [(entry, assignment_text(entry, catalog)) for entry in entries],
        },
    )


@login_required(login_url="platform_login")
@require_POST
def approve(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    try:
        services.approve(ctx, project, entry_id)
    except CostRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Costo aprobado.")
    return _back(request, project, entry_id)


@login_required(login_url="platform_login")
@require_POST
def reject(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    try:
        services.reject(ctx, project, entry_id, request.POST.get("motivo", ""))
    except CostRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Costo rechazado; su autor lo verá con el motivo.")
    return _back(request, project, entry_id)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def reverse_entry(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    entry = get_entry(ctx, project, entry_id)
    if not can_reverse_costs(ctx, project):
        raise PermissionDenied
    errors: list[str] = []
    if request.method == "POST":
        try:
            reversal = services.reverse(ctx, project, entry.pk, request.POST.get("motivo", ""))
        except CostRejected as exc:
            errors = exc.errors
        else:
            messages.success(request, "Costo reversado.")
            if request.POST.get("reabrir"):
                query = urlencode(
                    {
                        "copiar": entry.pk,
                        "alcance": entry.scope,
                        "capitulo": entry.chapter_id or "",
                    }
                )
                return redirect(f"{reverse('costs:new', args=[project.pk])}?{query}")
            return redirect("costs:detail", project.pk, reversal.pk)
    return _render(
        request,
        ctx,
        "suno/costs/reverse.html",
        {**_context(ctx, project), "entry": entry, "errors": errors},
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_upload(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    if not can_import_costs(ctx, project):
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
            except ExcelRejected as exc:
                errors = [str(exc)]
            else:
                result = importer.preview(ctx, project, upload.read())
    return _render(
        request,
        ctx,
        "suno/costs/import.html",
        {**_context(ctx, project), "errors": errors, "result": result},
    )


@login_required(login_url="platform_login")
@require_POST
def import_apply(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    try:
        created = importer.apply(ctx, project, request.POST.get("token", ""))
    except CostRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return redirect("costs:import", project.pk)
    plural = "s" if created != 1 else ""
    messages.success(
        request,
        f"{created} costo{plural} importado{plural} en borrador. Adjunta el soporte de cada uno.",
    )
    return redirect(f"{reverse('costs:list', args=[project.pk])}?estado=draft")


@login_required(login_url="platform_login")
@require_GET
def template_download(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    if not can_import_costs(ctx, project):
        raise PermissionDenied
    response = HttpResponse(importer.template_bytes(ctx, project), content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        f"plantilla-costos-{project.code or project.name}", ".xlsx", fallback="plantilla-costos"
    )
    return response


@login_required(login_url="platform_login")
@require_GET
def export(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """El libro filtrado, en Excel (auditado)."""
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    if not can_export_costs(ctx, project):
        raise PermissionDenied
    filters = LedgerFilters.from_query(request.GET)
    try:
        lines = export_lines(ctx, project, filters, catalog=load_catalog(ctx, project))
    except LedgerTooLarge:
        messages.error(
            request,
            f"El libro filtrado pasa de {MAX_EXPORT_LINES} líneas: filtra por fechas.",
        )
        return redirect(f"{reverse('costs:list', args=[project.pk])}?{filters.query}")
    content = ledger_workbook(lines)
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.COST_EXPORTED,
            target=f"project:{project.pk}",
            metadata={"lines": len(lines), "filters": filters.query},
        )
    response = HttpResponse(content, content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        f"costos-{project.code or project.name}", ".xlsx", fallback="costos"
    )
    return response
