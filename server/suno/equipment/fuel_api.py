"""Router de combustibles para el móvil: catálogo y registro de tanqueos. Solo traducción."""

from __future__ import annotations

import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from ninja import Query, Router
from ninja.errors import HttpError

from suno.core.access import Section
from suno.core.context import get_request_context
from suno.core.models import FuelStation, FuelStore
from suno.core.scoping import scoped_qs
from suno.equipment import fuel
from suno.equipment.schemas import FuelCatalog, FuelFillIn, FuelFillOut

__all__ = ["router"]

router = Router(tags=["equipment"])


@router.get(
    "/fuel/catalog",
    response={200: FuelCatalog},
    summary="Estaciones de servicio y tanques propios para registrar tanqueos",
    operation_id="fuelCatalog",
)
def fuel_catalog(request, version: str | None = Query(None, max_length=64)):
    """`GET /v1/fuel/catalog?version=`: la lista, o "sin cambios" si `version` coincide.

    Sin acceso a la sección Equipos el catálogo va vacío.
    """
    ctx = get_request_context(request)
    stations: list[dict[str, str]] = []
    stores: list[dict[str, str]] = []
    if ctx.has_section_access(Section.EQUIPMENT):
        stations = [
            {"id": str(row.pk), "name": row.name, "supplier": row.supplier.name}
            for row in scoped_qs(ctx, FuelStation)
            .filter(active=True)
            .select_related("supplier")
            .order_by("supplier__name", "name")
        ]
        stores = [
            {"id": str(row.pk), "name": row.name}
            for row in scoped_qs(ctx, FuelStore).filter(active=True).order_by("name")
        ]
    current = hashlib.sha256(
        json.dumps([stations, stores], sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if version == current:
        return {"version": current, "changed": False, "stations": [], "stores": []}
    return {"version": current, "changed": True, "stations": stations, "stores": stores}


@router.post(
    "/equipment/{equipment_id}/fuel-fills",
    response={200: FuelFillOut},
    summary="Registra un tanqueo (idempotente por `id`)",
    operation_id="equipmentFuelFillCreate",
)
def create_fuel_fill(request, equipment_id: uuid.UUID, payload: FuelFillIn):
    """`POST /v1/equipment/{id}/fuel-fills`.

    `404` si el equipo no es visible; `403` sin permiso; `422` si el vale ya existe, falta la
    lectura del medidor (o retrocede), la fecha es futura o el tanque no tiene stock.
    """
    ctx = get_request_context(request)
    try:
        fill = fuel.register_fill(
            ctx,
            fill_id=payload.id,
            equipment_id=equipment_id,
            source=payload.source,
            station_id=payload.station_id,
            store_id=payload.store_id,
            fuel_type=payload.fuel_type,
            voucher_number=payload.voucher_number,
            filled_at=payload.filled_at,
            gallons=payload.gallons,
            meter_unit=payload.meter_unit or "",
            meter_value=payload.meter_value,
            work_front=payload.work_front,
            driver_name=payload.driver_name,
            driver_document=payload.driver_document,
            notes=payload.notes,
        )
    except Http404 as exc:
        raise HttpError(404, "Equipment not found.") from exc
    except PermissionDenied as exc:
        raise HttpError(403, "Not allowed to register fuel for this equipment.") from exc
    except DjangoValidationError as exc:
        raise HttpError(422, "; ".join(exc.messages)) from exc
    return {"id": fill.pk, "voucher_number": fill.voucher_number}
