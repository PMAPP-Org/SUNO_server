"""Conciliación de nómina en la web (spec 2.6 y 3.3): importar, ver, filtrar por proyecto,
exportar; cada consulta queda en la auditoría."""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from suno.authn.tests.factories import make_user
from suno.core.access import AccessScope, SectionRole
from suno.core.models import AuditEvent, PayrollImport, PayrollImportLine, Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import TODAY, WEEK, bare_line, bare_timesheet, grant_labor, labor_world, xlsx

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

    def test_the_list_pages_by_100(self) -> None:
        """M9: la lista cortaba en 200 sin paginar (decisión 26: de a 100)."""
        PayrollImport.objects.bulk_create([
            PayrollImport(org=self.lw.world.org, starts_on=date(2020, 1, 1) + timedelta(days=n),
                          ends_on=date(2020, 1, 1) + timedelta(days=n), file_name=f"n{n}.xlsx",
                          file_size=1, file_sha256="0" * 64, line_count=1, total=1,
                          imported_by=self.lw.world.admin)
            for n in range(201)
        ])  # fmt: skip
        self.client.force_login(self.lw.world.admin)
        first = self.client.get(BASE)
        self.assertEqual(len(first.context["rows"]), 100)
        self.assertContains(first, "Página 1 de 3")
        last = self.client.get(BASE + "?pagina=3")
        self.assertEqual(len(last.context["rows"]), 1)
        self.assertContains(last, "01/01/2020")  # la más vieja, en la última página

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


@skip_without_db
class PayrollDeleteTests(TestCase):
    """#103: una nómina importada se borra (con sus líneas) para corregirla e importarla otra
    vez. Pide `delete` de Mano de obra en toda la organización y una confirmación."""

    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.payroll = PayrollImport.objects.create(
            org=self.lw.world.org, starts_on=date(2026, 3, 1), ends_on=date(2026, 3, 31),
            file_name="nomina.xlsx", file_size=10, file_sha256="0" * 64, line_count=1,
            total=130000, imported_by=self.lw.world.admin,
        )  # fmt: skip
        PayrollImportLine.objects.create(
            org=self.lw.world.org, payroll=self.payroll, row=2, document_type="CC",
            document_number="1012345678", worker=self.lw.pedro, total_paid=130000,
        )  # fmt: skip
        self.url = f"{BASE}{self.payroll.pk}/borrar/"

    def test_the_manager_confirms_and_the_lines_go_with_it(self) -> None:
        self.client.force_login(self.lw.world.admin)
        reconciliation = self.client.get(f"{BASE}{self.payroll.pk}/")
        self.assertContains(reconciliation, self.url)
        confirm = self.client.get(self.url)
        self.assertContains(confirm, "Borrar la importación")
        self.assertContains(confirm, 'name="confirm"')
        unconfirmed = self.client.post(self.url, {})
        self.assertEqual(unconfirmed.status_code, 200)
        self.assertContains(unconfirmed, "Marca la casilla")
        self.assertTrue(PayrollImport.objects.filter(pk=self.payroll.pk).exists())

        done = self.client.post(self.url, {"confirm": "1"})
        self.assertRedirects(done, BASE)
        self.assertFalse(PayrollImport.objects.filter(pk=self.payroll.pk).exists())
        self.assertFalse(PayrollImportLine.objects.exists())
        event = AuditEvent.objects.get(action="payroll.deleted")
        self.assertEqual(event.target, f"payroll_import:{self.payroll.pk}")
        self.assertEqual(
            (event.metadata["start"], event.metadata["end"], event.metadata["lines"]),
            ("2026-03-01", "2026-03-31", 1),
        )
        self.assertEqual(self.client.get(f"{BASE}{self.payroll.pk}/").status_code, 404)
        # El trabajador ya no aparece en ninguna nómina: se puede borrar si no tiene tareo.
        self.assertFalse(PayrollImportLine.objects.filter(worker=self.lw.pedro).exists())

    def test_the_contributor_with_delete_may_delete(self) -> None:
        self.client.force_login(self.lw.contributor)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.post(self.url, {"confirm": "1"})
        self.assertFalse(PayrollImport.objects.exists())

    def test_permissions_and_other_organizations(self) -> None:
        self.client.force_login(self.lw.viewer)  # lee la conciliación, no borra
        self.assertNotContains(self.client.get(f"{BASE}{self.payroll.pk}/"), self.url)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"confirm": "1"}).status_code, 403)
        self.client.force_login(self.lw.accountant)  # sin Mano de obra
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"confirm": "1"}).status_code, 404)
        other = labor_world("Ajena")
        self.client.force_login(other.world.admin)  # IDOR: la nómina es de otra organización
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"confirm": "1"}).status_code, 404)
        self.client.force_login(self.lw.world.admin)
        self.assertEqual(self.client.get(f"{BASE}{other.world.org.pk}/borrar/").status_code, 404)
        self.assertTrue(PayrollImport.objects.filter(pk=self.payroll.pk).exists())
        self.assertFalse(AuditEvent.objects.filter(action="payroll.deleted").exists())

    def test_project_scoped_labor_does_not_reach_the_organization_payroll(self) -> None:
        project_only = make_user(self.lw.world.org, username="tareo-proyecto", role=Role.VIEWER)
        grant_labor(self.lw.world, project_only, SectionRole.MANAGER,
                    scope=AccessScope.PROJECTS, projects=[self.lw.project])  # fmt: skip
        self.client.force_login(project_only)
        self.assertEqual(self.client.post(self.url, {"confirm": "1"}).status_code, 404)
        self.assertTrue(PayrollImport.objects.exists())
