"""Router de `/v1/layers` (Fase C). Solo traducción: contexto, servicio, respuesta."""

from __future__ import annotations

import uuid

from ninja import Router

from visitacampo.basemaps.schemas import BasemapBounds
from visitacampo.core.context import get_request_context
from visitacampo.core.models import ReferenceLayer
from visitacampo.layers import services
from visitacampo.layers.schemas import Layer, LayerDownloadResponse, LayerList

__all__ = ["router"]

router = Router(tags=["layers"])


def _layer(layer: ReferenceLayer) -> Layer:
    return Layer(
        id=layer.pk,
        name=layer.name,
        sha256=layer.sha256,
        size=layer.size,
        feature_count=layer.feature_count,
        geometry_types=list(layer.geometry_types),
        bounds=BasemapBounds(
            west=layer.west, south=layer.south, east=layer.east, north=layer.north
        ),
        created_at=layer.created_at,
    )


@router.get(
    "/layers",
    response={200: LayerList},
    summary="Capas de referencia de la organización",
    operation_id="layersList",
)
def list_layers(request):
    """`GET /v1/layers`. Cualquier miembro; solo las de su organización."""
    return {
        "items": [_layer(layer) for layer in services.live_layers(get_request_context(request))]
    }


@router.get(
    "/layers/{layer_id}/download",
    response={200: LayerDownloadResponse},
    summary="URL prefirmada de corta duración para descargar una capa",
    operation_id="layersDownload",
)
def download_layer(request, layer_id: uuid.UUID):
    """`GET /v1/layers/{id}/download`.

    `404` si no existe, está retirada o es de otra organización (RD-11).
    """
    result = services.download(get_request_context(request), layer_id)
    return {
        "url": result.url,
        "expires_in": result.expires_in,
        "sha256": result.sha256,
        "size": result.size,
    }
