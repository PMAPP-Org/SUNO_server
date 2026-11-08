"""Vistas web de mantenimiento: panel de alertas, órdenes de trabajo, planes y Hoja de Vida."""

from __future__ import annotations

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods, require_POST

from suno.core import audit
from suno.core.access import Capability, Section
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    OPEN_ORDER_STATUSES,
    EquipmentFault,
    Membership,
    WorkOrderKind,
    WorkOrderStatus,
)
from suno.core.scoping import scoped_qs
from suno.equipment.forms import (
    PlanForm,
    WorkOrderCloseForm,
    WorkOrderForm,
    WorkOrderLineForm,
)
from suno.equipment.hoja_de_vida import hoja_de_vida_data, hoja_de_vida_pdf
from suno.equipment.inspections import is_operator
from suno.equipment.maintenance import (
    add_line,
    approve_order,
    assign_mechanic,
    cancel_order,
    close_order,
    create_plan,
    create_work_order,
    maintenance_alerts,
    remove_line,
    retire_plan,
    start_order,
    visible_orders,
    visible_plans,
)
from suno.equipment.services import get_visible_equipment
from suno.equipment.views import _ctx, _message_validation, _render

PAGE_SIZE = 50


def _can_manage(ctx: RequestContext) -> bool:
    return ctx.has_capability(Section.EQUIPMENT, Capability.UPDATE) and not is_operator(ctx)


