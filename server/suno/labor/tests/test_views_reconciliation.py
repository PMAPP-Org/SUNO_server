"""Conciliación de nómina en la web (spec 2.6 y 3.3): importar, ver, filtrar por proyecto,
exportar; cada consulta queda en la auditoría."""

from __future__ import annotations

import unittest
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from suno.core.models import AuditEvent, PayrollImport
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import TODAY, WEEK, bare_line, bare_timesheet, labor_world, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

BASE = "/plataforma/trabajadores/conciliacion/"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@skip_without_db
class ReconciliationPageTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        sheet = bare_timesheet(self.lw, status="approved")
        bare_line(sheet, self.lw.pedro, WEEK[0], frozen=("80000.00", "1.5200", "121600.00"))

    def upload(self) -> PayrollImport:
        book = xlsx("Nómina", ["Tipo de documento", "Número de documento", "Total pagado"],
                    [["CC", "1012345678", "130000"]])  # fmt: skip
        preview = self.client.post(BASE + "importar/", {
            "starts_on": "2026-03-01", "ends_on": "2026-03-31",
            "file": SimpleUploadedFile("nomina.xlsx", book, content_type=XLSX),
        })  # fmt: skip
        self.assertContains(preview, "1 de 1")  # documentos del registro
        token = preview.context["result"].token
        self.client.post(BASE + "importar/aplicar/", {"token": token})
        return PayrollImport.objects.get()

    def test_import_view_filter_and_export(self) -> None:
        self.client.force_login(self.lw.world.admin)
        payroll = self.upload()
        page = self.client.get(f"{BASE}{payroll.pk}/")
        self.assertContains(page, "Pedro Pérez · CC ***5678")
        self.assertContains(page, "8.400,00")  # 130.000 − 121.600
        self.assertEqual(AuditEvent.objects.filter(action="labor_reconciliation.viewed").count(), 1)
        filtered = self.client.get(f"{BASE}{payroll.pk}/?proyecto={self.lw.project.pk}")
        self.assertEqual(filtered.status_code, 200)
        export = self.client.get(f"{BASE}{payroll.pk}/conciliacion.xlsx")
        self.assertEqual(export["Content-Type"], XLSX)
        self.assertIn("conciliacion-2026-03-01-2026-03-31", export["Content-Disposition"])
        self.assertTrue(AuditEvent.objects.filter(action="labor.exported").exists())

    def test_permissions(self) -> None:
        self.client.force_login(self.lw.world.admin)
        payroll = self.upload()
        self.client.force_login(self.lw.viewer)  # lee el registro, no importa ni exporta
        self.assertEqual(self.client.get(f"{BASE}{payroll.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"{BASE}{payroll.pk}/conciliacion.xlsx").status_code, 403)
        self.assertEqual(self.client.post(BASE + "importar/", {}).status_code, 403)
        self.client.force_login(self.lw.accountant)
        self.assertEqual(self.client.get(BASE).status_code, 404)
        other = labor_world("Ajena")
        self.client.force_login(other.world.admin)
        self.assertEqual(self.client.get(f"{BASE}{payroll.pk}/").status_code, 404)
