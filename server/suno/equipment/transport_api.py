"""Router de transporte de materiales para el móvil: catálogo y registro de viajes."""

from __future__ import annotations

import hashlib
import json

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from ninja import Query, Router
from ninja.errors import HttpError

from suno.core.access import Section
from suno.core.context import get_request_context
from suno.core.models import (
    Membership,
    TransportMaterial,
    TransportRoute,
    TransportVehicle,
)
from suno.core.scoping import scoped_qs
from suno.equipment import transport
from suno.equipment.schemas import TransportCatalog, TripIn, TripOut

__all__ = ["router"]

router = Router(tags=["equipment"])


@router.get(
    "/transport/catalog",
    response={200: TransportCatalog},
    summary="Tramos, materiales, vehículos de terceros y usuarios para registrar viajes",
    operation_id="transportCatalog",
)
def transport_catalog(request, version: str | None = Query(None, max_length=64)):
    """`GET /v1/transport/catalog?version=`: la lista, o "sin cambios" si `version` coincide.

    Sin acceso a la sección Equipos el catálogo va vacío. Los equipos propios que pueden
    transportar vienen en `GET /v1/equipment`.
    """
    ctx = get_request_context(request)
    data: dict[str, list[dict[str, object]]] = {
        "routes": [],
        "materials": [],
        "vehicles": [],
        "people": [],
    }
    if ctx.has_section_access(Section.EQUIPMENT):
        data["routes"] = [
            {
                "id": str(row.pk),
                "origin": row.origin,
                "destination": row.destination,
                "zone": row.zone,
                "distance_km": float(row.distance_km),
            }
            for row in scoped_qs(ctx, TransportRoute).filter(active=True)
        ]
        data["materials"] = [
            {"id": str(row.pk), "name": row.name}
            for row in scoped_qs(ctx, TransportMaterial).filter(active=True).order_by("name")
        ]
        data["vehicles"] = [
            {
                "id": str(row.pk),
                "plate": row.plate,
                "contractor": row.contractor.name,
                "capacity_m3": None if row.capacity_m3 is None else float(row.capacity_m3),
            }
            for row in scoped_qs(ctx, TransportVehicle)
            .filter(active=True)
            .select_related("contractor")
            .order_by("contractor__name", "plate")
        ]
        data["people"] = [
            {"id": str(row.user_id), "name": row.user.display_name or row.user.username}
            for row in scoped_qs(ctx, Membership).select_related("user").order_by("user__username")
        ]
    current = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if version == current:
        return {"version": current, "changed": False, **{key: [] for key in data}}
    return {"version": current, "changed": True, **data}


@router.post(
    "/transport/trips",
    response={200: TripOut},
    summary="Registra un viaje de material (idempotente por `id`)",
    operation_id="transportTripCreate",
)
def create_trip(request, payload: TripIn):
    """`POST /v1/transport/trips`.

    Un viaje lleva exactamente un vehículo (`equipment_id` propio o `vehicle_id` de terceros) y
    tres usuarios distintos en lo que importa: quien despacha no es quien recibe. `404` si el
    equipo no es visible; `403` sin permiso; `422` ante cualquier otra violación.
    """
    ctx = get_request_context(request)
    try:
        trip = transport.register_trip(
            ctx,
            trip_id=payload.id,
            recorded_at=payload.recorded_at,
            route_id=payload.route_id,
            material_id=payload.material_id,
            volume_m3=payload.volume_m3,
            requested_by_id=payload.requested_by_id,
            dispatched_by_id=payload.dispatched_by_id,
            received_by_id=payload.received_by_id,
            equipment_id=payload.equipment_id,
            vehicle_id=payload.vehicle_id,
            driver_name=payload.driver_name,
            driver_document=payload.driver_document,
            notes=payload.notes,
        )
    except Http404 as exc:
        raise HttpError(404, "Equipment not found.") from exc
    except PermissionDenied as exc:
        raise HttpError(403, "Not allowed to register trips.") from exc
    except DjangoValidationError as exc:
        raise HttpError(422, "; ".join(exc.messages)) from exc
    return {"id": trip.pk, "code": trip.code, "number": trip.number}
