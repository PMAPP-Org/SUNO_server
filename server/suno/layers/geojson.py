"""Validación de una capa GeoJSON **antes** de ofrecerla (Fase C).

RFC 7946: coordenadas en WGS84 (`[lon, lat]`), que es lo que QGIS exporta por
defecto. Se acepta una `FeatureCollection` de entidades con geometría
`Point`, `MultiPoint`, `LineString`, `MultiLineString`, `Polygon` o
`MultiPolygon`, y se rechaza con un mensaje que el operador entienda:

- JSON inválido, o con `NaN`/`Infinity` (JSON no los admite; Python sí);
- un miembro `crs` que no sea WGS84 (GeoJSON antiguo), o coordenadas fuera de
  rango: casi siempre es una capa exportada en un sistema proyectado
  (MAGNA-SIRGAS, UTM…), y se indica cómo corregirlo;
- geometrías de otro tipo, mal formadas o vacías, y capas sin entidades;
- más entidades o vértices de los que un teléfono dibuja con soltura.

Recorre las coordenadas con una pila explícita: nada recurre sobre datos que
vienen de fuera (mismo criterio que `core/api_errors.py`).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["InvalidLayer", "LayerInfo", "MAX_FEATURES", "MAX_POSITIONS", "inspect"]

#: Topes para que el teléfono la dibuje sin atascarse.
MAX_FEATURES = 50_000
MAX_POSITIONS = 2_000_000

_TYPES = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}
#: Profundidad del arreglo de coordenadas hasta llegar a una posición.
_DEPTH = {
    "Point": 0,
    "MultiPoint": 1,
    "LineString": 1,
    "MultiLineString": 2,
    "Polygon": 2,
    "MultiPolygon": 3,
}
_WGS84_CRS = {
    "urn:ogc:def:crs:OGC:1.3:CRS84",
    "urn:ogc:def:crs:OGC::CRS84",
    "urn:ogc:def:crs:EPSG::4326",
    "EPSG:4326",
}
_PROJECTED_HINT = (
    "parece estar en un sistema proyectado (MAGNA-SIRGAS, UTM…): expórtala desde "
    "QGIS como GeoJSON en EPSG:4326 (WGS 84)."
)


class InvalidLayer(ValueError):
    """La capa no es un GeoJSON utilizable. El mensaje es para el operador."""


@dataclass(frozen=True, slots=True)
class LayerInfo:
    feature_count: int
    geometry_types: tuple[str, ...]
    west: float
    south: float
    east: float
    north: float


def _reject_constant(value: str) -> Any:
    raise InvalidLayer(f"El JSON contiene {value}, que GeoJSON no admite.")


def inspect(path: str | Path) -> LayerInfo:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidLayer(f"No es un JSON válido en UTF-8: {exc}") from exc
    except RecursionError as exc:
        raise InvalidLayer("El JSON está anidado en exceso.") from exc
    return _inspect(data)


def _inspect(data: Any) -> LayerInfo:
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise InvalidLayer("Debe ser una FeatureCollection de GeoJSON.")
    _check_crs(data.get("crs"))
    features = data.get("features")
    if not isinstance(features, list) or not features:
        raise InvalidLayer("No tiene entidades (features).")
    if len(features) > MAX_FEATURES:
        raise InvalidLayer(f"Tiene {len(features)} entidades; el máximo es {MAX_FEATURES}.")

    types: set[str] = set()
    bounds = [math.inf, math.inf, -math.inf, -math.inf]
    positions = 0
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise InvalidLayer(f"La entidad {index} no es un Feature.")
        properties = feature.get("properties")
        if properties is not None and not isinstance(properties, dict):
            raise InvalidLayer(f"La entidad {index} tiene `properties` que no es un objeto.")
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") not in _TYPES:
            raise InvalidLayer(
                f"La entidad {index} no tiene una geometría admitida "
                "(punto, línea o polígono, simples o múltiples)."
            )
        kind = geometry["type"]
        types.add(kind)
        positions += _walk(geometry.get("coordinates"), _DEPTH[kind], bounds, index)
        if positions > MAX_POSITIONS:
            raise InvalidLayer(f"Tiene más de {MAX_POSITIONS} vértices en total.")

    west, south, east, north = bounds
    return LayerInfo(
        feature_count=len(features),
        geometry_types=tuple(sorted(types)),
        west=west,
        south=south,
        east=east,
        north=north,
    )


def _check_crs(crs: Any) -> None:
    if crs is None:
        return
    name = crs.get("properties", {}).get("name") if isinstance(crs, dict) else None
    if name not in _WGS84_CRS:
        raise InvalidLayer(f"Declara el sistema {name!r}: {_PROJECTED_HINT}")


def _walk(coordinates: Any, depth: int, bounds: list[float], index: int) -> int:
    """Comprueba cada posición `[lon, lat]` y amplía [bounds]. Devuelve cuántas hay."""
    stack: list[tuple[Any, int]] = [(coordinates, depth)]
    count = 0
    while stack:
        node, level = stack.pop()
        if not isinstance(node, list) or (level > 0 and not node):
            raise InvalidLayer(f"La entidad {index} tiene coordenadas mal formadas o vacías.")
        if level > 0:
            stack.extend((child, level - 1) for child in node)
            continue
        if len(node) < 2 or not all(
            isinstance(v, int | float) and not isinstance(v, bool) for v in node[:2]
        ):
            raise InvalidLayer(f"La entidad {index} tiene una posición que no es [lon, lat].")
        lon, lat = float(node[0]), float(node[1])
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise InvalidLayer(
                f"La entidad {index} tiene coordenadas fuera de rango: {_PROJECTED_HINT}"
            )
        bounds[0] = min(bounds[0], lon)
        bounds[1] = min(bounds[1], lat)
        bounds[2] = max(bounds[2], lon)
        bounds[3] = max(bounds[3], lat)
        count += 1
    return count
