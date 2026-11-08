"""Combustibles (M&E fase 4): tanqueos, tanque propio, rendimiento y liquidación a terceros.

Reglas que este módulo garantiza:
- Un tanqueo es idempotente por `id` (el móvil reintenta) y su vale no se repite en la
  misma estación o tanque.
- La lectura del medidor del tanqueo alimenta las lecturas del equipo y no puede retroceder.
- La salida de un tanque propio descuenta stock y se valora al costo promedio ponderado de
  las compras; un tanqueo en estación se valora con la tarifa vigente de esa estación.
- El rendimiento real compara el avance del medidor con los galones del tanqueo.
- Una liquidación solo se confirma con todos sus tanqueos valorados; al confirmar los
  tanqueos quedan ligados y no vuelven a entrar en otra liquidación.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import F, Max, QuerySet, Sum
from django.http import Http404
from django.utils import timezone

from suno.budgets.totals import money
from suno.core import audit
from suno.core.access import Capability, Section
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    Equipment,
    EquipmentProject,
    EquipmentStatus,
    FuelFill,
    FuelLiquidation,
    FuelPurchase,
    FuelSource,
    FuelStation,
    FuelStationRate,
    FuelStore,
    FuelSupplier,
    LiquidationStatus,
)
from suno.core.scoping import scoped_qs
from suno.equipment.datasheet import record_meter_reading
from suno.equipment.inspections import is_operator
from suno.equipment.services import (
    _require_for_equipment,
    can_view_costs,
    get_visible_equipment,
    visible_equipment,
)

__all__ = [
    "LiquidationLine",
    "LiquidationPreview",
    "MonthlyYield",
    "StationBlock",
    "StoreStock",
    "confirm_liquidation",
    "create_liquidation",
    "create_station",
    "create_store",
    "create_supplier",
    "liquidation_preview",
    "record_purchase",
    "recalculate_fill_prices",
    "register_fill",
    "set_station_rate",
    "store_stock",
    "visible_fills",
    "yield_report",
]

_LOCK_NAMESPACE = "fuel-liquidation-number"


# ---------------------------------------------------------------------------
# Catálogo: proveedores, estaciones, tarifas, tanques y compras
# ---------------------------------------------------------------------------


def _require_catalog(ctx: RequestContext) -> None:
    if not ctx.has_capability(Section.EQUIPMENT, Capability.CREATE) or is_operator(ctx):
        raise PermissionDenied("No tienes permiso para administrar combustibles.")


@transaction.atomic
def create_supplier(ctx: RequestContext, *, name: str, tax_id: str = "") -> FuelSupplier:
    _require_catalog(ctx)
    supplier = FuelSupplier(org_id=ctx.org_id, name=name.strip(), tax_id=tax_id.strip())
    supplier.full_clean()
    supplier.save()
    audit.record(
        ctx,
        action=AuditAction.FUEL_CATALOG_CHANGED,
        target=f"fuel-supplier:{supplier.pk}",
        metadata={"supplier": supplier.name},
    )
    return supplier


@transaction.atomic
def create_station(ctx: RequestContext, *, supplier_id: UUID, name: str) -> FuelStation:
    _require_catalog(ctx)
    try:
        supplier = scoped_qs(ctx, FuelSupplier).get(pk=supplier_id, active=True)
    except FuelSupplier.DoesNotExist as exc:
        raise ValidationError("El proveedor no existe o está inactivo.") from exc
    station = FuelStation(org_id=ctx.org_id, supplier=supplier, name=name.strip())
    station.full_clean()
    station.save()
    audit.record(
        ctx,
        action=AuditAction.FUEL_CATALOG_CHANGED,
        target=f"fuel-station:{station.pk}",
        metadata={"station": station.name, "supplier": supplier.name},
    )
    return station


@transaction.atomic
def set_station_rate(
    ctx: RequestContext, *, station_id: UUID, valid_from: date, price_per_gallon: Decimal
) -> FuelStationRate:
    """Registra la tarifa por galón desde una fecha. No edita las anteriores."""

    _require_catalog(ctx)
    try:
        station = scoped_qs(ctx, FuelStation).get(pk=station_id)
    except FuelStation.DoesNotExist as exc:
        raise Http404 from exc
    if scoped_qs(ctx, FuelStationRate).filter(station=station, valid_from=valid_from).exists():
        raise ValidationError("Ya existe una tarifa con esa fecha; usa una posterior.")
    rate = FuelStationRate(
        org_id=ctx.org_id,
        station=station,
        valid_from=valid_from,
        price_per_gallon=price_per_gallon,
        created_by_id=ctx.user_id,
    )
    rate.full_clean()
    rate.save()
    audit.record(
        ctx,
        action=AuditAction.FUEL_CATALOG_CHANGED,
        target=f"fuel-rate:{rate.pk}",
        metadata={
            "station": station.name,
            "valid_from": valid_from.isoformat(),
            "price": str(price_per_gallon),
        },
    )
    return rate


@transaction.atomic
def create_store(ctx: RequestContext, *, name: str, project_id: UUID | None = None) -> FuelStore:
    _require_catalog(ctx)
    store = FuelStore(org_id=ctx.org_id, name=name.strip(), project_id=project_id)
    store.full_clean()
    store.save()
    audit.record(
        ctx,
        action=AuditAction.FUEL_CATALOG_CHANGED,
        target=f"fuel-store:{store.pk}",
        metadata={"store": store.name},
    )
    return store


@dataclass(frozen=True)
class StoreStock:
    gallons: Decimal
    #: Costo promedio ponderado por galón de las compras; `None` sin compras.
    average_cost: Decimal | None


def store_stock(ctx: RequestContext, store: FuelStore) -> StoreStock:
    purchases = scoped_qs(ctx, FuelPurchase).filter(store=store)
    bought = purchases.aggregate(
        total_gallons=Sum("gallons"), total_value=Sum(F("gallons") * F("unit_cost"))
    )
    gallons_in = bought["total_gallons"] or Decimal("0")
    issued = scoped_qs(ctx, FuelFill).filter(store=store).aggregate(total=Sum("gallons"))["total"]
    average = (bought["total_value"] / gallons_in).quantize(Decimal("0.01")) if gallons_in else None
    return StoreStock(gallons=gallons_in - (issued or Decimal("0")), average_cost=average)


@transaction.atomic
def record_purchase(
    ctx: RequestContext,
    *,
    store_id: UUID,
    bought_on: date,
    gallons: Decimal,
    unit_cost: Decimal,
    supplier_name: str = "",
    notes: str = "",
) -> FuelPurchase:
    _require_catalog(ctx)
    try:
        store = scoped_qs(ctx, FuelStore).get(pk=store_id, active=True)
    except FuelStore.DoesNotExist as exc:
        raise ValidationError("El tanque no existe o está inactivo.") from exc
    purchase = FuelPurchase(
        org_id=ctx.org_id,
        store=store,
        bought_on=bought_on,
        gallons=gallons,
        unit_cost=unit_cost,
        supplier_name=supplier_name.strip(),
        notes=notes.strip(),
        created_by_id=ctx.user_id,
    )
    purchase.full_clean()
    purchase.save()
    audit.record(
        ctx,
        action=AuditAction.FUEL_CATALOG_CHANGED,
        target=f"fuel-purchase:{purchase.pk}",
        metadata={"store": store.name, "gallons": str(gallons), "unit_cost": str(unit_cost)},
    )
    return purchase


# ---------------------------------------------------------------------------
# Tanqueos
# ---------------------------------------------------------------------------


def visible_fills(ctx: RequestContext) -> QuerySet[FuelFill]:
    queryset = scoped_qs(ctx, FuelFill).filter(equipment__in=visible_equipment(ctx))
    if is_operator(ctx):
        queryset = queryset.filter(created_by_id=ctx.user_id)
    return queryset


def _rate_on(ctx: RequestContext, station: FuelStation, on: date) -> Decimal | None:
    rate = (
        scoped_qs(ctx, FuelStationRate)
        .filter(station=station, valid_from__lte=on)
        .order_by("-valid_from")
        .first()
    )
    return rate.price_per_gallon if rate else None


@transaction.atomic
def register_fill(
    ctx: RequestContext,
    *,
    fill_id: UUID,
    equipment_id: UUID,
    source: str,
    voucher_number: str,
    filled_at: datetime,
    gallons: Decimal,
    station_id: UUID | None = None,
    store_id: UUID | None = None,
    fuel_type: str = "acpm",
    meter_unit: str = "",
    meter_value: Decimal | None = None,
    work_front: str = "",
    driver_name: str = "",
    driver_document: str = "",
    notes: str = "",
) -> FuelFill:
    equipment = get_visible_equipment(ctx, equipment_id)
    _require_for_equipment(ctx, Capability.CREATE, equipment)

    existing = scoped_qs(ctx, FuelFill).filter(pk=fill_id).first()
    if existing is not None:
        if existing.equipment_id != equipment.pk or existing.created_by_id != ctx.user_id:
            raise ValidationError("El identificador del tanqueo ya está en uso.")
        return existing

    if equipment.status == EquipmentStatus.RETIRED:
        raise ValidationError("El equipo está retirado.")
    if source not in FuelSource.values:
        raise ValidationError("Origen de combustible desconocido.")
    if (source == FuelSource.STATION) != (station_id is not None) or (
        source == FuelSource.STORE
    ) != (store_id is not None):
        raise ValidationError("Indica la estación (o el tanque propio) del tanqueo.")
    voucher = voucher_number.strip()
    if not voucher:
        raise ValidationError("El número de vale es obligatorio.")
    if gallons <= 0:
        raise ValidationError("Los galones deben ser mayores que cero.")
    if filled_at > timezone.now() + timedelta(minutes=5):
        raise ValidationError("La fecha del tanqueo no puede estar en el futuro.")

    station = store = None
    price: Decimal | None = None
    if source == FuelSource.STATION:
        try:
            station = scoped_qs(ctx, FuelStation).get(pk=station_id, active=True)
        except FuelStation.DoesNotExist as exc:
            raise ValidationError("La estación no existe o está inactiva.") from exc
        if scoped_qs(ctx, FuelFill).filter(station=station, voucher_number=voucher).exists():
            raise ValidationError("Ese vale ya fue registrado en la estación.")
        price = _rate_on(ctx, station, filled_at.date())
    else:
        try:
            store = scoped_qs(ctx, FuelStore).select_for_update().get(pk=store_id, active=True)
        except FuelStore.DoesNotExist as exc:
            raise ValidationError("El tanque no existe o está inactivo.") from exc
        if scoped_qs(ctx, FuelFill).filter(store=store, voucher_number=voucher).exists():
            raise ValidationError("Ese vale ya fue registrado en el tanque.")
        stock = store_stock(ctx, store)
        if gallons > stock.gallons:
            raise ValidationError(
                f"Stock insuficiente: el tanque tiene {stock.gallons} galones disponibles."
            )
        price = stock.average_cost

    unit = ""
    value: Decimal | None = None
    if equipment.control_scheme == "meter" and equipment.primary_meter_unit:
        if not meter_unit or meter_value is None:
            raise ValidationError("La lectura del medidor es obligatoria.")
        record_meter_reading(
            ctx,
            equipment_id=equipment.pk,
            meter_unit=meter_unit,
            value=meter_value,
            read_at=filled_at,
            source="fuel",
            notes=f"Vale {voucher}",
        )
        unit, value = meter_unit, meter_value

    project_id = (
        scoped_qs(ctx, EquipmentProject)
        .filter(equipment=equipment, ended_on__isnull=True)
        .values_list("project_id", flat=True)
        .first()
    )
    fill = FuelFill(
        pk=fill_id,
        org_id=ctx.org_id,
        equipment=equipment,
        project_id=project_id,
        source=source,
        station=station,
        store=store,
        fuel_type=fuel_type,
        voucher_number=voucher,
        filled_at=filled_at,
        gallons=gallons,
        meter_unit=unit,
        meter_value=value,
        work_front=work_front.strip(),
        driver_name=driver_name.strip(),
        driver_document=driver_document.strip(),
        notes=notes.strip(),
        unit_price=price,
        cost=money(gallons * price) if price is not None else None,
        created_by_id=ctx.user_id,
    )
    fill.full_clean()
    fill.save(force_insert=True)
    audit.record(
        ctx,
        action=AuditAction.FUEL_FILL_REGISTERED,
        target=f"fuel-fill:{fill.pk}",
        metadata={
            "equipment": str(equipment.pk),
            "source": source,
            "gallons": str(gallons),
            "voucher": voucher,
        },
    )
    return fill


@transaction.atomic
def recalculate_fill_prices(ctx: RequestContext, *, station_id: UUID | None = None) -> int:
    """Valora los tanqueos de estación que quedaron sin tarifa; no toca los ya valorados."""

    _require_catalog(ctx)
    pending = scoped_qs(ctx, FuelFill).filter(
        source=FuelSource.STATION, unit_price__isnull=True, liquidation__isnull=True
    )
    if station_id is not None:
        pending = pending.filter(station_id=station_id)
    updated = 0
    for fill in pending.select_for_update(of=("self",)).select_related("station"):
        assert fill.station is not None
        price = _rate_on(ctx, fill.station, fill.filled_at.date())
        if price is None:
            continue
        fill.unit_price = price
        fill.cost = money(fill.gallons * price)
        fill.save(update_fields=["unit_price", "cost"])
        updated += 1
    if updated:
        audit.record(
            ctx,
            action=AuditAction.FUEL_CATALOG_CHANGED,
            target="fuel-fill:prices",
            metadata={"updated": updated},
        )
    return updated


# ---------------------------------------------------------------------------
# Rendimiento
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonthlyYield:
    month: date
    gallons: Decimal
    distance: Decimal
    #: Avance del medidor por galón; `None` sin datos suficientes en el mes.
    real: Decimal | None
    nominal: Decimal | None
    #: (real − nominal) / nominal en %; negativo = gasta más de lo previsto.
    deviation_pct: Decimal | None
    #: `True` si el rendimiento real cae por debajo de lo permitido por la tolerancia.
    out_of_tolerance: bool


def yield_report(
    ctx: RequestContext, equipment: Equipment, *, since: date, until: date
) -> list[MonthlyYield]:
    """Rendimiento real por mes frente al nominal de la ficha.

    Rendimiento real del tanqueo = (lectura − lectura anterior) / galones del tanqueo, con
    el medidor principal. La ventana se parte en meses para ver la deriva mes a mes.
    """

    unit = equipment.primary_meter_unit
    if not unit:
        return []
    fills = list(
        visible_fills(ctx)
        .filter(equipment=equipment, meter_unit=unit, meter_value__isnull=False)
        .order_by("filled_at")
    )
    nominal = equipment.nominal_fuel_yield
    tolerance = equipment.fuel_tolerance_pct
    buckets: dict[date, list[Decimal]] = {}
    previous: FuelFill | None = None
    for fill in fills:
        if previous is not None and since <= fill.filled_at.date() <= until:
            assert fill.meter_value is not None and previous.meter_value is not None
            delta = fill.meter_value - previous.meter_value
            if delta >= 0:
                bucket = buckets.setdefault(
                    fill.filled_at.date().replace(day=1), [Decimal("0")] * 2
                )
                bucket[0] += fill.gallons
                bucket[1] += delta
        previous = fill
    rows: list[MonthlyYield] = []
    for month in sorted(buckets):
        gallons, distance = buckets[month]
        real = (distance / gallons).quantize(Decimal("0.01")) if gallons else None
        deviation = None
        if real is not None and nominal:
            deviation = ((real - nominal) / nominal * Decimal(100)).quantize(Decimal("0.1"))
        flag = bool(
            real is not None
            and nominal
            and tolerance is not None
            and real < nominal * (Decimal(1) - tolerance / Decimal(100))
        )
        rows.append(MonthlyYield(month, gallons, distance, real, nominal, deviation, flag))
    return rows


# ---------------------------------------------------------------------------
# Liquidación a terceros
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiquidationLine:
    equipment: Equipment
    fills: int
    gallons: Decimal
    amount: Decimal


@dataclass(frozen=True)
class StationBlock:
    station: FuelStation
    gallons: Decimal
    amount: Decimal
    lines: list[LiquidationLine] = field(default_factory=list)


@dataclass(frozen=True)
class LiquidationPreview:
    supplier: FuelSupplier
    date_from: date
    date_to: date
    stations: list[StationBlock]
    total_gallons: Decimal
    total_amount: Decimal
    #: Tanqueos del periodo sin tarifa: impiden confirmar.
    unpriced: int


def _pending_fills(
    ctx: RequestContext, supplier: FuelSupplier, date_from: date, date_to: date
) -> QuerySet[FuelFill]:
    return scoped_qs(ctx, FuelFill).filter(
        source=FuelSource.STATION,
        station__supplier=supplier,
        liquidation__isnull=True,
        filled_at__date__gte=date_from,
        filled_at__date__lte=date_to,
    )


def _get_supplier(ctx: RequestContext, supplier_id: UUID) -> FuelSupplier:
    try:
        return scoped_qs(ctx, FuelSupplier).get(pk=supplier_id)
    except FuelSupplier.DoesNotExist as exc:
        raise Http404 from exc


def _require_costs(ctx: RequestContext) -> None:
    if not can_view_costs(ctx) or is_operator(ctx):
        raise PermissionDenied("No tienes permiso para liquidar combustibles.")


def liquidation_preview(
    ctx: RequestContext, *, supplier_id: UUID, date_from: date, date_to: date
) -> LiquidationPreview:
    """Agrupa los tanqueos sin liquidar del proveedor: solo estaciones con actividad."""

    _require_costs(ctx)
    if date_to < date_from:
        raise ValidationError("La fecha final no puede ser anterior a la inicial.")
    supplier = _get_supplier(ctx, supplier_id)
    fills = list(
        _pending_fills(ctx, supplier, date_from, date_to).select_related("station", "equipment")
    )
    by_station: dict[UUID, dict[UUID, list[FuelFill]]] = {}
    stations: dict[UUID, FuelStation] = {}
    for fill in fills:
        assert fill.station is not None
        stations[fill.station_id or fill.station.pk] = fill.station
        by_station.setdefault(fill.station.pk, {}).setdefault(fill.equipment_id, []).append(fill)
    blocks: list[StationBlock] = []
    for station_id in sorted(by_station, key=lambda sid: stations[sid].name):
        lines = []
        for rows in by_station[station_id].values():
            lines.append(
                LiquidationLine(
                    equipment=rows[0].equipment,
                    fills=len(rows),
                    gallons=sum((r.gallons for r in rows), Decimal("0")),
                    amount=sum((r.cost or Decimal("0") for r in rows), Decimal("0")),
                )
            )
        lines.sort(key=lambda line: line.equipment.code)
        blocks.append(
            StationBlock(
                station=stations[station_id],
                gallons=sum((line.gallons for line in lines), Decimal("0")),
                amount=sum((line.amount for line in lines), Decimal("0")),
                lines=lines,
            )
        )
    return LiquidationPreview(
        supplier=supplier,
        date_from=date_from,
        date_to=date_to,
        stations=blocks,
        total_gallons=sum((b.gallons for b in blocks), Decimal("0")),
        total_amount=sum((b.amount for b in blocks), Decimal("0")),
        unpriced=sum(1 for fill in fills if fill.cost is None),
    )


def _next_number(ctx: RequestContext) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"{_LOCK_NAMESPACE}:{ctx.org_id}"],
        )
    top = scoped_qs(ctx, FuelLiquidation).aggregate(top=Max("number"))["top"]
    return (top or 0) + 1


@transaction.atomic
def create_liquidation(
    ctx: RequestContext, *, supplier_id: UUID, date_from: date, date_to: date
) -> FuelLiquidation:
    _require_costs(ctx)
    if date_to < date_from:
        raise ValidationError("La fecha final no puede ser anterior a la inicial.")
    supplier = _get_supplier(ctx, supplier_id)
    liquidation = FuelLiquidation(
        org_id=ctx.org_id,
        number=_next_number(ctx),
        supplier=supplier,
        date_from=date_from,
        date_to=date_to,
        created_by_id=ctx.user_id,
    )
    liquidation.full_clean()
    liquidation.save(force_insert=True)
    audit.record(
        ctx,
        action=AuditAction.FUEL_LIQUIDATION_CREATED,
        target=f"fuel-liquidation:{liquidation.pk}",
        metadata={"number": liquidation.number, "supplier": supplier.name},
    )
    return liquidation


@transaction.atomic
def confirm_liquidation(
    ctx: RequestContext, liquidation_id: UUID, *, advance_applied: Decimal = Decimal("0")
) -> FuelLiquidation:
    """Confirma: liga los tanqueos del periodo, fija totales y amortiza el anticipo."""

    _require_costs(ctx)
    try:
        liquidation = (
            scoped_qs(ctx, FuelLiquidation)
            .select_for_update()
            .select_related("supplier")
            .get(pk=liquidation_id)
        )
    except FuelLiquidation.DoesNotExist as exc:
        raise Http404 from exc
    if liquidation.status != LiquidationStatus.DRAFT:
        raise ValidationError("La liquidación ya fue confirmada.")
    fills = list(
        _pending_fills(
            ctx, liquidation.supplier, liquidation.date_from, liquidation.date_to
        ).select_for_update()
    )
    if not fills:
        raise ValidationError("No hay tanqueos por liquidar en ese periodo.")
    unpriced = [fill for fill in fills if fill.cost is None]
    if unpriced:
        raise ValidationError(
            f"Hay {len(unpriced)} tanqueo(s) sin tarifa: define la tarifa de la estación."
        )
    total_amount = sum((fill.cost or Decimal("0") for fill in fills), Decimal("0"))
    if advance_applied < 0 or advance_applied > total_amount:
        raise ValidationError("El anticipo debe estar entre cero y el valor total.")
    for fill in fills:
        fill.liquidation = liquidation
        fill.save(update_fields=["liquidation"])
    liquidation.total_gallons = sum((fill.gallons for fill in fills), Decimal("0"))
    liquidation.total_amount = total_amount
    liquidation.advance_applied = advance_applied
    liquidation.status = LiquidationStatus.CONFIRMED
    liquidation.confirmed_by_id = ctx.user_id
    liquidation.confirmed_at = timezone.now()
    liquidation.full_clean()
    liquidation.save()
    audit.record(
        ctx,
        action=AuditAction.FUEL_LIQUIDATION_CONFIRMED,
        target=f"fuel-liquidation:{liquidation.pk}",
        metadata={
            "number": liquidation.number,
            "fills": len(fills),
            "total": str(total_amount),
            "advance": str(advance_applied),
        },
    )
    return liquidation
