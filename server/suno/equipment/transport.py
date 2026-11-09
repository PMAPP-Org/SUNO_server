"""Transporte de materiales (M&E fase 5): catálogo, viajes, informe y liquidación de fletes.

Reglas que este módulo garantiza:
- Un viaje es idempotente por `id` y lleva exactamente un vehículo: un equipo propio o uno
  subcontratado.
- Exige tres usuarios de la organización (solicita, despacha, recibe); quien despacha no es
  quien recibe.
- Fija al registrarse la distancia del tramo y la tarifa por m³ vigente de su material; sin
  tarifa queda sin valorar y se valora después.
- Los fletes de terceros se liquidan por contratista y periodo, con todo valorado; al
  confirmar los viajes quedan ligados y no vuelven a otra liquidación.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Count, Max, Q, QuerySet, Sum
from django.http import Http404
from django.utils import timezone

from suno.budgets.totals import money
from suno.core import audit
from suno.core.access import Capability, Section, role_has_capability
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    EquipmentProject,
    EquipmentStatus,
    LiquidationStatus,
    Membership,
    TransportContractor,
    TransportMaterial,
    TransportRate,
    TransportRoute,
    TransportSettlement,
    TransportVehicle,
    Trip,
)
from suno.core.scoping import scoped_qs
from suno.equipment.fuel import _require_catalog, _require_costs
from suno.equipment.inspections import is_operator
from suno.equipment.services import _require_for_equipment, get_visible_equipment, visible_equipment

__all__ = [
    "SettlementPreview",
    "TripReport",
    "VehicleBlock",
    "confirm_settlement",
    "create_contractor",
    "create_material",
    "create_route",
    "create_settlement",
    "create_vehicle",
    "recalculate_trip_prices",
    "register_trip",
    "set_rate",
    "settlement_preview",
    "trip_report",
    "visible_trips",
]

_TRIP_LOCK = "trip-number"
_SETTLEMENT_LOCK = "transport-settlement-number"


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------


def _audit_catalog(ctx: RequestContext, target: str, **metadata: str) -> None:
    audit.record(
        ctx, action=AuditAction.TRANSPORT_CATALOG_CHANGED, target=target, metadata=metadata
    )


@transaction.atomic
def create_material(ctx: RequestContext, *, name: str) -> TransportMaterial:
    _require_catalog(ctx)
    material = TransportMaterial(org_id=ctx.org_id, name=name.strip())
    material.full_clean()
    material.save()
    _audit_catalog(ctx, f"transport-material:{material.pk}", material=material.name)
    return material


@transaction.atomic
def create_route(
    ctx: RequestContext, *, origin: str, destination: str, distance_km: Decimal, zone: str = ""
) -> TransportRoute:
    _require_catalog(ctx)
    route = TransportRoute(
        org_id=ctx.org_id,
        origin=origin.strip(),
        destination=destination.strip(),
        zone=zone.strip(),
        distance_km=distance_km,
    )
    route.full_clean()
    route.save()
    _audit_catalog(ctx, f"transport-route:{route.pk}", route=str(route), km=str(distance_km))
    return route


@transaction.atomic
def set_rate(
    ctx: RequestContext,
    *,
    route_id: UUID,
    material_id: UUID,
    valid_from: date,
    price_per_m3: Decimal,
) -> TransportRate:
    """Tarifa por m³ de un material en un tramo desde una fecha. No edita las anteriores."""

    _require_catalog(ctx)
    try:
        route = scoped_qs(ctx, TransportRoute).get(pk=route_id)
        material = scoped_qs(ctx, TransportMaterial).get(pk=material_id)
    except (TransportRoute.DoesNotExist, TransportMaterial.DoesNotExist) as exc:
        raise Http404 from exc
    if (
        scoped_qs(ctx, TransportRate)
        .filter(route=route, material=material, valid_from=valid_from)
        .exists()
    ):
        raise ValidationError("Ya existe una tarifa con esa fecha; usa una posterior.")
    rate = TransportRate(
        org_id=ctx.org_id,
        route=route,
        material=material,
        valid_from=valid_from,
        price_per_m3=price_per_m3,
        created_by_id=ctx.user_id,
    )
    rate.full_clean()
    rate.save()
    _audit_catalog(
        ctx,
        f"transport-rate:{rate.pk}",
        route=str(route),
        material=material.name,
        price=str(price_per_m3),
    )
    return rate


@transaction.atomic
def create_contractor(ctx: RequestContext, *, name: str, tax_id: str = "") -> TransportContractor:
    _require_catalog(ctx)
    contractor = TransportContractor(org_id=ctx.org_id, name=name.strip(), tax_id=tax_id.strip())
    contractor.full_clean()
    contractor.save()
    _audit_catalog(ctx, f"transport-contractor:{contractor.pk}", contractor=contractor.name)
    return contractor


@transaction.atomic
def create_vehicle(
    ctx: RequestContext,
    *,
    contractor_id: UUID,
    plate: str,
    capacity_m3: Decimal | None = None,
) -> TransportVehicle:
    _require_catalog(ctx)
    try:
        contractor = scoped_qs(ctx, TransportContractor).get(pk=contractor_id, active=True)
    except TransportContractor.DoesNotExist as exc:
        raise ValidationError("El contratista no existe o está inactivo.") from exc
    vehicle = TransportVehicle(
        org_id=ctx.org_id,
        contractor=contractor,
        plate=plate.strip().upper(),
        capacity_m3=capacity_m3,
    )
    vehicle.full_clean()
    vehicle.save()
    _audit_catalog(ctx, f"transport-vehicle:{vehicle.pk}", plate=vehicle.plate)
    return vehicle


def _rate_on(
    ctx: RequestContext, route: TransportRoute, material: TransportMaterial, on: date
) -> Decimal | None:
    rate = (
        scoped_qs(ctx, TransportRate)
        .filter(route=route, material=material, valid_from__lte=on)
        .order_by("-valid_from")
        .first()
    )
    return rate.price_per_m3 if rate else None


# ---------------------------------------------------------------------------
# Viajes
# ---------------------------------------------------------------------------


def _can_register_for_third_party(ctx: RequestContext) -> bool:
    access = ctx.access_for(Section.EQUIPMENT)
    return (
        ctx.is_member
        and access is not None
        and role_has_capability(Section.EQUIPMENT, access.role, Capability.CREATE)
    )


def visible_trips(ctx: RequestContext) -> QuerySet[Trip]:
    """Viajes de equipos visibles y de flota de terceros; un operador solo ve los suyos."""

    queryset = scoped_qs(ctx, Trip)
    if not ctx.has_section_access(Section.EQUIPMENT):
        return queryset.none()
    queryset = queryset.filter(Q(equipment__in=visible_equipment(ctx)) | Q(equipment__isnull=True))
    if is_operator(ctx):
        queryset = queryset.filter(created_by_id=ctx.user_id)
    return queryset


def _next_number(ctx: RequestContext, namespace: str, queryset: QuerySet) -> int:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("La numeración requiere una transacción abierta.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"{namespace}:{ctx.org_id}"],
        )
    return (queryset.aggregate(top=Max("number"))["top"] or 0) + 1


def _members(ctx: RequestContext, ids: Iterable[UUID]) -> dict[UUID, object]:
    wanted = set(ids)
    found = {
        row.user_id: row.user
        for row in scoped_qs(ctx, Membership).filter(user_id__in=wanted).select_related("user")
    }
    if set(found) != wanted:
        raise ValidationError("Solicita, despacha y recibe deben ser usuarios de la organización.")
    return found


@transaction.atomic
def register_trip(
    ctx: RequestContext,
    *,
    trip_id: UUID,
    recorded_at: datetime,
    route_id: UUID,
    material_id: UUID,
    volume_m3: Decimal,
    requested_by_id: UUID,
    dispatched_by_id: UUID,
    received_by_id: UUID,
    equipment_id: UUID | None = None,
    vehicle_id: UUID | None = None,
    driver_name: str = "",
    driver_document: str = "",
    notes: str = "",
) -> Trip:
    if (equipment_id is None) == (vehicle_id is None):
        raise ValidationError("Indica el equipo propio o el vehículo de terceros (solo uno).")

    equipment = vehicle = None
    if equipment_id is not None:
        equipment = get_visible_equipment(ctx, equipment_id)
        _require_for_equipment(ctx, Capability.CREATE, equipment)
    elif not _can_register_for_third_party(ctx):
        raise PermissionDenied("No tienes permiso para registrar viajes.")

    existing = scoped_qs(ctx, Trip).filter(pk=trip_id).first()
    if existing is not None:
        if existing.created_by_id != ctx.user_id:
            raise ValidationError("El identificador del viaje ya está en uso.")
        return existing

    if equipment is not None and equipment.status == EquipmentStatus.RETIRED:
        raise ValidationError("El equipo está retirado.")
    if equipment_id is None:
        try:
            vehicle = scoped_qs(ctx, TransportVehicle).get(pk=vehicle_id, active=True)
        except TransportVehicle.DoesNotExist as exc:
            raise ValidationError("El vehículo no existe o está inactivo.") from exc
    try:
        route = scoped_qs(ctx, TransportRoute).get(pk=route_id, active=True)
        material = scoped_qs(ctx, TransportMaterial).get(pk=material_id, active=True)
    except (TransportRoute.DoesNotExist, TransportMaterial.DoesNotExist) as exc:
        raise ValidationError("El tramo o el material no existe o está inactivo.") from exc
    if volume_m3 <= 0:
        raise ValidationError("El volumen debe ser mayor que cero.")
    if vehicle is not None and vehicle.capacity_m3 is not None and volume_m3 > vehicle.capacity_m3:
        raise ValidationError(
            f"El volumen supera la capacidad del vehículo ({vehicle.capacity_m3} m³)."
        )
    if recorded_at > timezone.now() + timedelta(minutes=5):
        raise ValidationError("La fecha del viaje no puede estar en el futuro.")
    if dispatched_by_id == received_by_id:
        raise ValidationError("Quien despacha no puede ser quien recibe.")
    users = _members(ctx, {requested_by_id, dispatched_by_id, received_by_id})

    project_id = None
    if equipment is not None:
        project_id = (
            scoped_qs(ctx, EquipmentProject)
            .filter(equipment=equipment, ended_on__isnull=True)
            .values_list("project_id", flat=True)
            .first()
        )
    price = _rate_on(ctx, route, material, recorded_at.date())
    trip = Trip(
        pk=trip_id,
        org_id=ctx.org_id,
        number=_next_number(ctx, _TRIP_LOCK, scoped_qs(ctx, Trip)),
        recorded_at=recorded_at,
        equipment=equipment,
        vehicle=vehicle,
        project_id=project_id,
        driver_name=driver_name.strip(),
        driver_document=driver_document.strip(),
        route=route,
        distance_km=route.distance_km,
        material=material,
        volume_m3=volume_m3,
        unit_price=price,
        amount=money(volume_m3 * price) if price is not None else None,
        requested_by=users[requested_by_id],
        dispatched_by=users[dispatched_by_id],
        received_by=users[received_by_id],
        notes=notes.strip(),
        created_by_id=ctx.user_id,
    )
    trip.full_clean()
    trip.save(force_insert=True)
    audit.record(
        ctx,
        action=AuditAction.TRANSPORT_TRIP_REGISTERED,
        target=f"trip:{trip.pk}",
        metadata={
            "number": trip.number,
            "route": str(route),
            "material": material.name,
            "volume_m3": str(volume_m3),
        },
    )
    return trip


@transaction.atomic
def recalculate_trip_prices(ctx: RequestContext) -> int:
    """Valora los viajes que quedaron sin tarifa; no toca los ya valorados ni los liquidados."""

    _require_catalog(ctx)
    updated = 0
    pending = scoped_qs(ctx, Trip).filter(unit_price__isnull=True, settlement__isnull=True)
    for trip in pending.select_for_update(of=("self",)).select_related("route", "material"):
        price = _rate_on(ctx, trip.route, trip.material, trip.recorded_at.date())
        if price is None:
            continue
        trip.unit_price = price
        trip.amount = money(trip.volume_m3 * price)
        trip.save(update_fields=["unit_price", "amount"])
        updated += 1
    if updated:
        _audit_catalog(ctx, "transport-trip:prices", updated=str(updated))
    return updated


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TripReport:
    trips: QuerySet[Trip]
    count: int
    total_m3: Decimal
    total_amount: Decimal
    unpriced: int
    #: Volumen por zona de trabajo, mayor primero.
    by_zone: list[tuple[str, Decimal]] = field(default_factory=list)


def trip_report(
    ctx: RequestContext,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    zone: str = "",
    contractor_id: UUID | None = None,
    material_id: UUID | None = None,
    settled: bool | None = None,
) -> TripReport:
    """Viajes con sus filtros (zona, contratista, material, estado de liquidación) y totales."""

    trips = visible_trips(ctx).select_related(
        "route", "material", "equipment", "vehicle__contractor"
    )
    if date_from:
        trips = trips.filter(recorded_at__date__gte=date_from)
    if date_to:
        trips = trips.filter(recorded_at__date__lte=date_to)
    if zone:
        trips = trips.filter(route__zone__iexact=zone)
    if contractor_id:
        trips = trips.filter(vehicle__contractor_id=contractor_id)
    if material_id:
        trips = trips.filter(material_id=material_id)
    if settled is True:
        trips = trips.filter(settlement__isnull=False)
    elif settled is False:
        trips = trips.filter(settlement__isnull=True)
    totals = trips.aggregate(
        n_trips=Count("id"),
        total_m3=Sum("volume_m3"),
        total_amount=Sum("amount"),
        n_unpriced=Count("id", filter=Q(amount__isnull=True)),
    )
    zones = trips.values("route__zone").annotate(m3=Sum("volume_m3")).order_by("-m3", "route__zone")
    return TripReport(
        trips=trips,
        count=totals["n_trips"],
        total_m3=totals["total_m3"] or Decimal("0"),
        total_amount=totals["total_amount"] or Decimal("0"),
        unpriced=totals["n_unpriced"],
        by_zone=[(row["route__zone"] or "Sin zona", row["m3"]) for row in zones],
    )


# ---------------------------------------------------------------------------
# Liquidación de fletes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SettlementLine:
    route: TransportRoute
    material: TransportMaterial
    trips: int
    m3: Decimal
    amount: Decimal


@dataclass(frozen=True)
class VehicleBlock:
    vehicle: TransportVehicle
    m3: Decimal
    amount: Decimal
    lines: list[SettlementLine] = field(default_factory=list)


@dataclass(frozen=True)
class SettlementPreview:
    contractor: TransportContractor
    date_from: date
    date_to: date
    vehicles: list[VehicleBlock]
    total_m3: Decimal
    total_amount: Decimal
    unpriced: int


def _get_contractor(ctx: RequestContext, contractor_id: UUID) -> TransportContractor:
    try:
        return scoped_qs(ctx, TransportContractor).get(pk=contractor_id)
    except TransportContractor.DoesNotExist as exc:
        raise Http404 from exc


def _pending_trips(
    ctx: RequestContext, contractor: TransportContractor, date_from: date, date_to: date
) -> QuerySet[Trip]:
    return scoped_qs(ctx, Trip).filter(
        vehicle__contractor=contractor,
        settlement__isnull=True,
        recorded_at__date__gte=date_from,
        recorded_at__date__lte=date_to,
    )


def settlement_preview(
    ctx: RequestContext, *, contractor_id: UUID, date_from: date, date_to: date
) -> SettlementPreview:
    """Suma los m³ y los tramos recorridos por cada vehículo del contratista en el periodo."""

    _require_costs(ctx)
    if date_to < date_from:
        raise ValidationError("La fecha final no puede ser anterior a la inicial.")
    contractor = _get_contractor(ctx, contractor_id)
    trips = list(
        _pending_trips(ctx, contractor, date_from, date_to).select_related(
            "vehicle", "route", "material"
        )
    )
    grouped: dict[UUID, dict[tuple[UUID, UUID], list[Trip]]] = {}
    vehicles: dict[UUID, TransportVehicle] = {}
    for trip in trips:
        assert trip.vehicle is not None
        vehicles[trip.vehicle.pk] = trip.vehicle
        grouped.setdefault(trip.vehicle.pk, {}).setdefault(
            (trip.route_id, trip.material_id), []
        ).append(trip)
    blocks: list[VehicleBlock] = []
    for vehicle_id in sorted(grouped, key=lambda vid: vehicles[vid].plate):
        lines = []
        for rows in grouped[vehicle_id].values():
            lines.append(
                SettlementLine(
                    route=rows[0].route,
                    material=rows[0].material,
                    trips=len(rows),
                    m3=sum((r.volume_m3 for r in rows), Decimal("0")),
                    amount=sum((r.amount or Decimal("0") for r in rows), Decimal("0")),
                )
            )
        lines.sort(key=lambda line: (str(line.route), line.material.name))
        blocks.append(
            VehicleBlock(
                vehicle=vehicles[vehicle_id],
                m3=sum((line.m3 for line in lines), Decimal("0")),
                amount=sum((line.amount for line in lines), Decimal("0")),
                lines=lines,
            )
        )
    return SettlementPreview(
        contractor=contractor,
        date_from=date_from,
        date_to=date_to,
        vehicles=blocks,
        total_m3=sum((b.m3 for b in blocks), Decimal("0")),
        total_amount=sum((b.amount for b in blocks), Decimal("0")),
        unpriced=sum(1 for trip in trips if trip.amount is None),
    )


@transaction.atomic
def create_settlement(
    ctx: RequestContext, *, contractor_id: UUID, date_from: date, date_to: date
) -> TransportSettlement:
    _require_costs(ctx)
    if date_to < date_from:
        raise ValidationError("La fecha final no puede ser anterior a la inicial.")
    contractor = _get_contractor(ctx, contractor_id)
    settlement = TransportSettlement(
        org_id=ctx.org_id,
        number=_next_number(ctx, _SETTLEMENT_LOCK, scoped_qs(ctx, TransportSettlement)),
        contractor=contractor,
        date_from=date_from,
        date_to=date_to,
        created_by_id=ctx.user_id,
    )
    settlement.full_clean()
    settlement.save(force_insert=True)
    audit.record(
        ctx,
        action=AuditAction.TRANSPORT_SETTLEMENT_CREATED,
        target=f"transport-settlement:{settlement.pk}",
        metadata={"number": settlement.number, "contractor": contractor.name},
    )
    return settlement


@transaction.atomic
def confirm_settlement(
    ctx: RequestContext, settlement_id: UUID, *, advance_applied: Decimal = Decimal("0")
) -> TransportSettlement:
    _require_costs(ctx)
    try:
        settlement = (
            scoped_qs(ctx, TransportSettlement)
            .select_for_update()
            .select_related("contractor")
            .get(pk=settlement_id)
        )
    except TransportSettlement.DoesNotExist as exc:
        raise Http404 from exc
    if settlement.status != LiquidationStatus.DRAFT:
        raise ValidationError("La liquidación ya fue confirmada.")
    trips = list(
        _pending_trips(
            ctx, settlement.contractor, settlement.date_from, settlement.date_to
        ).select_for_update()
    )
    if not trips:
        raise ValidationError("No hay viajes por liquidar en ese periodo.")
    unpriced = [trip for trip in trips if trip.amount is None]
    if unpriced:
        raise ValidationError(
            f"Hay {len(unpriced)} viaje(s) sin tarifa: define la tarifa del tramo y el material."
        )
    total = sum((trip.amount or Decimal("0") for trip in trips), Decimal("0"))
    if advance_applied < 0 or advance_applied > total:
        raise ValidationError("El anticipo debe estar entre cero y el valor total.")
    for trip in trips:
        trip.settlement = settlement
        trip.save(update_fields=["settlement"])
    settlement.total_m3 = sum((trip.volume_m3 for trip in trips), Decimal("0"))
    settlement.total_amount = total
    settlement.advance_applied = advance_applied
    settlement.status = LiquidationStatus.CONFIRMED
    settlement.confirmed_by_id = ctx.user_id
    settlement.confirmed_at = timezone.now()
    settlement.full_clean()
    settlement.save()
    audit.record(
        ctx,
        action=AuditAction.TRANSPORT_SETTLEMENT_CONFIRMED,
        target=f"transport-settlement:{settlement.pk}",
        metadata={
            "number": settlement.number,
            "trips": len(trips),
            "total": str(total),
            "advance": str(advance_applied),
        },
    )
    return settlement
