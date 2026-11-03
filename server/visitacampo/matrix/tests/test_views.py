from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.budgets.tests.xlsx import build_xlsx
from visitacampo.core.models import LocationLevel, QuantityPlan, QuantityPlanImport
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.template import header

from .factories import budget_world, item, locate, plan, suno_levels

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class MatrixViewsTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        suno_levels(self.world)
        locate(self.world, 3, "Tramo 1")
        plan(self.world, 3, item(self.budget, "2.1"), "4")
        self.base = f"/plataforma/proyectos/{self.world.project.pk}/matriz/"
        self.client.force_login(self.world.admin)

    def test_field_agent_gets_404(self) -> None:
        self.world.responsible(3)
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_supervisor_sees_but_cannot_import(self) -> None:
        self.client.force_login(self.world.supervisor)
        self.assertContains(self.client.get(self.base), "Concreto zapatas")
        self.assertEqual(self.client.get(self.base + "importar/").status_code, 403)
        self.assertEqual(self.client.get(self.base + "niveles/").status_code, 403)

    def test_items_page_and_tab(self) -> None:
        response = self.client.get(self.base)
        self.assertContains(response, ">Matriz</a>")
        self.assertContains(response, "1.2 Descapote")
        self.assertContains(response, "Tramo 1")
        self.assertContains(response, "Con exceso")
        filtered = self.client.get(self.base + "?estado=over")
        self.assertNotContains(filtered, "Concreto zapatas")

    def test_locations_page(self) -> None:
        response = self.client.get(self.base + "ubicacion/")
        self.assertContains(response, "Tramo 1")
        self.assertContains(response, "1.800.000,00")

    def test_levels_page(self) -> None:
        response = self.client.post(
            self.base + "niveles/",
            {"name_1": "Tramo", "name_2": "PR", "name_3": "Costado"},
        )
        self.assertRedirects(response, self.base + "niveles/")
        self.assertEqual(LocationLevel.objects.filter(project=self.world.project).count(), 3)
        bad = self.client.post(self.base + "niveles/", {"name_1": "Tramo", "name_2": "tramo"})
        self.assertContains(bad, "repetido", status_code=200)

    def test_template_download(self) -> None:
        response = self.client.get(self.base + "plantilla.xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])

    def test_import_preview_apply(self) -> None:
        data = build_xlsx(
            [[3, "", "", "Tramo 1", "", "", "2.1", "", "", 6]],
            header=header(["Tramo", "PR"]),
            sheet="Matriz",
        )
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile("m.xlsx", data)
        response = self.client.post(self.base + "importar/", {"file": upload})
        staged = QuantityPlanImport.objects.get()
        self.assertRedirects(response, f"{self.base}importar/{staged.pk}/")
        preview = self.client.get(f"{self.base}importar/{staged.pk}/")
        self.assertContains(preview, "1.2 Descapote × 2.1: 4 → 6")
        self.client.post(f"{self.base}importar/{staged.pk}/", {"action": "apply"})
        self.assertEqual(str(QuantityPlan.objects.get().planned_quantity), "6.0000")
        self.assertContains(self.client.get(self.base + "historial/"), "m.xlsx")

    def test_import_errors_are_listed(self) -> None:
        data = build_xlsx(
            [[99, "", "", "", "", "", "2.1", "", "", 6]],
            header=header(["Tramo", "PR"]),
            sheet="Matriz",
        )
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.client.post(
            self.base + "importar/", {"file": SimpleUploadedFile("m.xlsx", data)}
        )
        self.assertContains(response, "UniqueID 99")
