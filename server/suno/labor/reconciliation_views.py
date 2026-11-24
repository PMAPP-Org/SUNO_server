"""Conciliación de nómina (costos, pieza 6d, spec 2.6 y 3.3): importar la nómina del periodo,
ver la tabla por trabajador (de la organización o de un proyecto) y exportarla. Ver pide
`can_read_labor_registry` (404); importar, `can_manage_labor_registry` (403); exportar,
`can_export_labor_registry` (403); borrar una importación, `can_delete_payroll` (403) con
confirmación. Cada consulta, exporte y borrado quedan en la auditoría."""

from __future__ import annotations

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.budgets.excel import MAX_BYTES, ExcelRejected, check_file_name
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.download import content_disposition
from suno.core.models import Project
from suno.core.policies import can_delete_payroll, can_export_labor_registry
from suno.core.scoping import scoped_qs

from . import payroll
from .access import get_payroll, registry_ctx, registry_manager
from .common import LaborRejected
from .export import reconciliation_workbook
from .forms import PayrollForm
from .queries import payroll_rows
from .registry_views import XLSX_TYPE, page


@login_required(login_url="platform_login")
@require_GET
def payroll_list(request: HttpRequest) -> HttpResponse:
    ctx = registry_ctx(request)
    rows = payroll_rows(ctx, page=request.GET.get("pagina"))
    return page(request, ctx, "suno/labor/payrolls.html", rows=rows, form=PayrollForm(),
                result=None, errors=[])  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def payroll_upload(request: HttpRequest) -> HttpResponse:
    ctx = registry_manager(request)
    form = PayrollForm(request.POST)
    upload = request.FILES.get("file")
    errors: list[str] = []
    result = None
    if not form.is_valid():
        errors = ["Escribe el periodo de la nómina."]
    elif upload is None:
        errors = ["Elige un archivo .xlsx."]
    elif upload.size is not None and upload.size > MAX_BYTES:
        errors = ["El archivo supera 20 MB."]
    else:
        try:
            check_file_name(upload.name or "")
            result = payroll.preview(
                ctx,
                upload.read(),
                form.cleaned_data["starts_on"],
                form.cleaned_data["ends_on"],
                upload.name or "nomina.xlsx",
            )
        except ExcelRejected as exc:
            errors = [str(exc)]
        except LaborRejected as exc:
            errors = exc.errors
    return page(request, ctx, "suno/labor/payrolls.html", rows=payroll_rows(ctx), form=form,
                result=result, errors=errors)  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def payroll_apply(request: HttpRequest) -> HttpResponse:
    ctx = registry_manager(request)
    try:
        imported = payroll.apply(ctx, request.POST.get("token", ""))
    except LaborRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return redirect("labor_registry:payrolls")
    messages.success(request, "Nómina importada.")
    return redirect("labor_registry:reconciliation", imported.pk)


def _project(ctx: RequestContext, value: str | None) -> Project | None:
    """El proyecto del filtro (vacío: la organización). Un id inválido o de otra organización
    es 404, no la vista de la organización: así no se confunde con un filtro aplicado."""
    if not value:
        return None
    try:
        wanted = UUID(value)
    except ValueError as exc:
        raise Http404 from exc
    found = scoped_qs(ctx, Project).filter(pk=wanted).first()
    if found is None:
        raise Http404
    return found


@login_required(login_url="platform_login")
@require_GET
def reconciliation(request: HttpRequest, payroll_id: UUID) -> HttpResponse:
    ctx = registry_ctx(request)
    imported = get_payroll(ctx, payroll_id)
    project = _project(ctx, request.GET.get("proyecto"))
    result = payroll.reconcile(ctx, imported, project)
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.LABOR_RECONCILIATION_VIEWED,
            target=f"payroll_import:{imported.pk}",
            metadata={"project": str(project.pk) if project else "", "rows": len(result.rows)},
        )
    projects = scoped_qs(ctx, Project).order_by("name").values_list("pk", "name")
    return page(request, ctx, "suno/labor/reconciliation.html", result=result,
                projects=list(projects), project=project,
                can_delete_payroll=can_delete_payroll(ctx, ctx.org_id))  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def payroll_delete(request: HttpRequest, payroll_id: UUID) -> HttpResponse:
    """Confirmación y borrado de una nómina importada (#103). Reemplazarla es borrarla e
    importar otra vez."""
    ctx = registry_ctx(request)
    if not can_delete_payroll(ctx, ctx.org_id):
        raise PermissionDenied
    imported = get_payroll(ctx, payroll_id)
    error = ""
    if request.method == "POST":
        if request.POST.get("confirm") == "1":
            payroll.delete_payroll(ctx, imported.pk)
            messages.success(request, "Nómina borrada. Puedes importar la corregida.")
            return redirect("labor_registry:payrolls")
        error = "Marca la casilla para confirmar el borrado."
    return page(request, ctx, "suno/labor/payroll_delete.html", payroll=imported, error=error)


@login_required(login_url="platform_login")
@require_GET
def reconciliation_sheet(request: HttpRequest, payroll_id: UUID) -> HttpResponse:
    ctx = registry_ctx(request)
    if not can_export_labor_registry(ctx, ctx.org_id):
        raise PermissionDenied
    imported = get_payroll(ctx, payroll_id)
    project = _project(ctx, request.GET.get("proyecto"))
    result = payroll.reconcile(ctx, imported, project)
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.LABOR_EXPORTED,
            target=f"payroll_import:{imported.pk}",
            metadata={"what": "conciliacion", "project": str(project.pk) if project else "",
                      "rows": len(result.rows)},
        )  # fmt: skip
    stem = f"conciliacion-{imported.starts_on}-{imported.ends_on}"
    response = HttpResponse(reconciliation_workbook(result), content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(stem, ".xlsx", fallback="conciliacion")
    return response


@login_required(login_url="platform_login")
@require_GET
def payroll_template(request: HttpRequest) -> HttpResponse:
    registry_manager(request)
    response = HttpResponse(payroll.template_bytes(), content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        "plantilla-nomina", ".xlsx", fallback="plantilla-nomina"
    )
    return response
