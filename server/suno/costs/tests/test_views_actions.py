"""Pestaña "Costos" (spec, 2.3 a 2.7): bandeja, rechazados, reverso, importar y exportar."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from suno.authn.tests.factories import make_user
from suno.budgets.tests.xlsx import build_xlsx
from suno.core.access import SectionRole
from suno.core.audit import AuditAction
from suno.core.models import AuditEvent, CostEntry, CostStatus, Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs import ledger
from suno.costs.importer import HEADER, SHEET

from .factories import cost_world, entry, grant_costs, support_blob

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def costs_file(*rows: list[object]) -> SimpleUploadedFile:
    return SimpleUploadedFile("costos.xlsx", build_xlsx(rows, header=HEADER, sheet=SHEET), XLSX)


@skip_without_db
class CostActionPagesTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.world, self.budget = cost_world()
        grant_costs(self.world, self.world.supervisor, SectionRole.REVIEWER)
        self.contributor = make_user(self.world.org, username="colaborador", role=Role.VIEWER)
        grant_costs(self.world, self.contributor, SectionRole.CONTRIBUTOR)
        self.viewer = make_user(self.world.org, username="consulta", role=Role.VIEWER)
        grant_costs(self.world, self.viewer, SectionRole.VIEWER)
        self.base = f"/plataforma/proyectos/{self.world.project.pk}/costos/"

    def test_inbox_lists_drafts_of_others_and_decides(self) -> None:
        theirs = entry(self.world, description="Arena de río", attachment=support_blob())
        entry(self.world, description="Mío", created_by=self.world.supervisor)
        self.client.force_login(self.world.supervisor)
        inbox = self.client.get(self.base + "por-aprobar/")
        self.assertContains(inbox, "Arena de río")
        self.assertNotContains(inbox, "Mío")
        self.assertContains(inbox, "Por aprobar (1)")
        response = self.client.post(f"{self.base}{theirs.pk}/aprobar/", {"volver": "bandeja"})
        self.assertRedirects(response, self.base + "por-aprobar/")
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, CostStatus.APPROVED)

    def test_self_approval_is_explained(self) -> None:
        own = entry(self.world, attachment=support_blob())
        self.client.force_login(self.world.admin)
        response = self.client.post(f"{self.base}{own.pk}/aprobar/", follow=True)
        self.assertContains(response, "Nadie aprueba")
        own.refresh_from_db()
        self.assertEqual(own.status, CostStatus.DRAFT)

    def test_contributor_does_not_open_the_inbox(self) -> None:
        self.client.force_login(self.contributor)
        self.assertEqual(self.client.get(self.base + "por-aprobar/").status_code, 403)

    def test_rejection_and_the_authors_list(self) -> None:
        draft = entry(self.world, description="Formaleta", created_by=self.contributor)
        self.client.force_login(self.world.supervisor)
        empty = self.client.post(f"{self.base}{draft.pk}/rechazar/", {"motivo": " "}, follow=True)
        self.assertContains(empty, "Escribe el motivo")
        self.client.post(f"{self.base}{draft.pk}/rechazar/", {"motivo": "Falta la factura"})
        self.client.force_login(self.contributor)
        page = self.client.get(self.base + "rechazados/")
        self.assertContains(page, "Formaleta")
        self.assertContains(page, "Falta la factura")
        self.assertContains(page, "Mis rechazados (1)")
        self.assertContains(page, f"{self.base}{draft.pk}/editar/")

    def test_reverse_and_reopen(self) -> None:
        cost = entry(self.world, status=CostStatus.APPROVED, description="Factura duplicada")
        self.client.force_login(self.world.supervisor)
        self.assertEqual(self.client.get(f"{self.base}{cost.pk}/reversar/").status_code, 403)
        self.client.force_login(self.world.admin)
        self.assertContains(self.client.get(f"{self.base}{cost.pk}/"), "Reversar")
        self.assertContains(self.client.get(f"{self.base}{cost.pk}/reversar/"), "Reversar costo")
        response = self.client.post(
            f"{self.base}{cost.pk}/reversar/", {"motivo": "Duplicada", "reabrir": "1"}
        )
        reversal = CostEntry.objects.get(reverses=cost)
        self.assertEqual(reversal.amount, -cost.amount)
        self.assertTrue(response["Location"].startswith(f"{self.base}nuevo/?copiar={cost.pk}"))
        self.assertContains(self.client.get(response["Location"]), 'value="Factura duplicada"')
        self.assertContains(self.client.get(f"{self.base}{cost.pk}/"), "Reversado el")

    def test_import_preview_and_apply(self) -> None:
        self.client.force_login(self.contributor)
        good = [date(2026, 3, 10), "Materiales", "2.1", "", "Cemento", 950000, "", "Factura", "1"]
        preview = self.client.post(self.base + "importar/", {"file": costs_file(good)})
        self.assertContains(preview, "1 costo por")
        self.assertContains(preview, "Cargar en borrador")
        token = preview.context["result"].token
        response = self.client.post(self.base + "importar/aplicar/", {"token": token})
        self.assertRedirects(response, self.base + "?estado=draft")
        self.assertEqual(CostEntry.objects.filter(status=CostStatus.DRAFT).count(), 1)
        bad = self.client.post(
            self.base + "importar/", {"file": costs_file([*good[:2], "9.9", *good[3:]])}
        )
        self.assertContains(bad, "Fila 2")
        self.assertNotContains(bad, "Cargar en borrador")
        wrong = SimpleUploadedFile("costos.xls", b"x", "application/vnd.ms-excel")
        self.assertEqual(
            self.client.post(self.base + "importar/", {"file": wrong}).status_code, 200
        )

    def test_template_and_export(self) -> None:
        entry(self.world, status=CostStatus.APPROVED, description="Cemento gris")
        self.client.force_login(self.contributor)
        template = self.client.get(self.base + "plantilla.xlsx")
        self.assertEqual(template["Content-Type"], XLSX)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.base + "plantilla.xlsx").status_code, 403)
        export = self.client.get(self.base + "libro.xlsx?estado=approved")
        self.assertEqual(export["Content-Type"], XLSX)
        self.assertIn("costos-", export["Content-Disposition"])
        event = AuditEvent.objects.get(action=AuditAction.COST_EXPORTED)
        self.assertEqual(event.metadata["lines"], 1)
        with mock.patch.object(ledger, "MAX_EXPORT_LINES", 0):
            refused = self.client.get(self.base + "libro.xlsx", follow=True)
        self.assertContains(refused, "filtra por fechas")
