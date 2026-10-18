"""Mapas base offline (Fase B): validación del MBTiles, importación, listado y
descarga aislados por organización, y retiro.
"""

from __future__ import annotations

import sqlite3
import tempfile
import typing
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
from visitacampo.basemaps import services
from visitacampo.basemaps.mbtiles import InvalidMbtiles, inspect
from visitacampo.basemaps.schemas import TileFormatValue
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import Principal, RequestContext, resolve_request_context
from visitacampo.core.models import AppUser, AuditEvent, BasemapPackage, Role, TileFormat
from visitacampo.core.policies import PolicyDenied
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def make_mbtiles(
    directory: str,
    *,
    fmt: str | None = "png",
    tile: bytes = PNG,
    bounds: str | None = "-74.2,4.5,-74.0,4.7",
    tiles: list[tuple[int, int, int]] | None = None,
    name: str = "mapa.mbtiles",
) -> Path:
    path = Path(directory) / name
    connection = sqlite3.connect(path)
    with connection:
        connection.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
        connection.execute(
            "CREATE TABLE tiles "
            "(zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)"
        )
        metadata = {"name": "Finca El Roble", "minzoom": "10", "maxzoom": "16"}
        if fmt is not None:
            metadata["format"] = fmt
        if bounds is not None:
            metadata["bounds"] = bounds
        connection.executemany("INSERT INTO metadata VALUES (?, ?)", list(metadata.items()))
        for z, x, y in tiles if tiles is not None else [(10, 300, 520), (16, 19200, 33300)]:
            connection.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)", (z, x, y, tile))
    connection.close()
    return path


class MbtilesValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def test_a_raster_package_is_described(self) -> None:
        info = inspect(make_mbtiles(self.dir))
        self.assertEqual((info.tile_format, info.min_zoom, info.max_zoom), ("png", 10, 16))
        self.assertEqual((info.west, info.south, info.east, info.north), (-74.2, 4.5, -74.0, 4.7))
        self.assertEqual(info.name, "Finca El Roble")

    def test_vector_tiles_are_rejected_with_a_clear_reason(self) -> None:
        with self.assertRaisesRegex(InvalidMbtiles, "vectorial"):
            inspect(make_mbtiles(self.dir, fmt="pbf"))

    def test_the_tiles_must_match_the_declared_format(self) -> None:
        with self.assertRaisesRegex(InvalidMbtiles, "jpeg"):
            inspect(make_mbtiles(self.dir, fmt="jpeg", tile=PNG))

    def test_without_declared_format_it_is_sniffed(self) -> None:
        self.assertEqual(inspect(make_mbtiles(self.dir, fmt=None, tile=JPEG)).tile_format, "jpg")

    def test_without_bounds_they_come_from_the_tiles(self) -> None:
        # Zoom 1, toda la mitad oeste del mundo (columna 0, las dos filas TMS).
        info = inspect(make_mbtiles(self.dir, bounds=None, tiles=[(1, 0, 0), (1, 0, 1)]))
        self.assertAlmostEqual(info.west, -180)
        self.assertAlmostEqual(info.east, 0)
        self.assertAlmostEqual(info.north, 85.0511, places=3)
        self.assertAlmostEqual(info.south, -85.0511, places=3)

    def test_not_sqlite_empty_or_broken_is_rejected(self) -> None:
        not_sqlite = Path(self.dir) / "x.mbtiles"
        not_sqlite.write_bytes(b"hola")
        for path in (not_sqlite, make_mbtiles(self.dir, tiles=[], name="vacio.mbtiles")):
            with self.subTest(path=path.name), self.assertRaises(InvalidMbtiles):
                inspect(path)
        with self.assertRaisesRegex(InvalidMbtiles, "Extensión"):
            inspect(make_mbtiles(self.dir, bounds="10,0,-10,5", name="al-reves.mbtiles"))

    def test_the_schema_and_the_model_list_the_same_formats(self) -> None:
        self.assertEqual(set(typing.get_args(TileFormatValue)), set(TileFormat.values))


