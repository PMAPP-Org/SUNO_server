"""Registro de trabajadores y factor en la web (spec 3.1 y 3.6; decisión 3 del plan)."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from suno.core.access import AccessScope, SectionRole
from suno.core.models import AuditEvent, BenefitFactor, Worker, WorkerWage
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import TODAY, grant_labor, labor_world, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
BASE = "/plataforma/trabajadores/"


@skip_without_db
class RegistryPageTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()

    def test_list_masks_documents_and_the_sheet_shows_them_whole(self) -> None:
        self.client.force_login(self.lw.world.admin)
        page = self.client.get(BASE)
        self.assertContains(page, "Pedro Pérez")
        self.assertContains(page, "CC ***5678")
        self.assertNotContains(page, "1012345678")
        sheet = self.client.get(f"{BASE}{self.lw.pedro.pk}/")
        self.assertContains(sheet, "1012345678")
        self.assertContains(sheet, "80.000,00")
        self.assertContains(self.client.get(BASE), 'href="/plataforma/trabajadores/"', html=False)

    def test_who_sees_the_registry(self) -> None:
        for user, status in (
            (self.lw.accountant, 404),  # solo Costos
            (self.lw.world.agent, 404),
            (self.lw.viewer, 200),  # Consulta con alcance de organización: lee
        ):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(BASE).status_code, status)
        limited = self.lw.cw.contributor
        grant_labor(self.lw.world, limited, SectionRole.MANAGER, scope=AccessScope.PROJECTS,
                    projects=[self.lw.project])  # fmt: skip
        self.client.force_login(limited)
        self.assertEqual(self.client.get(BASE).status_code, 404)
        self.client.force_login(self.lw.viewer)
        self.assertEqual(self.client.get(f"{BASE}nuevo/").status_code, 403)
        self.assertNotContains(self.client.get(BASE), "Nuevo trabajador")

    def test_create_change_wage_retire_and_delete(self) -> None:
        self.client.force_login(self.lw.world.admin)
        created = self.client.post(f"{BASE}nuevo/", {
            "document_type": "CC", "document_number": "1.023.456.789", "full_name": "Luis Torres",
            "position": "Oficial", "hired_on": "2026-02-01", "daily_wage": "85000",
        })  # fmt: skip
        luis = Worker.objects.get(document_number="1023456789")
        self.assertRedirects(created, f"{BASE}{luis.pk}/")
        self.client.post(f"{BASE}{luis.pk}/jornal/", {"valid_from": "2026-04-01",
                                                      "daily_wage": "90000"})  # fmt: skip
        self.assertEqual(WorkerWage.objects.filter(worker=luis).count(), 2)
        self.client.post(f"{BASE}{luis.pk}/retirar/", {"retired_on": "2026-04-10"})
        luis.refresh_from_db()
        self.assertFalse(luis.is_active)
        self.assertRedirects(self.client.post(f"{BASE}{luis.pk}/borrar/"), BASE)
        self.assertFalse(Worker.objects.filter(pk=luis.pk).exists())

    def test_import_in_two_steps(self) -> None:
        self.client.force_login(self.lw.world.admin)
        header = ["Tipo de documento", "Número de documento", "Nombre", "Cargo",
                  "Fecha de ingreso", "Jornal"]  # fmt: skip
        book = xlsx("Trabajadores", header, [["CC", "70111222", "Ana", "Ayudante", "2026-02-01",
                                              "70000"]])  # fmt: skip
        preview = self.client.post(f"{BASE}importar/", {"file": _file(book)})
        self.assertContains(preview, "nuevo")
        token = preview.context["result"].token
        applied = self.client.post(f"{BASE}importar/aplicar/", {"token": token})
        self.assertRedirects(applied, BASE)
        self.assertTrue(Worker.objects.filter(document_number="70111222").exists())
        template = self.client.get(f"{BASE}plantilla.xlsx")
        self.assertEqual(template.status_code, 200)

    def test_factor_history(self) -> None:
        self.client.force_login(self.lw.world.admin)
        self.assertContains(self.client.get(f"{BASE}factor/"), "1,5200")
        self.client.post(f"{BASE}factor/", {"valid_from": "2026-04-01", "factor": "1.55",
                                            "note": "Dotación"})  # fmt: skip
        self.assertTrue(
            BenefitFactor.objects.filter(org=self.lw.world.org, factor=D("1.5500")).exists()
        )
        self.assertTrue(AuditEvent.objects.filter(action="benefit_factor.changed").exists())


def _file(content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        "trabajadores.xlsx",
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
