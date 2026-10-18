"""Capas de referencia (Fase C): validación del GeoJSON, importación, listado
y descarga aislados por organización, y retiro."""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from io import StringIO
from pathlib import Path
from typing import Any

from django.core.management import CommandError, call_command
from django.test import Client, TestCase, override_settings

from visitacampo.attachments.localfs import LocalFsBlobStore
from visitacampo.attachments.storage import get_blob_store
from visitacampo.authn.tests.factories import login, make_org, make_user
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import Principal, RequestContext, resolve_request_context
from visitacampo.core.models import AppUser, AuditEvent, ReferenceLayer, Role
from visitacampo.core.policies import PolicyDenied
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.layers import services
from visitacampo.layers.geojson import InvalidLayer, inspect

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

PREDIOS: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"codigo": "P-001", "propietario": "Finca El Roble"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[-74.08, 4.6], [-74.07, 4.6], [-74.07, 4.61], [-74.08, 4.61], [-74.08, 4.6]]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {"nombre": "Pozo"},
            "geometry": {"type": "Point", "coordinates": [-74.075, 4.605]},
        },
    ],
}


def write(directory: str, data: Any, name: str = "capa.geojson") -> Path:
    path = Path(directory) / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


class GeoJsonValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def check(self, data: Any) -> Any:
        return inspect(write(self._tmp.name, data))

    def test_a_valid_layer_is_described(self) -> None:
        info = self.check(PREDIOS)
        self.assertEqual(info.feature_count, 2)
        self.assertEqual(info.geometry_types, ("Point", "Polygon"))
        self.assertEqual(
            (info.west, info.south, info.east, info.north), (-74.08, 4.6, -74.07, 4.61)
        )

    def test_projected_coordinates_are_rejected_with_the_fix(self) -> None:
        projected = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {"type": "Point", "coordinates": [4880061.5, 2067458.0]},
                }
            ],
        }
        with self.assertRaisesRegex(InvalidLayer, "EPSG:4326"):
            self.check(projected)

    def test_a_non_wgs84_crs_member_is_rejected(self) -> None:
        data = dict(PREDIOS, crs={"type": "name", "properties": {"name": "EPSG:9377"}})
        with self.assertRaisesRegex(InvalidLayer, "EPSG:9377"):
            self.check(data)

    def test_broken_inputs_are_rejected(self) -> None:
        cases: dict[str, Any] = {
            "no_json": "{roto",
            "nan": '{"type": "FeatureCollection", "features": [{"type": "Feature", '
            '"properties": {}, "geometry": {"type": "Point", "coordinates": [NaN, 4]}}]}',
            "no_coleccion": {"type": "Feature"},
            "vacia": {"type": "FeatureCollection", "features": []},
            "tipo_raro": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {"type": "Circle", "coordinates": []},
                    }
                ],
            },
            "anillo_vacio": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {"type": "Polygon", "coordinates": [[]]},
                    }
                ],
            },
        }
        for name, data in cases.items():
            with self.subTest(case=name), self.assertRaises(InvalidLayer):
                self.check(data)


@skip_without_db
class LayerServiceAndApiTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(
            STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name + "/store"
        )
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.org = make_org("Org A")
        self.admin = make_user(self.org, username="admin-a", role=Role.ADMIN)
        make_user(self.org, username="agente")
        other = make_org("Org B")
        self.other_admin = make_user(other, username="admin-b", role=Role.ADMIN)

    def ctx(self, user: AppUser) -> RequestContext:
        return resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))

    def test_import_list_download_and_isolation(self) -> None:
        layer = services.import_layer(
            self.ctx(self.admin), path=write(self._tmp.name, PREDIOS), name="Predios"
        )
        store = get_blob_store()
        assert isinstance(store, LocalFsBlobStore)  # nosec B101 — test con localfs
        self.assertTrue(store.layer_exists(layer.sha256))
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.LAYER_IMPORTED).exists())
        foreign = services.import_layer(
            self.ctx(self.other_admin), path=write(self._tmp.name, PREDIOS, "b.geojson"), name="B"
        )

        client = Client()
        auth = {"HTTP_AUTHORIZATION": f"Bearer {login(client).json()['tokens']['access_token']}"}
        items = client.get("/v1/layers", **auth).json()["items"]
        self.assertEqual([item["id"] for item in items], [str(layer.pk)])
        self.assertEqual(items[0]["geometry_types"], ["Point", "Polygon"])
        self.assertEqual(items[0]["feature_count"], 2)

        response = client.get(f"/v1/layers/{layer.pk}/download", **auth)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["sha256"], layer.sha256)
        for layer_id in (foreign.pk, uuid.uuid4()):
            with self.subTest(layer_id=layer_id):
                self.assertEqual(
                    client.get(f"/v1/layers/{layer_id}/download", **auth).status_code, 404
                )

    def test_only_admins_import_and_retired_ones_disappear(self) -> None:
        agent = AppUser.objects.get(username="agente")
        with self.assertRaises(PolicyDenied):
            services.import_layer(self.ctx(agent), path=write(self._tmp.name, PREDIOS), name="X")
        layer = services.import_layer(
            self.ctx(self.admin), path=write(self._tmp.name, PREDIOS), name="X"
        )
        services.retire(self.ctx(self.admin), layer.pk)
        self.assertFalse(services.live_layers(self.ctx(agent)).exists())
        self.assertIsNotNone(ReferenceLayer.objects.get(pk=layer.pk).deleted_at)

    def test_the_command_imports_on_behalf_of_an_admin(self) -> None:
        path = write(self._tmp.name, PREDIOS)
        out = StringIO()
        call_command(
            "import_layer",
            str(path),
            org=str(self.org.pk),
            by="admin-a",
            name="Predios",
            stdout=out,
        )
        self.assertIn("Importada «Predios» (2 entidades", out.getvalue())
        with self.assertRaisesRegex(CommandError, "No hay un usuario"):
            call_command("import_layer", str(path), org=str(self.org.pk), by="admin-b", name="X")
        with self.assertRaisesRegex(CommandError, "rechazada"):
            call_command(
                "import_layer",
                str(write(self._tmp.name, {"type": "Feature"}, "mala.geojson")),
                org=str(self.org.pk),
                by="admin-a",
                name="X",
            )
