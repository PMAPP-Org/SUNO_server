from __future__ import annotations

import io
import json
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from pypdf import PdfReader
from pypdf.generic import DictionaryObject

from visitacampo.backoffice import export, spatial_export
from visitacampo.backoffice.export import ExportTooLarge
from visitacampo.backoffice.spatial_export import (
    export_visits,
    to_geojson,
    to_geopackage,
    visit_features,
)
from visitacampo.backoffice.visit_pdf import visit_pdf
from visitacampo.core.audit import AuditAction
from visitacampo.core.models import AuditEvent, FormTemplateVersion, Role, Visit
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.forms.tests.test_xlsform import workbook

from .factories import admin_client, make_tenant

setUpModule = setup_test_database
tearDownModule = teardown_test_database


@unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")
class FormsAndExportsTests(TestCase):
    def setUp(self):
        self.a = make_tenant("Org A")
        self.b = make_tenant("Org B")

    def action(self, name, visits, user=None):
        return admin_client(user or self.a.staff).post(
            "/admin/core/visit/",
            {
                "action": name,
                "_selected_action": [str(v.pk) for v in visits],
            },
        )

    def test_exports_authorize_and_scope_even_an_unscoped_queryset(self):
        own, foreign = self.a.visit(), self.b.visit()
        deleted = self.a.visit()
        deleted.deleted_at = timezone.now()
        deleted.save()
        self.assertEqual([v.pk for v in export_visits(self.a.ctx(), Visit.objects.all())], [own.pk])
        with self.assertRaises(PermissionDenied):
            export_visits(self.a.ctx(self.a.agent), Visit.objects.all())
        response = self.action("export_geojson", [own, foreign])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/geo+json")
        self.assertNotIn(str(foreign.pk), response.content.decode())
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditAction.VISITS_EXPORTED, metadata__format="geojson"
            ).exists()
        )
        with patch.object(export, "MAX_EXPORT_ROWS", 0), self.assertRaises(ExportTooLarge):
            export_visits(self.a.ctx(), Visit.objects.all())
        with patch.object(export, "MAX_EXPORT_ROWS", 0):
            self.assertEqual(self.action("export_gpkg", [own]).status_code, 302)
        # CSV/XLSX share the selection: deleted visits never leave either.
        csv = self.action("export_csv", [own, deleted]).content.decode()
        self.assertIn(str(own.pk), csv)
        self.assertNotIn(str(deleted.pk), csv)

    def test_agent_staff_cannot_export_and_pdf_requires_one_visit(self):
        self.a.agent.is_staff = True
        self.a.agent.save()
        self.a.agent.user_permissions.set(self.a.staff.user_permissions.all())
        visit = self.a.visit()
        self.assertEqual(self.action("export_geojson", [visit], self.a.agent).status_code, 302)
        self.assertFalse(AuditEvent.objects.filter(action=AuditAction.VISITS_EXPORTED).exists())
        self.assertEqual(self.action("export_pdf", [visit, self.a.visit()]).status_code, 302)

    def test_large_geometry_exports_refuse_without_a_download_or_audit(self):
        visit = self.a.visit()
        for limit in ("MAX_EXPORT_FEATURES", "MAX_EXPORT_BYTES"):
            with patch.object(spatial_export, limit, 0):
                self.assertEqual(self.action("export_geojson", [visit]).status_code, 302)
        self.assertFalse(AuditEvent.objects.filter(action=AuditAction.VISITS_EXPORTED).exists())

    def test_pdf_labels_choices_and_repeat_rows_remain_readable(self):
        options = [{"value": "ok", "label": {"es": "Bueno"}}]
        self.a.version.definition = {
            "fields": [
                {
                    "key": "rows",
                    "type": "repeat",
                    "label": {"es": "Activos"},
                    "fields": [
                        {
                            "key": "state",
                            "type": "single_choice",
                            "label": {"es": "Estado"},
                            "options": options,
                        },
                        {
                            "key": "many",
                            "type": "multi_choice",
                            "label": {"es": "Opciones"},
                            "options": options,
                        },
                        {"key": "empty", "type": "text", "label": {"es": "Sin responder"}},
                    ],
                }
            ]
        }
        self.a.version.save()
        visit = self.a.visit(rows=[{"state": "ok", "many": ["ok"]}])
        reader = PdfReader(io.BytesIO(visit_pdf(visit)))
        text = "\n".join(p.extract_text() for p in reader.pages)
        self.assertIn("Activos / 1 / Estado", text)
        self.assertIn("Bueno", text)
        self.assertNotIn("Sin responder", text)
        # Historical values that do not fit the choice type print as stored.
        legacy = self.a.visit(rows=[{"state": {"x": 1}, "many": [{"y": 2}]}])
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(visit_pdf(legacy))).pages)
        self.assertIn("Activos / 1 / Estado", text)

    def test_geometry_roundtrip_all_kinds_nested_and_null(self):
        line = {"type": "LineString", "coordinates": [[-74.1, 4.6], [-74.0, 4.7]]}
        polygon = {
            "type": "Polygon",
            "coordinates": [[[-74.1, 4.6], [-74.0, 4.6], [-74.0, 4.7], [-74.1, 4.6]]],
        }
        self.a.version.definition = {
            "fields": [
                {
                    "key": "rows",
                    "type": "repeat",
                    "label": {"es": "Filas"},
                    "fields": [
                        {"key": "line", "type": "line", "label": {"es": "Línea"}},
                        {"key": "polygon", "type": "polygon", "label": {"es": "Polígono"}},
                        {"key": "point", "type": "location", "label": {"es": "Punto"}},
                    ],
                }
            ]
        }
        self.a.version.save()
        visit = self.a.visit(
            rows=[{"line": line, "polygon": polygon, "point": {"lat": 4.5, "lon": -74.2}}]
        )
        blank = self.a.visit()
        blank.lat = blank.lon = None
        blank.save()
        collection = visit_features(export_visits(self.a.ctx(), Visit.objects.all()))
        features = collection["features"]
        self.assertEqual(
            [f["geometry"]["type"] if f["geometry"] else None for f in features],
            ["Point", "LineString", "Polygon", "Point", None],
        )
        self.assertEqual(features[1]["properties"]["field"], "rows.0.line")
        self.assertEqual(json.loads(to_geojson(collection)), collection)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "visitas.gpkg"
            path.write_bytes(to_geopackage(collection))
            with sqlite3.connect(path) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(
                    connection.execute("PRAGMA application_id").fetchone()[0], 0x47504B47
                )
                rows = connection.execute(
                    "SELECT geom,answers FROM visitas ORDER BY fid"
                ).fetchall()
                self.assertEqual(len(rows), 5)
                self.assertIsNone(rows[-1][0])
                self.assertEqual(rows[0][0][:8], b"GP\x00\x01\xe6\x10\x00\x00")
                self.assertEqual(struct.unpack("<BIdd", rows[0][0][8:]), (1, 1, -74.08, 4.6))
                self.assertEqual(struct.unpack("<BII", rows[1][0][8:17]), (1, 2, 2))
                self.assertEqual(struct.unpack("<BII", rows[2][0][8:17]), (1, 3, 1))
                self.assertEqual(json.loads(rows[0][1]), visit.answers)
            # Windows closes connections on exit, not just the transaction context.
            connection.close()
        response = self.action("export_gpkg", [visit])
        self.assertTrue(response.content.startswith(b"SQLite format 3"))

    def test_legacy_template_and_multipage_pdf_escape_markup(self):
        self.a.version.definition = {}
        self.a.version.save()
        visit = self.a.visit(notas="Bogotá <script> & firma " * 800, final="FIN DEL INFORME")
        data = visit_pdf(visit)
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(p.extract_text() for p in reader.pages)
        self.assertGreater(len(reader.pages), 1)
        self.assertIn("Bogotá <script> & firma", text)
        self.assertIn("FIN DEL INFORME", text)
        self.assertIn(str(visit.pk), text)
        root = reader.trailer["/Root"]
        self.assertIsInstance(root, DictionaryObject)
        assert isinstance(root, DictionaryObject)
        self.assertFalse(root.get("/OpenAction"))
        self.assertEqual(len(visit_features([visit])["features"]), 1)
        response = self.action("export_pdf", [visit])
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("no-store", response["Cache-Control"])

    def test_import_admin_creates_immutable_version_and_audit(self):
        client = admin_client(self.a.staff)
        url = f"/admin/core/formtemplate/{self.a.version.template_id}/import-xlsform/"
        self.assertEqual(client.get(url).status_code, 200)
        data = workbook(
            [["type", "name", "label", "default"], ["barcode", "code", "Código", "initial"]]
        )
        response = client.post(
            url, {"workbook": SimpleUploadedFile("form.xlsx", data), "name": "Códigos"}
        )
        self.assertEqual(response.status_code, 302)
        versions = list(
            FormTemplateVersion.objects.filter(template_id=self.a.version.template_id).order_by(
                "version_number"
            )
        )
        self.assertEqual([v.version_number for v in versions], [1, 2])
        self.assertEqual(versions[0].definition, self.a.version.definition)
        self.assertGreater(versions[1].change_seq, versions[0].change_seq)
        self.assertEqual(versions[1].definition["fields"][0]["default"], "initial")
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditAction.TEMPLATE_PUBLISHED, metadata__source="xlsform"
            ).exists()
        )
        response = client.post(url, {"workbook": SimpleUploadedFile("bad.xlsx", b"bad")})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "XLSForm inválido")
        self.assertEqual(
            FormTemplateVersion.objects.filter(template_id=self.a.version.template_id).count(), 2
        )
        with patch("visitacampo.backoffice.xlsform_admin.MAX_FILE_BYTES", 1):
            self.assertContains(
                client.post(url, {"workbook": SimpleUploadedFile("large.xlsx", data)}),
                "supera 5 MiB",
            )
        self.assertEqual(
            client.get(
                f"/admin/core/formtemplate/{self.b.version.template_id}/import-xlsform/"
            ).status_code,
            404,
        )
        change = f"/admin/core/formtemplate/{self.a.version.template_id}/change/"
        self.assertContains(client.get(change), "Importar XLSForm")
        supervisor = make_tenant("Supervisor", staff_role=Role.SUPERVISOR)
        supervisor_client = admin_client(supervisor.staff)
        self.assertEqual(
            supervisor_client.get(
                f"/admin/core/formtemplate/{supervisor.version.template_id}/import-xlsform/"
            ).status_code,
            403,
        )
        supervisor_change = supervisor_client.get(
            f"/admin/core/formtemplate/{supervisor.version.template_id}/change/"
        )
        self.assertNotContains(supervisor_change, "Importar XLSForm")

    def test_import_command_validation_publication_and_denials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "form.xlsx"
            path.write_bytes(workbook([["type", "name", "label"], ["text", "code", "Código"]]))
            options = {
                "org": self.a.org.pk,
                "by": self.a.staff.username,
                "template": self.a.version.template_id,
                "stdout": io.StringIO(),
            }
            call_command("import_xlsform", str(path), validate_only=True, **options)
            self.assertEqual(
                FormTemplateVersion.objects.filter(template_id=self.a.version.template_id).count(),
                1,
            )
            call_command("import_xlsform", str(path), **options)
            versions = FormTemplateVersion.objects.filter(template_id=self.a.version.template_id)
            self.assertEqual(versions.count(), 2)
            # Without --name the version takes the template's name, as in the admin.
            self.assertEqual(versions.get(version_number=2).name, self.a.version.template.name)
            for overrides in (
                {"by": self.a.agent.username},
                {"by": "missing"},
                {"template": self.b.version.template_id},
            ):
                with self.assertRaises(CommandError):
                    call_command("import_xlsform", str(path), **{**options, **overrides})
            with self.assertRaises(CommandError):
                call_command("import_xlsform", str(path) + "missing", **options)
