"""Vistas web de transporte de materiales: informe, catálogo, viajes y liquidación de fletes."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods, require_POST

from suno.core.context import RequestContext
from suno.core.models import (
    LiquidationStatus,
    TransportContractor,
    TransportMaterial,
    TransportSettlement,
)
from suno.core.scoping import scoped_qs
from suno.equipment.forms import (
    FuelConfirmForm,
    TransportContractorForm,
    TransportMaterialForm,
    TransportRateForm,
    TransportRouteForm,
    TransportSettlementForm,
    TransportVehicleForm,
    TripForm,
)
from suno.equipment.fuel_views import _can_manage, _equipment_initial, _form_view
from suno.equipment.inspections import is_operator
from suno.equipment.services import can_view_costs
from suno.equipment.transport import (
    confirm_settlement,
    create_contractor,
    create_material,
    create_route,
    create_settlement,
    create_vehicle,
    recalculate_trip_prices,
    register_trip,
    set_rate,
    settlement_preview,
    trip_report,
)
from suno.equipment.views import _ctx, _message_validation, _render

PAGE_SIZE = 50
HOME = "equipment:transport"


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _uuid(value: str) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


def _can_settle(ctx: RequestContext) -> bool:
    return can_view_costs(ctx) and not is_operator(ctx)


@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def transport_panel(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    get = request.GET
    settled = {"settled": True, "pending": False}.get(get.get("state", ""))
    contractor_id = _uuid(get.get("contractor", ""))
    material_id = _uuid(get.get("material", ""))
    report = trip_report(
        ctx,
        date_from=_date(get.get("from", "")),
        date_to=_date(get.get("to", "")),
        zone=get.get("zone", "").strip(),
        contractor_id=contractor_id,
        material_id=material_id,
        settled=settled,
    )
    page = Paginator(report.trips, PAGE_SIZE).get_page(get.get("page"))
    show_costs = can_view_costs(ctx)
    query = urlencode(
        {key: get.get(key, "") for key in ("from", "to", "zone", "contractor", "material", "state")}
    )
    return _render(
        request,
        ctx,
        "suno/equipment/transport_panel.html",
        {
            "report": report,
            "page": page,
            "query": query,
            "filters": {key: get.get(key, "") for key in ("from", "to", "zone", "state")},
            "contractor_id": str(contractor_id or ""),
            "material_id": str(material_id or ""),
            "contractors": scoped_qs(ctx, TransportContractor).order_by("name"),
            "materials": scoped_qs(ctx, TransportMaterial).order_by("name"),
            "settlements": scoped_qs(ctx, TransportSettlement).select_related("contractor")[:8]
            if _can_settle(ctx)
            else [],
            "show_costs": show_costs,
            "can_manage": _can_manage(ctx),
            "can_settle": _can_settle(ctx),
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def material_create(request: HttpRequest) -> HttpResponse:
    return _form_view(
        request,
        form_class=TransportMaterialForm,
        needs_ctx=False,
        title="Nuevo material",
        submit=lambda ctx, data: create_material(ctx, **data),
        success="Material creado.",
        redirect_to=HOME,
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def route_create(request: HttpRequest) -> HttpResponse:
    return _form_view(
        request,
        form_class=TransportRouteForm,
        needs_ctx=False,
        title="Nuevo tramo del mapa de distancias",
        submit=lambda ctx, data: create_route(ctx, **data),
        success="Tramo creado.",
        redirect_to=HOME,
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def rate_create(request: HttpRequest) -> HttpResponse:
    def submit(ctx: RequestContext, data: dict[str, Any]) -> object:
        rate = set_rate(
            ctx,
            route_id=data["route"].pk,
            material_id=data["material"].pk,
            valid_from=data["valid_from"],
            price_per_m3=data["price_per_m3"],
        )
        recalculate_trip_prices(ctx)
        return rate

    return _form_view(
        request,
        form_class=TransportRateForm,
        title="Tarifa por m³ de un material en un tramo",
        submit=submit,
        success="Tarifa registrada; los viajes sin tarifa se valoraron.",
        redirect_to=HOME,
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def contractor_create(request: HttpRequest) -> HttpResponse:
    return _form_view(
        request,
        form_class=TransportContractorForm,
        needs_ctx=False,
        title="Nueva empresa de transporte",
        submit=lambda ctx, data: create_contractor(ctx, **data),
        success="Empresa creada.",
        redirect_to=HOME,
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def vehicle_create(request: HttpRequest) -> HttpResponse:
    return _form_view(
        request,
        form_class=TransportVehicleForm,
        title="Nuevo vehículo de terceros",
        submit=lambda ctx, data: create_vehicle(
            ctx,
            contractor_id=data["contractor"].pk,
            plate=data["plate"],
            capacity_m3=data["capacity_m3"],
        ),
        success="Vehículo creado.",
        redirect_to=HOME,
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def trip_create(request: HttpRequest) -> HttpResponse:
    def submit(ctx: RequestContext, data: dict[str, Any]) -> object:
        equipment, vehicle = data["equipment"], data["vehicle"]
        return register_trip(
            ctx,
            trip_id=uuid.uuid4(),
            recorded_at=data["recorded_at"] or timezone.now(),
            route_id=data["route"].pk,
            material_id=data["material"].pk,
            volume_m3=data["volume_m3"],
            requested_by_id=data["requested_by"].user_id,
            dispatched_by_id=data["dispatched_by"].user_id,
            received_by_id=data["received_by"].user_id,
            equipment_id=equipment.pk if equipment else None,
            vehicle_id=vehicle.pk if vehicle else None,
            driver_name=data["driver_name"],
            driver_document=data["driver_document"],
            notes=data["notes"],
        )

    return _form_view(
        request,
        form_class=TripForm,
        title="Registrar viaje",
        submit=submit,
        success="Viaje registrado.",
        manage_only=False,
        initial=_equipment_initial(request),
        redirect_to=HOME,
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def settlement_create(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    if not _can_settle(ctx):
        raise PermissionDenied
    form = TransportSettlementForm(ctx, request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            settlement = create_settlement(
                ctx,
                contractor_id=form.cleaned_data["contractor"].pk,
                date_from=form.cleaned_data["date_from"],
                date_to=form.cleaned_data["date_to"],
            )
        except ValidationError as exc:
            _message_validation(request, exc)
        else:
            return redirect("equipment:transport_settlement", settlement.pk)
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": "Nueva liquidación de fletes"},
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def settlement_detail(request: HttpRequest, settlement_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    if not _can_settle(ctx):
        raise PermissionDenied
    settlement = (
        scoped_qs(ctx, TransportSettlement)
        .select_related("contractor")
        .filter(pk=settlement_id)
        .first()
    )
    if settlement is None:
        raise Http404
    preview = None
    if settlement.status == LiquidationStatus.DRAFT:
        preview = settlement_preview(
            ctx,
            contractor_id=settlement.contractor_id,
            date_from=settlement.date_from,
            date_to=settlement.date_to,
        )
    return _render(
        request,
        ctx,
        "suno/equipment/transport_settlement.html",
        {
            "settlement": settlement,
            "preview": preview,
            "trips": settlement.trips.select_related("vehicle", "route", "material").order_by(
                "vehicle__plate", "number"
            ),
            "form": FuelConfirmForm(),
        },
    )


@login_required(login_url="platform_login")
@require_POST
def settlement_confirm(request: HttpRequest, settlement_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    form = FuelConfirmForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Revisa el valor del anticipo.")
    else:
        try:
            confirm_settlement(
                ctx, settlement_id, advance_applied=form.cleaned_data["advance_applied"]
            )
        except ValidationError as exc:
            _message_validation(request, exc)
        else:
            messages.success(request, "Liquidación confirmada.")
    return redirect("equipment:transport_settlement", settlement_id)