@skip_without_db
class BasemapServiceTests(TestCase):
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
        self.agent = make_user(self.org, username="agente")
        self.other = make_org("Org B")
        self.other_admin = make_user(self.other, username="admin-b", role=Role.ADMIN)

    def ctx(self, user: AppUser) -> RequestContext:
        return resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))

    def imported(self, user: AppUser | None = None, **kwargs: Any) -> BasemapPackage:
        return services.import_basemap(
            self.ctx(user or self.admin), path=make_mbtiles(self._tmp.name, **kwargs)
        )

    def test_an_admin_imports_and_it_is_audited(self) -> None:
        package = self.imported()
        self.assertEqual((package.org_id, package.uploaded_by_id), (self.org.pk, self.admin.pk))
        self.assertEqual(package.name, "Finca El Roble")
        store = get_blob_store()
        assert isinstance(store, LocalFsBlobStore)  # nosec B101 — test con localfs
        self.assertTrue(store.basemap_exists(package.sha256))
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.BASEMAP_IMPORTED).exists())

    def test_an_agent_cannot_import(self) -> None:
        with self.assertRaises(PolicyDenied):
            self.imported(user=self.agent)

    def test_too_big_is_rejected_before_reading_it(self) -> None:
        with (
            override_settings(BASEMAP_MAX_BYTES=10),
            self.assertRaisesRegex(InvalidMbtiles, "máximo"),
        ):
            self.imported()

    def test_the_command_imports_on_behalf_of_an_admin_of_that_org(self) -> None:
        path = make_mbtiles(self._tmp.name)
        out = StringIO()
        call_command(
            "import_basemap",
            str(path),
            org=str(self.org.pk),
            by="admin-a",
            name="Roble",
            stdout=out,
        )
        self.assertIn("Importado «Roble»", out.getvalue())
        # El admin de B no existe en A: no puede importar en A.
        with self.assertRaisesRegex(CommandError, "No hay un usuario"):
            call_command("import_basemap", str(path), org=str(self.org.pk), by="admin-b")
        with self.assertRaisesRegex(CommandError, "no puede administrar"):
            call_command("import_basemap", str(path), org=str(self.org.pk), by="agente")
        with self.assertRaisesRegex(CommandError, "rechazado"):
            call_command(
                "import_basemap",
                str(make_mbtiles(self._tmp.name, fmt="pbf", name="v.mbtiles")),
                org=str(self.org.pk),
                by="admin-a",
            )

    def test_retired_ones_are_no_longer_offered(self) -> None:
        package = self.imported()
        services.retire(self.ctx(self.admin), package.pk)
        self.assertFalse(services.live_basemaps(self.ctx(self.agent)).exists())
        with self.assertRaises(services.BasemapNotFound):
            services.download(self.ctx(self.agent), package.pk)
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.BASEMAP_RETIRED).exists())


@skip_without_db
@override_settings(STORAGE_BACKEND="localfs")
class BasemapApiTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(STORAGE_LOCAL_ROOT=self._tmp.name + "/store")
        overrides.enable()
        self.addCleanup(overrides.disable)

        self.org = make_org("Org A")
        admin = make_user(self.org, username="admin-a", role=Role.ADMIN)
        make_user(self.org, username="agente")
        self.package = services.import_basemap(
            resolve_request_context(Principal(user_id=admin.pk, org_id=self.org.pk)),
            path=make_mbtiles(self._tmp.name),
        )
        other = make_org("Org B")
        other_admin = make_user(other, username="admin-b", role=Role.ADMIN)
        self.foreign = services.import_basemap(
            resolve_request_context(Principal(user_id=other_admin.pk, org_id=other.pk)),
            path=make_mbtiles(self._tmp.name, tile=JPEG, fmt="jpg", name="b.mbtiles"),
        )
        self.client = Client()
        tokens = login(self.client).json()["tokens"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access_token']}"}

    def test_lists_only_its_organization(self) -> None:
        response = self.client.get("/v1/basemaps", **self.auth)
        self.assertEqual(response.status_code, 200, response.content)
        items = response.json()["items"]
        self.assertEqual([item["id"] for item in items], [str(self.package.pk)])
        self.assertEqual(
            items[0]["bounds"], {"west": -74.2, "south": 4.5, "east": -74.0, "north": 4.7}
        )
        self.assertEqual(items[0]["sha256"], self.package.sha256)

    def test_download_gives_a_short_lived_url_and_the_checksum(self) -> None:
        response = self.client.get(f"/v1/basemaps/{self.package.pk}/download", **self.auth)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn(self.package.sha256, body["url"])
        self.assertEqual((body["sha256"], body["size"]), (self.package.sha256, self.package.size))
        self.assertEqual(body["expires_in"], 3600)

    def test_another_organizations_package_is_not_found(self) -> None:
        """IDOR: el de otra organización responde igual que uno inexistente."""
        for basemap_id in (self.foreign.pk, uuid.uuid4()):
            with self.subTest(basemap_id=basemap_id):
                response = self.client.get(f"/v1/basemaps/{basemap_id}/download", **self.auth)
                self.assertEqual(response.status_code, 404)

    def test_requires_authentication(self) -> None:
        self.assertEqual(self.client.get("/v1/basemaps").status_code, 401)
