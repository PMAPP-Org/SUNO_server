"""Cortes de consumos en la pestaña "Materiales" (costos, pieza 6c, spec 3.2 "Cortes")."""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.download import content_disposition
from suno.core.models import ConsumptionCut
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_export_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.costs.catalog import load_catalog
from suno.projects.views import _ctx, _parse_date

from . import cuts
from .access import get_document, get_materials_project
from .common import MaterialRejected, attachment_url
from .export import cut_workbook
from .pdf import cut_pdf
from .queries import cut_rows
from .views import download, page, require_export, run, xlsx
from .views_purchases import back


@login_required(login_url="platform_login")
@require_GET
def cut_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """La lista y la propuesta del próximo corte hasta la fecha que se elija (por defecto,
    hoy). La propuesta no guarda nada."""
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    start = cuts.default_start(ctx, project)
    end = _parse_date(request.GET.get("hasta")) or timezone.localdate()
    proposal = None
    error = ""
    if start is not None:
        try:
            proposal = cuts.proposal_until(ctx, project, end, load_catalog(ctx, project))
        except MaterialRejected as exc:
            error = exc.errors[0]
    rows, more = cut_rows(ctx, project)
    return page(
        request, ctx, project, "suno/materials/cuts.html", rows=rows, more=more, start=start,
        end=end, proposal=proposal, error=error,
    )  # fmt: skip


def _detail(project_id: UUID, cut_id: UUID) -> HttpResponse:
    return redirect("materials:cut", project_id, cut_id)


@login_required(login_url="platform_login")
@require_POST
def cut_create(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    end = _parse_date(request.POST.get("hasta")) or timezone.localdate()
    created: list[ConsumptionCut] = []
    if run(request, lambda: created.append(cuts.create_cut(ctx, project, end)),
           "Corte guardado en borrador: descarga el PDF, hazlo firmar y súbelo."):  # fmt: skip
        return _detail(project.pk, created[0].pk)
    return redirect(f"{reverse('materials:cuts', args=[project.pk])}?hasta={end.isoformat()}")


@login_required(login_url="platform_login")
@require_GET
def cut_detail(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    cut = get_document(ctx, ConsumptionCut, project, cut_id, "created_by", "updated_by",
                       "decided_by", "voided_by")  # fmt: skip
    is_open = cut.status in OPEN_STATES
    changed = False
    if is_open:
        live = cuts.build_proposal(ctx, project, cut.period_start, cut.period_end,
                                   load_catalog(ctx, project))  # fmt: skip
        changed = (
            live.fingerprint() != (cut.summary or {}).get("fingerprint")
            or cuts.default_start(ctx, project) != cut.period_start
        )
    mine = ctx.user_id in (cut.created_by_id, cut.updated_by_id)
    url = reverse("materials:cut", args=[project.pk, cut.pk])
    return page(
        request, ctx, project, "suno/materials/cut_detail.html", cut=cut, changed=changed,
        groups=(cut.summary or {}).get("groups", []),
        can_edit=is_open and can_update_cost(ctx, cut)
        and (cut.status == "draft" or cut.created_by_id == ctx.user_id),
        can_delete=is_open and can_delete_cost(ctx, cut),
        can_attach=cut.status == "draft" and can_update_cost(ctx, cut),
        can_decide=cut.status == "draft" and can_approve_costs(ctx, project) and not mine,
        can_void=cut.status == "approved" and can_reverse_costs(ctx, project),
        needs="" if cut.attachment_id else "Falta el reporte firmado",
        urls={"approve": url + "aprobar/", "reject": url + "rechazar/", "void": url + "anular/",
              "upload": url + "firmado/", "view": url + "firmado/ver/"},
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def cut_recalculate(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    end = _parse_date(request.POST.get("hasta")) or timezone.localdate()
    run(request, lambda: cuts.recalculate_cut(ctx, project, cut_id, end),
        "Corte recalculado: vuelve a imprimirlo y a cargar el reporte firmado.")  # fmt: skip
    return _detail(project.pk, cut_id)


@login_required(login_url="platform_login")
@require_POST
def cut_delete(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    if run(request, lambda: cuts.delete_cut(ctx, project, cut_id), "Borrador del corte borrado."):
        return redirect("materials:cuts", project.pk)
    return _detail(project.pk, cut_id)


@login_required(login_url="platform_login")
@require_POST
def cut_signed(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    upload = request.FILES.get("file")
    if upload is not None:
        name = upload.name or "reporte-firmado.pdf"
        run(request, lambda: cuts.attach_signed_report(ctx, project, cut_id, upload, name),
            "Reporte firmado adjunto; ya se puede aprobar.")  # fmt: skip
    return _detail(project.pk, cut_id)


@login_required(login_url="platform_login")
@require_GET
def cut_signed_file(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    return download(attachment_url(ctx, project, get_document(ctx, ConsumptionCut, project,
                                                              cut_id)))  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def cut_approve(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    run(request, lambda: cuts.approve_cut(ctx, project, cut_id),
        "Corte aprobado: sus costos están en el libro y el periodo quedó cerrado.")  # fmt: skip
    return back(request, project.pk, _detail(project.pk, cut_id))


@login_required(login_url="platform_login")
@require_POST
def cut_reject(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: cuts.reject_cut(ctx, project, cut_id, note),
        "Corte rechazado; su autor lo verá con el motivo.")  # fmt: skip
    return back(request, project.pk, _detail(project.pk, cut_id))


@login_required(login_url="platform_login")
@require_POST
def cut_void(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: cuts.void_cut(ctx, project, cut_id, note),
        "Corte anulado: sus reversos están en el libro y el periodo se reabrió.")  # fmt: skip
    return _detail(project.pk, cut_id)


@login_required(login_url="platform_login")
@require_GET
def cut_document(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    """El corte para imprimir y firmar (auditado)."""
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    cut = get_document(ctx, ConsumptionCut, project, cut_id)
    if not can_export_costs(ctx, project):
        raise PermissionDenied
    content = cut_pdf(project, cut, generated_on=timezone.localdate())
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_EXPORTED,
            target=f"consumption_cut:{cut.pk}",
            metadata={"project": str(project.pk), "number": cut.number, "status": cut.status},
        )
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = content_disposition(
        f"corte-{cut.number}", ".pdf", fallback="corte"
    )
    return response


@login_required(login_url="platform_login")
@require_GET
def cut_sheet(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    require_export(ctx, project)
    cut = get_document(ctx, ConsumptionCut, project, cut_id)
    return xlsx(ctx, project, cut_workbook(cut), f"corte-{cut.number}", "corte",
                cut=str(cut.pk))  # fmt: skip
