"""Vistas web de combustibles: panel, catálogo, tanqueos, liquidaciones y rendimiento."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q, Sum
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from suno.core.access import Capability, Section
from suno.core.context import RequestContext
from suno.core.models import (
    FuelLiquidation,
    FuelSource,
    FuelStation,
    FuelStore,
    LiquidationStatus,
)
from suno.core.scoping import scoped_qs
from suno.equipment.forms import (
    FuelConfirmForm,
    FuelFillForm,
    FuelLiquidationForm,
    FuelPurchaseForm,
    FuelRateForm,
    FuelStationForm,
    FuelStoreForm,
    FuelSupplierForm,
)
from suno.equipment.fuel import (
    confirm_liquidation,
    create_liquidation,
    create_station,
    create_store,
    create_supplier,
    liquidation_preview,
    recalculate_fill_prices,
    record_purchase,
    register_fill,
    set_station_rate,
    store_stock,
    visible_fills,
    yield_report,
)
from suno.equipment.inspections import is_operator
from suno.equipment.services import can_view_costs, get_visible_equipment
from suno.equipment.views import _ctx, _message_validation, _render

PAGE_SIZE = 50


def _can_manage(ctx: RequestContext) -> bool:
    return ctx.has_capability(Section.EQUIPMENT, Capability.CREATE) and not is_operator(ctx)


def _require_manage(ctx: RequestContext) -> None:
    if not _can_manage(ctx):
        raise PermissionDenied


@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def fuel_panel(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    fills = visible_fills(ctx).select_related("equipment", "station__supplier", "store")
    source = request.GET.get("source", "")
    if source in FuelSource.values:
        fills = fills.filter(source=source)
    needle = request.GET.get("q", "").strip()
    if needle:
        fills = fills.filter(
            Q(equipment__code__icontains=needle)
            | Q(voucher_number__icontains=needle)
            | Q(driver_name__icontains=needle)
        )
    page = Paginator(fills, PAGE_SIZE).get_page(request.GET.get("page"))
    show_costs = can_view_costs(ctx)
    stores = [
        (store, store_stock(ctx, store))
        for store in scoped_qs(ctx, FuelStore).filter(active=True).order_by("name")
    ]
    month_start = date.today().replace(day=1)
    month = (
        visible_fills(ctx)
        .filter(filled_at__date__gte=month_start)
        .aggregate(gallons=Sum("gallons"), cost=Sum("cost"))
    )
    return _render(
        request,
        ctx,
        "suno/equipment/fuel_panel.html",
        {
            "page": page,
            "source": source,
            "q": needle,
            "sources": FuelSource.choices,
            "stores": stores,
            "stations": scoped_qs(ctx, FuelStation)
            .filter(active=True)
            .select_related("supplier")
            .order_by("supplier__name", "name"),
            "liquidations": scoped_qs(ctx, FuelLiquidation).select_related("supplier")[:8]
            if show_costs and not is_operator(ctx)
            else [],
            "month_gallons": month["gallons"] or 0,
            "month_cost": (month["cost"] or 0) if show_costs else None,
            "show_costs": show_costs,
            "can_manage": _can_manage(ctx),
            "can_liquidate": show_costs and not is_operator(ctx),
        },
    )


def _form_view(
    request: HttpRequest,
    *,
    form_class: type[Any],
    title: str,
    submit: Callable[[RequestContext, dict[str, Any]], object],
    success: str,
    manage_only: bool = True,
    needs_ctx: bool = True,
    redirect_to: str = "equipment:fuel",
    equipment: Any = None,
) -> HttpResponse:
    ctx = _ctx(request)
    if manage_only:
        _require_manage(ctx)
    form = form_class(ctx, request.POST or None) if needs_ctx else form_class(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            submit(ctx, form.cleaned_data)
        except ValidationError as exc:
            _message_validation(request, exc)
        except IntegrityError:
            form.add_error(None, "El registro entra en conflicto con otro existente.")
        else:
            messages.success(request, success)
            return redirect(redirect_to)
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": title, "equipment": equipment},
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def supplier_create(request: HttpRequest) -> HttpResponse:
    return _form_view(
        request,
        form_class=FuelSupplierForm,
        needs_ctx=False,
        title="Nuevo proveedor de combustible",
        submit=lambda ctx, data: create_supplier(ctx, **data),
        success="Proveedor creado.",
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def station_create(request: HttpRequest) -> HttpResponse:
    return _form_view(
        request,
        form_class=FuelStationForm,
        title="Nueva estación de servicio",
        submit=lambda ctx, data: create_station(
            ctx, supplier_id=data["supplier"].pk, name=data["name"]
        ),
        success="Estación creada.",
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def rate_create(request: HttpRequest) -> HttpResponse:
    def submit(ctx: RequestContext, data: dict[str, Any]) -> object:
        rate = set_station_rate(
            ctx,
            station_id=data["station"].pk,
            valid_from=data["valid_from"],
            price_per_gallon=data["price_per_gallon"],
        )
        recalculate_fill_prices(ctx, station_id=data["station"].pk)
        return rate

    return _form_view(
        request,
        form_class=FuelRateForm,
        title="Tarifa por galón de una estación",
        submit=submit,
        success="Tarifa registrada; los tanqueos sin tarifa se valoraron.",
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def store_create(request: HttpRequest) -> HttpResponse:
    return _form_view(
        request,
        form_class=FuelStoreForm,
        title="Nuevo tanque propio",
        submit=lambda ctx, data: create_store(
            ctx, name=data["name"], project_id=data["project"].pk if data["project"] else None
        ),
        success="Tanque creado.",
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def purchase_create(request: HttpRequest) -> HttpResponse:
    return _form_view(
        request,
        form_class=FuelPurchaseForm,
        title="Compra de combustible al tanque",
        submit=lambda ctx, data: record_purchase(ctx, store_id=data.pop("store").pk, **data),
        success="Compra registrada: el stock del tanque aumentó.",
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def fill_create(request: HttpRequest) -> HttpResponse:
    def submit(ctx: RequestContext, data: dict[str, Any]) -> object:
        station, store = data.pop("station"), data.pop("store")
        equipment = data.pop("equipment")
        return register_fill(
            ctx,
            fill_id=uuid.uuid4(),
            equipment_id=equipment.pk,
            station_id=station.pk if station else None,
            store_id=store.pk if store else None,
            **{**data, "filled_at": data["filled_at"] or timezone.now()},
        )

    return _form_view(
        request,
        form_class=FuelFillForm,
        title="Registrar tanqueo",
        submit=submit,
        success="Tanqueo registrado.",
        manage_only=False,
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def liquidation_create(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    if not can_view_costs(ctx) or is_operator(ctx):
        raise PermissionDenied
    form = FuelLiquidationForm(ctx, request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            liquidation = create_liquidation(
                ctx,
                supplier_id=form.cleaned_data["supplier"].pk,
                date_from=form.cleaned_data["date_from"],
                date_to=form.cleaned_data["date_to"],
            )
        except ValidationError as exc:
            _message_validation(request, exc)
        else:
            return redirect("equipment:fuel_liquidation", liquidation.pk)
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": "Nueva liquidación de combustible"},
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def liquidation_detail(request: HttpRequest, liquidation_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    if not can_view_costs(ctx) or is_operator(ctx):
        raise PermissionDenied
    liquidation = (
        scoped_qs(ctx, FuelLiquidation).select_related("supplier").filter(pk=liquidation_id).first()
    )
    if liquidation is None:
        raise Http404
    preview = None
    if liquidation.status == LiquidationStatus.DRAFT:
        preview = liquidation_preview(
            ctx,
            supplier_id=liquidation.supplier_id,
            date_from=liquidation.date_from,
            date_to=liquidation.date_to,
        )
    return _render(
        request,
        ctx,
        "suno/equipment/liquidation_detail.html",
        {
            "liquidation": liquidation,
            "preview": preview,
            "fills": liquidation.fills.select_related("equipment", "station").order_by(
                "station__name", "equipment__code", "filled_at"
            ),
            "form": FuelConfirmForm(),
        },
    )


@login_required(login_url="platform_login")
@require_POST
def liquidation_confirm(request: HttpRequest, liquidation_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    form = FuelConfirmForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Revisa el valor del anticipo.")
    else:
        try:
            confirm_liquidation(
                ctx, liquidation_id, advance_applied=form.cleaned_data["advance_applied"]
            )
        except ValidationError as exc:
            _message_validation(request, exc)
        else:
            messages.success(request, "Liquidación confirmada.")
    return redirect("equipment:fuel_liquidation", liquidation_id)


@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def yield_view(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    equipment = get_visible_equipment(ctx, equipment_id)
    until = date.today()
    try:
        months = max(1, min(24, int(request.GET.get("months", "6"))))
    except ValueError:
        months = 6
    since = (until.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    return _render(
        request,
        ctx,
        "suno/equipment/fuel_yield.html",
        {
            "equipment": equipment,
            "rows": yield_report(ctx, equipment, since=since, until=until),
            "months": months,
            "since": since,
            "until": until,
        },
    )
