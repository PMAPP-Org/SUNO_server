"""Páginas de la matriz de homologación (costos, pieza 4)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from visitacampo.budgets.excel import MAX_BYTES
from visitacampo.core.context import RequestContext
from visitacampo.core.download import content_disposition
from visitacampo.core.models import (
    MAX_LEVELS,
    Budget,
    Project,
    QuantityPlanImport,
    QuantityPlanImportStatus,
    ScheduleImport,
    ScheduleImportStatus,
)
from visitacampo.core.policies import can_manage_budgets
from visitacampo.core.scoping import scoped_qs
from visitacampo.projects.views import _ctx, _parse_uuid, _project_context, _render

from .access import get_matrix_project, require_manage
from .importer import PlanRejected, apply_plan, discard_plan, stage_plan, template_bytes
from .locations import LevelError, LevelSpec, from_project, load_tree, project_levels, save_levels
from .reports import (
    STATUS_LABELS,
    chapter_choices,
    filter_item_rows,
    item_rows,
    location_rows,
)


def _context(ctx: RequestContext, project: Project) -> dict[str, object]:
    return {
        **_project_context(ctx, project),
        "can_manage_matrix": can_manage_budgets(ctx, ctx.org_id),
    }


@login_required(login_url="platform_login")
def items(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    tree = load_tree(ctx, project)
    status = request.GET.get("estado") or None
    filters = {
        "presupuesto": str(_parse_uuid(request.GET.get("presupuesto")) or ""),
        "capitulo": str(_parse_uuid(request.GET.get("capitulo")) or ""),
        "ubicacion": str(_parse_uuid(request.GET.get("ubicacion")) or ""),
        "estado": status if status in STATUS_LABELS else "",
    }
    location_id = _parse_uuid(filters["ubicacion"] or None)
    rows = filter_item_rows(
        item_rows(ctx, project),
        budget_id=_parse_uuid(filters["presupuesto"] or None),
        chapter_id=_parse_uuid(filters["capitulo"] or None),
        location_id=location_id if location_id in tree.nodes else None,
        tree=tree,
        status=filters["estado"] or None,
    )
    return _render(
        request,
        ctx,
        "suno/matrix/items.html",
        {
            **_context(ctx, project),
            "rows": rows,
            "filters": filters,
            "status_labels": STATUS_LABELS,
            "budgets": scoped_qs(ctx, Budget).filter(project=project).order_by("name"),
            "chapters": chapter_choices(ctx, project),
            "nodes": sorted(
                ((pk, tree.label(pk)) for pk in tree.nodes), key=lambda n: n[1].lower()
            ),
        },
    )


@login_required(login_url="platform_login")
def locations(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    return _render(
        request,
        ctx,
        "suno/matrix/locations.html",
        {**_context(ctx, project), "rows": location_rows(ctx, project)},
    )


def _custom_fields(ctx: RequestContext, project: Project) -> list[dict[str, str]]:
    """Campos personalizados del último XML aplicado (para elegir la fuente de cada nivel)."""
    last = (
        scoped_qs(ctx, ScheduleImport)
        .filter(project=project, status=ScheduleImportStatus.APPLIED)
        .defer("source_xml")
        .order_by("-number")
        .first()
    )
    fields: list[dict[str, str]] = list(last.summary.get("custom_fields", [])) if last else []
    return fields


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def levels(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    require_manage(ctx)
    error = ""
    current = project_levels(ctx, project)
    rows: list[dict[str, Any]] = [
        {
            "depth": depth,
            "name": current[depth - 1].name if depth <= len(current) else "",
            "field": current[depth - 1].source_field if depth <= len(current) else "",
        }
        for depth in range(1, MAX_LEVELS + 1)
    ]
    if request.method == "POST":
        specs: list[LevelSpec] = []
        for depth in range(1, MAX_LEVELS + 1):
            name = request.POST.get(f"name_{depth}", "").strip()
            if not name:
                break
            specs.append(LevelSpec(name, request.POST.get(f"field_{depth}", "").strip()))
        try:
            save_levels(ctx, project, specs)
        except LevelError as exc:
            error = str(exc)
            rows = [
                {
                    "depth": depth,
                    "name": request.POST.get(f"name_{depth}", ""),
                    "field": request.POST.get(f"field_{depth}", ""),
                }
                for depth in range(1, MAX_LEVELS + 1)
            ]
        else:
            messages.success(request, "Niveles de ubicación guardados.")
            return redirect("matrix:levels", project.pk)
    fields = _custom_fields(ctx, project)
    known = {f["field_id"] for f in fields}
    # Un campo guardado que el último XML ya no trae se sigue ofreciendo (y seleccionado):
    # si no, guardar el formulario lo cambiaría a "Ninguno" sin que nadie lo pidiera.
    for row in rows:
        row["missing"] = bool(row["field"]) and row["field"] not in known
    return _render(
        request,
        ctx,
        "suno/matrix/levels.html",
        {
            **_context(ctx, project),
            "rows": rows,
            "fields": fields,
            "from_project": from_project(current),
            "error": error,
        },
    )


@login_required(login_url="platform_login")
def template_download(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    response = HttpResponse(
        template_bytes(ctx, project),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = content_disposition(
        f"matriz-{project.code or project.name}", ".xlsx", fallback="matriz"
    )
    return response


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_upload(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    require_manage(ctx)
    errors: list[str] = []
    levels = project_levels(ctx, project)
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            errors = ["Elige un archivo .xlsx."]
        elif upload.size is not None and upload.size > MAX_BYTES:
            errors = ["El archivo supera 20 MB."]
        else:
            try:
                staged = stage_plan(
                    ctx, project, file_name=upload.name or "matriz.xlsx", data=upload.read()
                )
            except PlanRejected as exc:
                errors = exc.errors
            else:
                return redirect("matrix:import_preview", project.pk, staged.pk)
    return _render(
        request,
        ctx,
        "suno/matrix/import.html",
        {
            **_context(ctx, project),
            "errors": errors,
            "levels": levels,
            "from_project": from_project(levels),
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_preview(request: HttpRequest, project_id: UUID, import_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    require_manage(ctx)
    imp = (
        scoped_qs(ctx, QuantityPlanImport)
        .defer("source_file")
        .filter(pk=import_id, project=project)
        .first()
    )
    if imp is None:
        raise Http404
    errors: list[str] = []
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "apply":
                applied = apply_plan(ctx, imp.pk)
                messages.success(request, f"Matriz aplicada (versión {applied.number}).")
                return redirect("matrix:items", project.pk)
            if action == "discard":
                discard_plan(ctx, imp.pk)
                messages.info(request, "Importación descartada.")
                return redirect("matrix:import", project.pk)
        except PlanRejected as exc:
            errors = exc.errors
    return _render(
        request,
        ctx,
        "suno/matrix/import_preview.html",
        {
            **_context(ctx, project),
            "imp": imp,
            "s": imp.summary,
            "pending": imp.status == QuantityPlanImportStatus.PENDING,
            "status_labels": STATUS_LABELS,
            "errors": errors,
        },
    )


@login_required(login_url="platform_login")
def history(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    imports = (
        scoped_qs(ctx, QuantityPlanImport)
        .filter(project=project)
        .defer("source_file")
        .select_related("uploaded_by")
        .order_by("-uploaded_at")
    )
    return _render(
        request,
        ctx,
        "suno/matrix/history.html",
        {**_context(ctx, project), "imports": imports},
    )
