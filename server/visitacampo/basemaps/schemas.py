"""Esquemas de `/v1/basemaps` (`components.schemas.Basemap*` del contrato)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from ninja import Schema

__all__ = ["Basemap", "BasemapBounds", "BasemapDownloadResponse", "BasemapList"]

#: Mismos valores que `core.models.TileFormat`; un test compara los dos.
TileFormatValue = Literal["png", "jpg", "webp"]


class BasemapBounds(Schema):
    west: float
    south: float
    east: float
    north: float


class Basemap(Schema):
    id: uuid.UUID
    name: str
    sha256: str
    size: int
    tile_format: TileFormatValue
    min_zoom: int
    max_zoom: int
    bounds: BasemapBounds
    created_at: datetime


class BasemapList(Schema):
    items: list[Basemap]


class BasemapDownloadResponse(Schema):
    url: str
    expires_in: int
    sha256: str
    size: int
