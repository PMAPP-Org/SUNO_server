"""Vistas web de preoperacionales: bandeja, revisión, novedades y lista de chequeo."""

from __future__ import annotations

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods, require_POST

from suno.core.access import Capability, Section
from suno.core.models import (
    EquipmentInspection,
    EquipmentType,
    InspectionStatus,
    PreoperationalItem,
)
from suno.core.scoping import scoped_qs
from suno.equipment.inspections import (
    checklist_for,
    close_fault,
    define_checklist_item,
    is_operator,
    retire_checklist_item,
    review_inspection,
    visible_faults,
    visible_inspections,
)
from suno.equipment.views import _ctx, _message_validation, _render

PAGE_SIZE = 50


@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def inspection_list(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    queryset = visible_inspections(ctx).select_related("equipment", "operator", "reviewed_by")
    status = request.GET.get("status", "")
    if status in InspectionStatus.values:
        queryset = queryset.filter(status=status)
    needle = request.GET.get("q", "").strip()
    if needle:
        queryset = queryset.filter(
            Q(equipment__code__icontains=needle) | Q(equipment__name__icontains=needle)
        )
    pending = visible_inspections(ctx).filter(status=InspectionStatus.SUBMITTED).count()
    with_faults = visible_faults(ctx).filter(status="open").aggregate(n=Count("id"))["n"]
    page = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get("page"))
    return _render(
        request,
        ctx,
        "suno/equipment/inspection_list.html",
        {
            "page": page,
            "status": status,
            "q": needle,
            "statuses": InspectionStatus.choices,
            "pending": pending,
            "open_faults": with_faults,
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def inspection_detail(request: HttpRequest, inspection_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    try:
        inspection = (
            visible_inspections(ctx)
            .select_related("equipment", "operator", "reviewed_by", "project")
            .get(pk=inspection_id)
        )
    except EquipmentInspection.DoesNotExist as exc:
        raise Http404 from exc
    can_review = (
        inspection.status == InspectionStatus.SUBMITTED
        and inspection.operator_id != ctx.user_id
        and ctx.has_capability(Section.EQUIPMENT, Capability.REVIEW)
    )
    return _render(
        request,
        ctx,
        "suno/equipment/inspection_detail.html",
        {
            "inspection": inspection,
            "faults": inspection.faults.order_by("-critical", "item_label"),
            "can_review": can_review,
            "can_close_faults": ctx.has_capability(Section.EQUIPMENT, Capability.UPDATE)
            and not is_operator(ctx),
        },
    )


@login_required(login_url="platform_login")
@require_POST
def inspection_review(request: HttpRequest, inspection_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    try:
        review_inspection(
            ctx,
            inspection_id,
            decision=request.POST.get("decision", ""),
            comment=request.POST.get("comment", ""),
        )
    except ValidationError as exc:
        _message_validation(request, exc)
    else:
        messages.success(request, "Decisión registrada.")
    return redirect("equipment:inspection_detail", inspection_id)


@login_required(login_url="platform_login")
@require_POST
def fault_close(request: HttpRequest, fault_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    fault = visible_faults(ctx).filter(pk=fault_id).select_related("inspection").first()
    if fault is None:
        raise Http404
    try:
        close_fault(ctx, fault_id, note=request.POST.get("note", ""))
    except ValidationError as exc:
        _message_validation(request, exc)
    else:
        messages.success(request, "Novedad cerrada.")
    return redirect("equipment:inspection_detail", fault.inspection_id)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def checklist_edit(request: HttpRequest, type_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    try:
        equipment_type = scoped_qs(ctx, EquipmentType).get(pk=type_id)
    except EquipmentType.DoesNotExist as exc:
        raise Http404 from exc
    can_edit = ctx.has_capability(Section.EQUIPMENT, Capability.UPDATE)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        try:
            if request.POST.get("action") == "retire":
                retire_checklist_item(ctx, UUID(request.POST.get("item_id", "")))
                messages.success(request, "Ítem retirado de la lista.")
            else:
                define_checklist_item(
                    ctx,
                    equipment_type_id=equipment_type.pk,
                    label=request.POST.get("label", ""),
                    critical=request.POST.get("critical") == "on",
                )
                messages.success(request, "Ítem agregado a la lista.")
        except ValidationError as exc:
            _message_validation(request, exc)
        except (IntegrityError, ValueError):
            messages.error(
                request, "No fue posible guardar el ítem. Verifica que no esté repetido."
            )
        return redirect("equipment:checklist", type_id)
    return _render(
        request,
        ctx,
        "suno/equipment/checklist.html",
        {
            "equipment_type": equipment_type,
            "items": checklist_for(ctx, equipment_type.pk),
            "can_edit": can_edit,
            "retired": scoped_qs(ctx, PreoperationalItem)
            .filter(equipment_type=equipment_type, active=False)
            .count(),
        },
    )
