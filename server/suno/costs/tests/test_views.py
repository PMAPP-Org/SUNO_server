"""Pestaña "Costos" (spec, 2.1, 2.2 y 5): permisos, lista, formulario, ficha y soporte."""

from __future__ import annotations

import tempfile
import unittest
from typing import Any

from django.test import TestCase, override_settings

from suno.authn.tests.factories import make_user
from suno.core.access import AccessScope, SectionRole
from suno.core.models import CostEntry, CostScope, CostStatus, Project, Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import cost_world, entry, grant_costs, item, pdf_upload

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class CostPagesTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.world, self.budget = cost_world()
        self.project_url = f"/plataforma/proyectos/{self.world.project.pk}/"
        self.base = f"{self.project_url}costos/"
        self.contributor = make_user(self.world.org, username="colaborador", role=Role.VIEWER)
        grant_costs(self.world, self.contributor, SectionRole.CONTRIBUTOR)
        self.viewer = make_user(self.world.org, username="consulta", role=Role.VIEWER)
        grant_costs(self.world, self.viewer, SectionRole.VIEWER)
        self.client.force_login(self.world.admin)

    def form(self, **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "alcance": "item",
            "capitulo": "",
            "item": "2.1 Concreto zapatas",
            "task": str(self.world.task(5).pk),
            "incurred_on": "2026-03-10",
            "kind": "materials",
            "description": "Concreto premezclado",
            "amount": "1500000.00",
            "third_party": "Concretos SA",
            "document_type": "invoice",
            "document_number": "F-101",
            "support": pdf_upload(),
        }
        values.update(overrides)
        return values

    def test_who_sees_the_tab(self) -> None:
        self.assertContains(self.client.get(self.project_url), ">Costos</a>")
        self.client.force_login(self.world.supervisor)  # sin la sección Costos
        self.assertNotContains(self.client.get(self.project_url), ">Costos</a>")
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.base).status_code, 404)
        other, _ = cost_world("Otra constructora")
        self.client.force_login(other.admin)
        self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_project_scope(self) -> None:
        elsewhere = Project.objects.create(
            org=self.world.org, name="Otra obra", created_by=self.world.admin
        )
        scoped = make_user(self.world.org, username="limitado", role=Role.VIEWER)
        grant_costs(
            self.world,
            scoped,
            SectionRole.VIEWER,
            scope=AccessScope.PROJECTS,
            projects=(elsewhere,),
        )
        self.client.force_login(scoped)
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.assertEqual(
            self.client.get(f"/plataforma/proyectos/{elsewhere.pk}/costos/").status_code, 200
        )

    def test_list_filters_and_totals(self) -> None:
        entry(self.world, status=CostStatus.APPROVED, description="Cemento gris")
        entry(self.world, description="Papelería")
        response = self.client.get(self.base)
        self.assertContains(response, 'aria-current="page">Costos</a>')
        self.assertContains(response, "Cemento gris")
        self.assertContains(response, "Papelería")
        self.assertContains(response, "Total aprobado")
        drafts = self.client.get(self.base + "?estado=draft")
        self.assertNotContains(drafts, "Cemento gris")
        self.assertContains(drafts, '<option value="draft" selected>')

    def test_viewer_reads_but_does_not_create(self) -> None:
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.base).status_code, 200)
        self.assertEqual(self.client.get(self.base + "nuevo/").status_code, 403)

    def test_create_an_item_draft_with_support(self) -> None:
        page = self.client.get(self.base + "nuevo/")
        self.assertContains(page, '<datalist id="cst-items">')
        self.assertContains(page, '<option value="2.1 Concreto zapatas">')
        response = self.client.post(self.base + "nuevo/", self.form())
        cost = CostEntry.objects.get()
        self.assertRedirects(response, f"{self.base}{cost.pk}/")
        self.assertEqual((cost.status, cost.task_id), ("draft", self.world.task(5).pk))
        detail = self.client.get(f"{self.base}{cost.pk}/")
        self.assertContains(detail, "2.1 Concreto zapatas · tarea 2.1 Columnas")
        self.assertContains(detail, "factura.pdf")
        support = self.client.get(f"{self.base}{cost.pk}/soporte/")
        self.assertEqual(support.status_code, 302)
        self.assertIn(cost.attachment_id, support["Location"])

    def test_errors_are_shown(self) -> None:
        response = self.client.post(self.base + "nuevo/", self.form(item="9.9 No existe"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "versión vigente de su presupuesto")
        self.assertFalse(CostEntry.objects.exists())

    def test_selection_in_two_steps(self) -> None:
        chapter = item(self.budget, "2")
        step = self.client.get(self.base + f"nuevo/?alcance=selection&capitulo={chapter.pk}")
        for code in ("2.1", "2.2", "2.4"):
            self.assertContains(step, f'value="{item(self.budget, code).pk}"')
        self.client.post(
            self.base + "nuevo/",
            self.form(
                alcance="selection",
                capitulo=str(chapter.pk),
                items=[str(item(self.budget, "2.1").pk), str(item(self.budget, "2.2").pk)],
            ),
        )
        cost = CostEntry.objects.get()
        self.assertEqual((cost.scope, cost.items.count()), (CostScope.SELECTION, 2))

    def test_edit_rules(self) -> None:
        theirs = entry(self.world)
        self.client.force_login(self.contributor)
        self.assertEqual(self.client.get(f"{self.base}{theirs.pk}/editar/").status_code, 403)
        self.client.force_login(self.world.admin)
        self.client.post(f"{self.base}{theirs.pk}/editar/", self.form(description="Corregido"))
        theirs.refresh_from_db()
        self.assertEqual(theirs.description, "Corregido")
        approved = entry(self.world, status=CostStatus.APPROVED)
        response = self.client.get(f"{self.base}{approved.pk}/editar/", follow=True)
        self.assertContains(response, "se reversa")

    def test_delete_and_copy(self) -> None:
        draft = entry(self.world, description="Para borrar")
        copy = self.client.get(self.base + f"nuevo/?copiar={draft.pk}")
        self.assertContains(copy, 'value="Para borrar"')
        self.client.post(f"{self.base}{draft.pk}/borrar/")
        self.assertFalse(CostEntry.objects.filter(pk=draft.pk).exists())
