"""Router de `/v1/basemaps` (Fase B). Solo traducción: contexto, servicio, respuesta."""

from __future__ import annotations

import uuid

from ninja import Router

from visitacampo.basemaps import services
from visitacampo.basemaps.schemas import (
    Basemap,
    BasemapBounds,
    BasemapDownloadResponse,
    BasemapList,
)
from visitacampo.core.context import get_request_context
from visitacampo.core.models import BasemapPackage

__all__ = ["router"]

router = Router(tags=["basemaps"])


def _basemap(package: BasemapPackage) -> Basemap:
    return Basemap(
        id=package.pk,
        name=package.name,
        sha256=package.sha256,
        size=package.size,
        tile_format=package.tile_format,
        min_zoom=package.min_zoom,
        max_zoom=package.max_zoom,
        bounds=BasemapBounds(
            west=package.west, south=package.south, east=package.east, north=package.north
        ),
        created_at=package.created_at,
    )


@router.get(
    "/basemaps",
    response={200: BasemapList},
    summary="Mapas base offline de la organización",
    operation_id="basemapsList",
)
def list_basemaps(request):
    """`GET /v1/basemaps`. Cualquier miembro; solo los de su organización."""
    packages = services.live_basemaps(get_request_context(request))
    return {"items": [_basemap(p) for p in packages]}


@router.get(
    "/basemaps/{basemap_id}/download",
    response={200: BasemapDownloadResponse},
    summary="URL prefirmada de corta duración para descargar un mapa base",
    operation_id="basemapsDownload",
)
def download_basemap(request, basemap_id: uuid.UUID):
    """`GET /v1/basemaps/{id}/download`.

    `404` si no existe, está retirado o es de otra organización (RD-11).
    """
    result = services.download(get_request_context(request), basemap_id)
    return {
        "url": result.url,
        "expires_in": result.expires_in,
        "sha256": result.sha256,
        "size": result.size,
    }
