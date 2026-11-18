"""Catálogo de materiales en la web (spec, 3.1): lista, formulario, importación y permisos."""

from __future__ import annotations

import unittest

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from suno.backoffice.export import to_xlsx_book
from suno.core.models import Material
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import material_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

BASE = "/plataforma/materiales/"


@skip_without_db
class CatalogPageTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.world = self.mw.world

    def test_anyone_with_costs_reads_the_catalog_with_filters(self) -> None:
        self.client.force_login(self.mw.cw.viewer)
        page = self.client.get(BASE)
        self.assertContains(page, "CEM-01")
        self.assertNotContains(page, "Nuevo material")
        found = self.client.get(BASE + "?texto=arena")
        self.assertContains(found, "ARE-01")
        self.assertNotContains(found, "CEM-01")
        self.assertNotContains(self.client.get(BASE + "?estado=inactivos"), "CEM-01")
        self.client.force_login(self.world.agent)  # sin la sección Costos
        self.assertEqual(self.client.get(BASE).status_code, 404)

    def test_create_edit_and_deactivate(self) -> None:
        self.client.force_login(self.mw.cw.viewer)
        self.assertEqual(self.client.get(BASE + "nuevo/").status_code, 403)
        self.client.force_login(self.world.admin)
        form = {"code": "LAD-01", "name": "Ladrillo tolete", "unit": "und", "category": "",
                "active": "on"}  # fmt: skip
        self.assertRedirects(self.client.post(BASE + "nuevo/", form), BASE)
        again = self.client.post(BASE + "nuevo/", {**form, "code": "lad-01"})
        self.assertContains(again, "Ya hay un material con el código lad-01.")
        brick = Material.objects.get(code="LAD-01")
        edited = {**form, "name": "Ladrillo macizo"}
        del edited["active"]  # casilla sin marcar: se desactiva
        self.client.post(f"{BASE}{brick.pk}/editar/", edited)
        brick.refresh_from_db()
        self.assertEqual((brick.name, brick.active), ("Ladrillo macizo", False))
        other = material_world("Otra constructora")
        self.assertEqual(self.client.get(f"{BASE}{other.cement.pk}/editar/").status_code, 404)

    def test_import_preview_and_apply(self) -> None:
        self.client.force_login(self.world.admin)
        content = to_xlsx_book([("Materiales", ["Código", "Nombre", "Unidad", "Categoría"],
                                 [["LAD-01", "Ladrillo", "und", "Mampostería"]])])  # fmt: skip
        upload = SimpleUploadedFile("materiales.xlsx", content)
        page = self.client.post(BASE + "importar/", {"file": upload})
        self.assertContains(page, "1 nuevo")
        token = page.context["result"].token
        self.assertRedirects(self.client.post(BASE + "importar/aplicar/", {"token": token}), BASE)
        self.assertTrue(Material.objects.filter(code="LAD-01").exists())
        template = self.client.get(BASE + "plantilla.xlsx")
        self.assertEqual(template.status_code, 200)

    def test_the_sidebar_links_the_catalog(self) -> None:
        self.client.force_login(self.world.admin)
        self.assertContains(self.client.get("/plataforma/proyectos/"), f'href="{BASE}"')