@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def maintenance_panel(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    orders = visible_orders(ctx).select_related("equipment", "plan")
    status = request.GET.get("status", "")
    if status in WorkOrderStatus.values:
        orders = orders.filter(status=status)
    elif status == "open":
        orders = orders.filter(status__in=OPEN_ORDER_STATUSES)
    kind = request.GET.get("kind", "")
    if kind in WorkOrderKind.values:
        orders = orders.filter(kind=kind)
    needle = request.GET.get("q", "").strip()
    if needle:
        orders = orders.filter(
            Q(equipment__code__icontains=needle) | Q(equipment__name__icontains=needle)
        )
    page = Paginator(orders, PAGE_SIZE).get_page(request.GET.get("page"))
    return _render(
        request,
        ctx,
        "suno/equipment/maintenance_panel.html",
        {
            "alerts": maintenance_alerts(ctx),
            "page": page,
            "status": status,
            "kind": kind,
            "q": needle,
            "statuses": WorkOrderStatus.choices,
            "kinds": WorkOrderKind.choices,
            "can_manage": _can_manage(ctx),
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def order_create(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    """Crea una orden en borrador. `?plan=` o `?fault=` precargan su origen."""

    ctx = _ctx(request)
    equipment = get_visible_equipment(ctx, equipment_id)
    if not _can_manage(ctx) or not ctx.has_capability(Section.EQUIPMENT, Capability.CREATE):
        raise PermissionDenied
    plan_id = request.GET.get("plan") or None
    fault_ids = request.GET.getlist("fault")
    try:
        plan_uuid = UUID(plan_id) if plan_id else None
        fault_uuids = [UUID(value) for value in fault_ids]
    except ValueError as exc:
        raise Http404 from exc
    initial = {}
    if plan_uuid:
        plan = visible_plans(ctx).filter(pk=plan_uuid, equipment=equipment).first()
        if plan is None:
            raise Http404
        initial = {
            "kind": WorkOrderKind.PREVENTIVE,
            "description": f"Mantenimiento preventivo: {plan.name}",
        }
    elif fault_uuids:
        faults = scoped_qs(ctx, EquipmentFault).filter(pk__in=fault_uuids, equipment=equipment)
        initial = {
            "kind": WorkOrderKind.CORRECTIVE,
            "description": "\n".join(
                f"{fault.item_label}: {fault.description}" for fault in faults
            ),
        }
    form = WorkOrderForm(ctx, request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            order = create_work_order(
                ctx,
                equipment_id=equipment.pk,
                kind=form.cleaned_data["kind"],
                description=form.cleaned_data["description"],
                plan_id=plan_uuid,
                fault_ids=fault_uuids,
                mechanic_ids=[m.user_id for m in form.cleaned_data["mechanics"]],
            )
        except ValidationError as exc:
            _message_validation(request, exc)
        else:
            messages.success(request, f"Orden {order.code} creada en borrador.")
            return redirect("equipment:order_detail", order.pk)
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": "Nueva orden de trabajo", "equipment": equipment},
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def order_detail(request: HttpRequest, order_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    order = (
        visible_orders(ctx)
        .select_related("equipment", "plan", "created_by", "approved_by", "closed_by")
        .filter(pk=order_id)
        .first()
    )
    if order is None:
        raise Http404
    lines = list(order.lines.all())
    manage = _can_manage(ctx)
    is_open = order.status in OPEN_ORDER_STATUSES
    return _render(
        request,
        ctx,
        "suno/equipment/order_detail.html",
        {
            "order": order,
            "lines": lines,
            "total": sum((line.total for line in lines), start=0),
            "mechanics": order.mechanics.select_related("mechanic"),
            "faults": order.faults.all(),
            "can_edit": manage and is_open,
            "can_approve": order.status == WorkOrderStatus.DRAFT
            and order.created_by_id != ctx.user_id
            and ctx.has_capability(Section.EQUIPMENT, Capability.APPROVE),
            "can_start": manage and order.status == WorkOrderStatus.APPROVED,
            "can_cancel": manage
            and order.status in (WorkOrderStatus.DRAFT, WorkOrderStatus.APPROVED),
            "can_close": manage and order.status == WorkOrderStatus.IN_EXECUTION,
            "members": scoped_qs(ctx, Membership).select_related("user").order_by("user__username"),
            "line_form": WorkOrderLineForm(),
            "close_form": WorkOrderCloseForm(ctx),
        },
    )


@login_required(login_url="platform_login")
@require_POST
def order_action(request: HttpRequest, order_id: UUID, action: str) -> HttpResponse:
    ctx = _ctx(request)
    post = request.POST
    try:
        if action == "approve":
            approve_order(ctx, order_id)
            messages.success(request, "Orden aprobada.")
        elif action == "start":
            start_order(ctx, order_id)
            messages.success(request, "Trabajos iniciados: el equipo queda en mantenimiento.")
        elif action == "cancel":
            cancel_order(ctx, order_id, reason=post.get("reason", ""))
            messages.success(request, "Orden cancelada.")
        elif action == "mechanic":
            assign_mechanic(ctx, order_id, mechanic_id=UUID(post.get("mechanic", "")))
            messages.success(request, "Mecánico asignado.")
        elif action == "line":
            form = WorkOrderLineForm(post)
            if not form.is_valid():
                messages.error(request, "Revisa los datos de la línea.")
            else:
                add_line(ctx, order_id, **form.cleaned_data)
                messages.success(request, "Línea agregada.")
        elif action == "remove-line":
            remove_line(ctx, UUID(post.get("line", "")))
            messages.success(request, "Línea eliminada.")
        elif action == "close":
            form = WorkOrderCloseForm(ctx, post)
            if not form.is_valid():
                messages.error(request, "Revisa los datos del cierre técnico.")
            else:
                data = form.cleaned_data
                close_order(
                    ctx,
                    order_id,
                    downtime_hours=data["downtime_hours"],
                    failed_part=data["failed_part"],
                    failure_cause=data["failure_cause"],
                    notes=data["notes"],
                    mechanic_ids=[m.user_id for m in data["mechanics"]],
                )
                messages.success(request, "Orden cerrada técnicamente.")
        else:
            raise Http404
    except ValidationError as exc:
        _message_validation(request, exc)
    except ValueError:
        messages.error(request, "Solicitud inválida.")
    return redirect("equipment:order_detail", order_id)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def plan_create(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    equipment = get_visible_equipment(ctx, equipment_id)
    if not _can_manage(ctx):
        raise PermissionDenied
    form = PlanForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_plan(ctx, equipment_id=equipment.pk, **form.cleaned_data)
        except (ValidationError, IntegrityError) as exc:
            if isinstance(exc, ValidationError):
                _message_validation(request, exc)
            else:
                form.add_error(None, "No fue posible guardar el plan.")
        else:
            messages.success(request, "Plan de mantenimiento creado.")
            return redirect(f"{_detail_url(equipment.pk)}?tab=maintenance")
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": "Nuevo plan de mantenimiento", "equipment": equipment},
    )


def _detail_url(equipment_id: UUID) -> str:
    return reverse("equipment:detail", args=[equipment_id])


@login_required(login_url="platform_login")
@require_POST
def plan_retire(request: HttpRequest, plan_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    plan = visible_plans(ctx).filter(pk=plan_id).first()
    if plan is None:
        raise Http404
    try:
        retire_plan(ctx, plan_id)
    except ValidationError as exc:
        _message_validation(request, exc)
    else:
        messages.success(request, "Plan retirado.")
    return redirect(f"{_detail_url(plan.equipment_id)}?tab=maintenance")


@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def hoja_de_vida(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    equipment = get_visible_equipment(ctx, equipment_id)
    pdf = hoja_de_vida_pdf(hoja_de_vida_data(ctx, equipment))
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_LIFE_SHEET_EXPORTED,
        target=f"equipment:{equipment.pk}",
        metadata={"code": equipment.code},
    )
    response = HttpResponse(pdf, content_type="application/pdf")
    name = slugify(equipment.code) or "equipo"
    response["Content-Disposition"] = f'inline; filename="hoja-de-vida-{name}.pdf"'
    response["Cache-Control"] = "private, no-store"
    return response
