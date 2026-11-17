"""Salidas, devoluciones y traslados en la pestaña "Materiales" (costos, pieza 6c, spec 3.2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import prefetch_related_objects
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.core.models import (
    CostScope,
    MaterialIssue,
    MaterialIssueLine,
    MaterialReturn,
    MaterialTransfer,
    MaterialTransferLine,
    Warehouse,
)
from suno.core.policies import can_create_costs, can_delete_cost, can_update_cost
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.projects.views import _ctx

from . import movements
from .access import get_document, get_materials_project
from .common import MaterialRejected
from .destination import destination_label, destination_options, task_label
from .export import issues_workbook
from .forms import (
    NEW_ROWS,
    IssueForm,
    MaterialLineFormSet,
    ReturnForm,
    TransferForm,
    issue_initial,
    material_index,
    material_rows,
    more_rows,
    read_material_lines,
)
from .queries import MAX_EXPORT_ROWS, DocumentFilters, issue_query, issue_rows, transfer_rows
from .views import page, require_export, run, xlsx


def issue_label(issue: MaterialIssue, catalog: Catalog) -> str:
    """Con `select_related("task")` y `prefetch_related("item_links")`."""
    return destination_label(
        catalog, issue.scope, issue.chapter_id, [link.item_id for link in issue.item_links.all()],
        task_label(issue.task),
    )  # fmt: skip


def _warehouses(ctx: Any, project: Any) -> list[Warehouse]:
    return list(scoped_qs(ctx, Warehouse).filter(project=project, active=True).order_by("name"))


def _issue_form(
    request: HttpRequest, ctx: Any, project: Any, issue: MaterialIssue | None
) -> tuple[HttpResponse | None, dict[str, Any]]:
    """El formulario de salida (nueva o editada). Devuelve la redirección si se guardó, o el
    contexto para pintarlo."""
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    current = (
        list(scoped_qs(ctx, MaterialIssueLine).filter(issue=issue).select_related("material"))
        if issue
        else []
    )
    index = material_index(ctx, [line.material_id for line in current])
    warehouses = _warehouses(ctx, project)
    action = request.POST.get("accion", "guardar") if request.method == "POST" else ""
    errors: list[str] = []
    if request.method == "POST":
        data = more_rows(request.POST, action) or request.POST
        form = IssueForm(data, warehouses=warehouses, tasks=tasks)
        lines = MaterialLineFormSet(data, prefix="lineas")
        if action == "guardar" and form.is_valid() and lines.is_valid():
            rows, errors = read_material_lines(lines, index)
            values, error = form.issue_data(rows, catalog)
            if error:
                errors.append(error)
            if not errors and values is not None:
                try:
                    if issue is None:
                        movements.create_issue(ctx, project, values, catalog=catalog, tasks=tasks)
                        return redirect("materials:issues", project.pk), {}
                    movements.update_issue(
                        ctx, project, issue.pk, values, catalog=catalog, tasks=tasks
                    )
                    return redirect("materials:issue", project.pk, issue.pk), {}
                except MaterialRejected as exc:
                    errors = exc.errors
    elif issue is None:
        form = IssueForm(initial={"issued_on": timezone.localdate()}, warehouses=warehouses,
                         tasks=tasks)  # fmt: skip
        lines = MaterialLineFormSet(prefix="lineas", initial=[{}] * NEW_ROWS)
    else:
        form = IssueForm(initial=issue_initial(issue, catalog), warehouses=warehouses, tasks=tasks)
        lines = MaterialLineFormSet(prefix="lineas", initial=material_rows(current))
    return None, {
        "form": form,
        "lines": lines,
        "errors": errors,
        "show_errors": action == "guardar",
        "material_index": index,
        "destination_options": destination_options(catalog),
        "catalog": catalog,
    }


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def issue_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """La lista filtrable y, para quien registra, el formulario rápido de salida."""
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    can_create = can_create_costs(ctx, project)
    if request.method == "POST" and not can_create:
        raise PermissionDenied
    done, form_context = _issue_form(request, ctx, project, None) if can_create else (None, {})
    if done is not None:
        messages.success(request, "Salida registrada.")
        return done
    filters = DocumentFilters.from_query(request.GET)
    rows, more = issue_rows(ctx, project, filters)
    catalog = form_context.get("catalog") or load_catalog(ctx, project)
    labelled = [(row, issue_label(row, catalog)) for row in _with_items(rows)]
    return page(
        request, ctx, project, "suno/materials/issues.html", filters=filters, rows=labelled,
        more=more, scopes=CostScope.choices,
        all_warehouses=scoped_qs(ctx, Warehouse).filter(project=project).order_by("name"),
        **form_context,
    )  # fmt: skip


def _with_items(rows: list[MaterialIssue]) -> list[MaterialIssue]:
    """Los ítems de los destinos de una lista ya leída (una consulta)."""
    prefetch_related_objects(rows, "item_links")
    return rows


def _changeable(ctx: Any, issue: MaterialIssue, policy: Any) -> bool:
    return (
        issue.cut_id is None
        and not scoped_qs(ctx, MaterialReturn).filter(issue=issue).exists()
        and policy(ctx, issue)
    )


@login_required(login_url="platform_login")
@require_GET
def issue_detail(request: HttpRequest, project_id: UUID, issue_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    issue = get_document(ctx, MaterialIssue, project, issue_id, "warehouse", "task", "cut",
                         "created_by", "updated_by")  # fmt: skip
    catalog = load_catalog(ctx, project)
    _with_items([issue])
    lines = list(
        scoped_qs(ctx, MaterialIssueLine).filter(issue=issue).select_related("material")
        .order_by("order")
    )  # fmt: skip
    returned = movements.returned_by_line(ctx, [line.pk for line in lines])
    return page(
        request, ctx, project, "suno/materials/issue_detail.html", issue=issue,
        label=issue_label(issue, catalog),
        lines=[(line, returned.get(line.pk, (None, None))[0]) for line in lines],
        returns=scoped_qs(ctx, MaterialReturn).filter(issue=issue).order_by("number"),
        return_form=ReturnForm(initial={"returned_on": timezone.localdate()}, lines=lines)
        if can_create_costs(ctx, project)
        else None,
        can_edit=_changeable(ctx, issue, can_update_cost),
        can_delete=_changeable(ctx, issue, can_delete_cost),
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def issue_edit(request: HttpRequest, project_id: UUID, issue_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    issue = get_document(ctx, MaterialIssue, project, issue_id, "task")
    if not can_update_cost(ctx, issue):
        raise PermissionDenied
    if issue.cut_id is not None:
        messages.error(request, movements.IN_A_CUT)
        return redirect("materials:issue", project.pk, issue.pk)
    if scoped_qs(ctx, MaterialReturn).filter(issue=issue).exists():
        messages.error(request, movements.HAS_RETURNS)
        return redirect("materials:issue", project.pk, issue.pk)
    _with_items([issue])
    done, form_context = _issue_form(request, ctx, project, issue)
    if done is not None:
        messages.success(request, "Salida guardada.")
        return done
    return page(request, ctx, project, "suno/materials/issue_form.html", issue=issue,
                **form_context)  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def issue_delete(request: HttpRequest, project_id: UUID, issue_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    if run(request, lambda: movements.delete_issue(ctx, project, issue_id), "Salida borrada."):
        return redirect("materials:issues", project.pk)
    return redirect("materials:issue", project.pk, issue_id)


@login_required(login_url="platform_login")
@require_POST
def issue_return(request: HttpRequest, project_id: UUID, issue_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    issue = get_document(ctx, MaterialIssue, project, issue_id)
    lines = list(scoped_qs(ctx, MaterialIssueLine).filter(issue=issue))
    form = ReturnForm(request.POST, lines=lines)
    if form.is_valid():
        data = form.return_data()
        run(request, lambda: movements.create_return(ctx, project, issue.pk, data),
            "Devolución registrada.")  # fmt: skip
    else:
        messages.error(request, "Revisa la fecha y las cantidades de la devolución.")
    return redirect("materials:issue", project.pk, issue.pk)


@login_required(login_url="platform_login")
@require_GET
def export_issues(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """Las líneas de salida de un periodo (spec 3.4)."""
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    require_export(ctx, project)
    filters = DocumentFilters.from_query(request.GET)
    catalog = load_catalog(ctx, project)
    issues = issue_query(ctx, project, filters)
    lines = list(
        scoped_qs(ctx, MaterialIssueLine)
        .filter(issue__in=issues)
        .select_related("issue__warehouse", "issue__cut", "issue__task", "material")
        .prefetch_related("issue__item_links")
        .order_by("issue__issued_on", "issue__number", "order")[:MAX_EXPORT_ROWS]
    )
    start = filters.start.isoformat() if filters.start else "inicio"
    end = filters.end.isoformat() if filters.end else "hoy"
    return xlsx(
        ctx, project, issues_workbook([(line, issue_label(line.issue, catalog)) for line in lines]),
        f"salidas-{start}-{end}", "salidas", lines=len(lines), filters=filters.query,
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def transfer_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    can_create = can_create_costs(ctx, project)
    if request.method == "POST" and not can_create:
        raise PermissionDenied
    warehouses = _warehouses(ctx, project)
    index = material_index(ctx)
    action = request.POST.get("accion", "guardar") if request.method == "POST" else ""
    errors: list[str] = []
    if request.method == "POST":
        data = more_rows(request.POST, action) or request.POST
        form = TransferForm(data, warehouses=warehouses)
        lines = MaterialLineFormSet(data, prefix="lineas")
        if action == "guardar" and form.is_valid() and lines.is_valid():
            rows, errors = read_material_lines(lines, index)
            if not errors:
                values = form.transfer_data(rows)
                try:
                    transfer = movements.create_transfer(ctx, project, values)
                except MaterialRejected as exc:
                    errors = exc.errors
                else:
                    messages.success(request, "Traslado registrado.")
                    return redirect("materials:transfer", project.pk, transfer.pk)
    else:
        form = TransferForm(initial={"moved_on": timezone.localdate()}, warehouses=warehouses)
        lines = MaterialLineFormSet(prefix="lineas", initial=[{}] * NEW_ROWS)
    rows_list, more = transfer_rows(ctx, project)
    return page(
        request, ctx, project, "suno/materials/transfers.html", rows=rows_list, more=more,
        form=form, lines=lines, errors=errors, show_errors=action == "guardar",
        material_index=index, can_create=can_create,
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_GET
def transfer_detail(request: HttpRequest, project_id: UUID, transfer_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    transfer = get_document(ctx, MaterialTransfer, project, transfer_id, "source", "target",
                            "created_by")  # fmt: skip
    lines = (
        scoped_qs(ctx, MaterialTransferLine).filter(transfer=transfer).select_related("material")
        .order_by("order")
    )  # fmt: skip
    return page(request, ctx, project, "suno/materials/transfer_detail.html", transfer=transfer,
                lines=lines)  # fmt: skip
