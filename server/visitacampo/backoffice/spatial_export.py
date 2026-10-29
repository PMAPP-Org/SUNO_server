"""Visit exports: RFC 7946 GeoJSON and OGC GeoPackage 1.3 (WGS84 XY)."""

from __future__ import annotations

import json
import sqlite3
import struct
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet

from visitacampo.backoffice.export import exportable_visits
from visitacampo.core.context import RequestContext
from visitacampo.core.models import Visit
from visitacampo.core.policies import can_export_visits
from visitacampo.forms.definition import TemplateDefinitionError, validate_definition

MAX_EXPORT_FEATURES = 20_000
MAX_EXPORT_BYTES = 64 * 1024 * 1024


class ExportContentTooLarge(ValueError):
    def __init__(self) -> None:
        super().__init__("La exportación supera 20000 geometrías o 64 MiB. Reduce la selección.")


def export_visits(ctx: RequestContext, queryset: QuerySet[Visit]) -> list[Visit]:
    if not can_export_visits(ctx, ctx.org_id):
        raise PermissionDenied("No puede exportar visitas.")
    return list(exportable_visits(ctx, queryset))


def answer_geometries(
    fields: list[Any], answers: dict[str, Any], prefix: str = ""
) -> Iterator[tuple[str, dict[str, Any]]]:
    for field in fields:
        value = answers.get(field.key)
        path = prefix + field.key
        if field.type == "repeat" and isinstance(value, list):
            for index, row in enumerate(value):
                if isinstance(row, dict):
                    yield from answer_geometries(field.fields, row, f"{path}.{index}.")
        elif value is not None and field.check_answer(value) is None:
            if field.type == "location":
                yield path, {"type": "Point", "coordinates": [value["lon"], value["lat"]]}
            elif field.type in ("line", "polygon"):
                yield path, value


def visit_features(visits: list[Visit]) -> dict[str, Any]:
    features = []
    size = 0
    for visit in visits:
        geometries: list[tuple[str, dict[str, Any] | None]] = []
        if visit.lat is not None and visit.lon is not None:
            geometries.append(("visit", {"type": "Point", "coordinates": [visit.lon, visit.lat]}))
        try:
            definition = validate_definition(visit.template_version.definition)
        except TemplateDefinitionError:
            definition = None  # Historical untyped templates still retain all answers.
        if definition:
            geometries.extend(answer_geometries(definition.fields, visit.answers or {}))
        if not geometries:
            geometries.append(("visit", None))
        # Las respuestas completas van en cada entidad (docs/forms-and-exports.md):
        # se miden una vez por visita, no una por entidad.
        answers_size = len(
            json.dumps(visit.answers, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
        for path, geometry in geometries:
            properties = {
                "visit_id": str(visit.pk),
                "field": path,
                "captured_at": visit.captured_at.isoformat(),
                "template": visit.template_version.name,
                "template_version": visit.template_version.version_number,
                "created_by": str(visit.created_by_id),
                "accuracy_m": visit.accuracy_m,
            }
            feature = {
                "type": "Feature",
                "id": f"{visit.pk}:{path}",
                "geometry": geometry,
                "properties": properties,
            }
            size += answers_size + len(
                json.dumps(feature, ensure_ascii=False, allow_nan=False).encode("utf-8")
            )
            properties["answers"] = visit.answers
            features.append(feature)
            if len(features) > MAX_EXPORT_FEATURES or size > MAX_EXPORT_BYTES:
                raise ExportContentTooLarge
    return {"type": "FeatureCollection", "features": features}


def to_geojson(collection: dict[str, Any]) -> bytes:
    return json.dumps(collection, ensure_ascii=False, allow_nan=False).encode("utf-8")


def _wkb(geometry: dict[str, Any]) -> bytes:
    kind, coordinates = geometry["type"], geometry["coordinates"]
    code = {"Point": 1, "LineString": 2, "Polygon": 3}[kind]
    header = struct.pack("<BI", 1, code)

    def points(values: list[Any]) -> bytes:
        return struct.pack("<I", len(values)) + b"".join(struct.pack("<dd", *p[:2]) for p in values)

    if kind == "Point":
        return header + struct.pack("<dd", *coordinates[:2])
    if kind == "LineString":
        return header + points(coordinates)
    return header + struct.pack("<I", len(coordinates)) + b"".join(points(r) for r in coordinates)


def to_geopackage(collection: dict[str, Any]) -> bytes:
    # Fixed SQL identifiers; values always bound. No extension or network loading.
    with tempfile.TemporaryDirectory(prefix="suno-gpkg-") as directory:
        path = Path(directory) / "visitas.gpkg"
        connection = sqlite3.connect(path)
        try:
            connection.executescript("""
                PRAGMA application_id=1196444487;
                PRAGMA user_version=10300;
                PRAGMA foreign_keys=ON;
                CREATE TABLE gpkg_spatial_ref_sys (
                    srs_name TEXT NOT NULL, srs_id INTEGER NOT NULL PRIMARY KEY,
                    organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
                    definition TEXT NOT NULL, description TEXT);
                CREATE TABLE gpkg_contents (
                    table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL,
                    identifier TEXT UNIQUE, description TEXT DEFAULT '',
                    last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER,
                    FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
                CREATE TABLE gpkg_geometry_columns (
                    table_name TEXT NOT NULL, column_name TEXT NOT NULL,
                    geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL,
                    z TINYINT NOT NULL, m TINYINT NOT NULL,
                    PRIMARY KEY(table_name,column_name), UNIQUE(table_name),
                    FOREIGN KEY(table_name) REFERENCES gpkg_contents(table_name),
                    FOREIGN KEY(srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
                CREATE TABLE visitas (
                    fid INTEGER PRIMARY KEY AUTOINCREMENT, geom GEOMETRY,
                    visit_id TEXT NOT NULL, field TEXT NOT NULL, captured_at TEXT,
                    template TEXT, template_version INTEGER, created_by TEXT,
                    accuracy_m REAL, answers TEXT);
            """)
            wkt = (
                'GEOGCS["WGS 84",DATUM["WGS_1984",'
                'SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],'
                'UNIT["degree",0.0174532925199433],AUTHORITY["EPSG","4326"]]'
            )
            connection.executemany(
                "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
                [
                    ("Undefined Cartesian", -1, "NONE", -1, "undefined", ""),
                    ("Undefined geographic", 0, "NONE", 0, "undefined", ""),
                    ("WGS 84", 4326, "EPSG", 4326, wkt, "Longitude, latitude"),
                ],
            )
            connection.execute(
                "INSERT INTO gpkg_contents (table_name,data_type,identifier,srs_id) "
                "VALUES ('visitas','features','Visitas',4326)"
            )
            connection.execute(
                "INSERT INTO gpkg_geometry_columns VALUES ('visitas','geom','GEOMETRY',4326,0,0)"
            )
            for feature in collection["features"]:
                geometry, props = feature["geometry"], feature["properties"]
                blob = (
                    b"GP" + bytes([0, 1]) + struct.pack("<i", 4326) + _wkb(geometry)
                    if geometry
                    else None
                )
                connection.execute(
                    "INSERT INTO visitas (geom,visit_id,field,captured_at,template,"
                    "template_version,created_by,accuracy_m,answers) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        blob,
                        props["visit_id"],
                        props["field"],
                        props["captured_at"],
                        props["template"],
                        props["template_version"],
                        props["created_by"],
                        props["accuracy_m"],
                        json.dumps(props["answers"], ensure_ascii=False),
                    ),
                )
            connection.commit()
        finally:
            connection.close()
        return path.read_bytes()
