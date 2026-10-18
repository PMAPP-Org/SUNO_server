"""Esquemas de `/v1/layers` (`components.schemas.Layer*` del contrato)."""

from __future__ import annotations

import uuid
from datetime import datetime

from ninja import Schema

from visitacampo.basemaps.schemas import BasemapBounds

__all__ = ["Layer", "LayerDownloadResponse", "LayerList"]


class Layer(Schema):
    id: uuid.UUID
    name: str
    sha256: str
    size: int
    feature_count: int
    geometry_types: list[str]
    bounds: BasemapBounds
    created_at: datetime


class LayerList(Schema):
    items: list[Layer]


class LayerDownloadResponse(Schema):
    url: str
    expires_in: int
    sha256: str
    size: int
