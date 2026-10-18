"""Lectura y validación de un paquete MBTiles **antes** de ofrecerlo (Fase B).

MBTiles 1.3: una base SQLite con una tabla `metadata (name, value)` y una
tabla (o vista) `tiles (zoom_level, tile_column, tile_row, tile_data)`. Aquí
se comprueba lo que el teléfono necesita para mostrarlo bien, y se rechaza lo
demás con un mensaje que el operador entienda:

- es SQLite de verdad (cabecera) y tiene esas dos tablas;
- es **ráster** (PNG, JPEG o WebP): la app no dibuja teselas vectoriales;
- tiene al menos una tesela, y su contenido es del formato declarado;
- niveles de zoom y extensión (`bounds`) válidos; si faltan en `metadata`,
  se calculan de las teselas.

La base se abre **solo lectura e inmutable**: validar no escribe en el archivo
ni crea un journal a su lado.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

__all__ = ["InvalidMbtiles", "MbtilesInfo", "inspect"]

_SQLITE_MAGIC = b"SQLite format 3\x00"
_FORMATS = {"png": "png", "jpg": "jpg", "jpeg": "jpg", "webp": "webp"}
_MAX_ZOOM = 24


class InvalidMbtiles(ValueError):
    """El archivo no es un MBTiles ráster utilizable. El mensaje es para el operador."""


@dataclass(frozen=True, slots=True)
class MbtilesInfo:
    tile_format: str
    min_zoom: int
    max_zoom: int
    west: float
    south: float
    east: float
    north: float
    name: str | None


def inspect(path: str | Path) -> MbtilesInfo:
    source = Path(path)
    with source.open("rb") as handle:
        if handle.read(len(_SQLITE_MAGIC)) != _SQLITE_MAGIC:
            raise InvalidMbtiles("No es una base SQLite: un MBTiles es un archivo .mbtiles.")
    uri = f"{source.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise InvalidMbtiles(f"No se pudo abrir: {exc}") from exc
    try:
        return _inspect(connection)
    except sqlite3.Error as exc:
        raise InvalidMbtiles(f"No tiene la estructura de un MBTiles: {exc}") from exc
    finally:
        connection.close()


def _inspect(connection: sqlite3.Connection) -> MbtilesInfo:
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }
    if not {"metadata", "tiles"} <= names:
        raise InvalidMbtiles("Faltan las tablas `metadata` y `tiles` de un MBTiles.")
    metadata = {
        str(name): str(value)
        for name, value in connection.execute("SELECT name, value FROM metadata")
    }

    declared = metadata.get("format", "").strip().lower()
    if declared == "pbf":
        raise InvalidMbtiles(
            "Es un MBTiles vectorial (pbf): la app solo muestra teselas ráster (PNG, JPEG o WebP)."
        )
    tile_format = _FORMATS.get(declared)

    sample = connection.execute("SELECT tile_data FROM tiles LIMIT 1").fetchone()
    if sample is None or not sample[0]:
        raise InvalidMbtiles("No tiene teselas.")
    sniffed = _sniff(bytes(sample[0]))
    if sniffed is None:
        raise InvalidMbtiles("Las teselas no son PNG, JPEG ni WebP.")
    if tile_format is not None and tile_format != sniffed:
        raise InvalidMbtiles(f"`metadata.format` dice {declared}, pero las teselas son {sniffed}.")

    stored_min, stored_max = connection.execute(
        "SELECT MIN(zoom_level), MAX(zoom_level) FROM tiles"
    ).fetchone()
    min_zoom = _int(metadata.get("minzoom"), default=stored_min)
    max_zoom = _int(metadata.get("maxzoom"), default=stored_max)
    if not (0 <= min_zoom <= max_zoom <= _MAX_ZOOM):
        raise InvalidMbtiles(f"Niveles de zoom inválidos: {min_zoom}–{max_zoom}.")

    bounds = _bounds(metadata.get("bounds")) or _bounds_from_tiles(connection, stored_min)
    west, south, east, north = bounds
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise InvalidMbtiles(f"Extensión inválida: {bounds}.")

    return MbtilesInfo(
        tile_format=sniffed,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        west=west,
        south=south,
        east=east,
        north=north,
        name=metadata.get("name"),
    )


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _int(value: str | None, *, default: int) -> int:
    try:
        return int(value) if value is not None else int(default)
    except (TypeError, ValueError) as exc:
        raise InvalidMbtiles(f"Nivel de zoom no numérico: {value!r}.") from exc


def _bounds(value: str | None) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    try:
        west, south, east, north = (float(part) for part in value.split(","))
    except ValueError as exc:
        raise InvalidMbtiles(f"`metadata.bounds` no es «oeste,sur,este,norte»: {value!r}.") from exc
    return west, south, east, north


def _bounds_from_tiles(
    connection: sqlite3.Connection, zoom: int
) -> tuple[float, float, float, float]:
    """Extensión de las teselas del nivel [zoom]. MBTiles usa filas TMS: la fila
    0 es la del **sur**, al revés que el esquema XYZ de los mapas web."""
    min_x, max_x, min_y, max_y = connection.execute(
        "SELECT MIN(tile_column), MAX(tile_column), MIN(tile_row), MAX(tile_row) "
        "FROM tiles WHERE zoom_level = ?",
        (zoom,),
    ).fetchone()
    n = 2**zoom

    def lon(x: int) -> float:
        return x / n * 360 - 180

    def lat(xyz_y: int) -> float:
        """Latitud del borde **superior** de la fila XYZ [xyz_y]."""
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * xyz_y / n))))

    north = lat(n - 1 - max_y)
    south = lat(n - min_y)
    return lon(min_x), south, lon(max_x + 1), north
