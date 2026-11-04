from __future__ import annotations

import unittest

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from visitacampo.budgets.tests.xlsx import build_xlsx
from visitacampo.core.models import LocationLevel, Project, QuantityPlan, QuantityPlanImport
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.locations import LevelSpec, save_levels
from visitacampo.matrix.template import header
from visitacampo.projects.tests.samples import TEXT1

from .factories import (
    budget_world,
    import_schedule,
    item,
    locate,
    located_xml,
    plan,
    suno_levels,
)

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

    def test_levels_page_keeps_a_field_missing_from_the_latest_xml(self) -> None:
        import_schedule(self.world, located_xml())
        save_levels(
            self.world.ctx(),
            self.world.project,
            [LevelSpec("Tramo", TEXT1), LevelSpec("PR", "999")],
        )
        response = self.client.get(self.base + "niveles/")
        self.assertContains(
            response, '<option value="999" selected>999 (no está en el último XML)</option>'
        )
        self.assertContains(response, f'<option value="{TEXT1}" selected>')

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
        response = self.client.post(
            self.base + "importar/", {"file": SimpleUploadedFile("m.xlsx", data)}
        )
        self.assertContains(response, "UniqueID 99")

    def _stage(self) -> QuantityPlanImport:
        data = build_xlsx(
            [[3, "", "", "Tramo 1", "", "", "2.1", "", "", 6]],
            header=header(["Tramo", "PR"]),
            sheet="Matriz",
        )
        self.client.post(self.base + "importar/", {"file": SimpleUploadedFile("m.xlsx", data)})
        return QuantityPlanImport.objects.get()

    def test_import_of_other_project_is_404(self) -> None:
        other = Project.objects.create(
            org=self.world.org, name="Otra obra", created_by=self.world.admin
        )
        foreign = QuantityPlanImport.objects.create(
            org=self.world.org,
            project=other,
            file_name="m.xlsx",
            source_file=b"",
            uploaded_by=self.world.admin,
        )
        response = self.client.get(f"{self.base}importar/{foreign.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_supervisor_cannot_apply(self) -> None:
        staged = self._stage()
        self.client.force_login(self.world.supervisor)
        response = self.client.post(f"{self.base}importar/{staged.pk}/", {"action": "apply"})
        self.assertEqual(response.status_code, 403)
        staged.refresh_from_db()
        self.assertEqual(staged.status, "pending")
        self.assertEqual(str(QuantityPlan.objects.get().planned_quantity), "4.0000")
