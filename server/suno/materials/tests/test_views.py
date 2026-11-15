"""Pestaña "Materiales" (spec, 3.2 y 3.5): acceso, inventario, kárdex, bodegas y Excel."""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from django.test import TestCase

from suno.core.models import AuditEvent, Project, Warehouse
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import issue, make_warehouse, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TODAY = date(2026, 4, 15)


@skip_without_db
class MaterialsTabTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.world = self.mw.world
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        issue(self.mw, [(self.mw.cement, "7")], day=date(2026, 3, 10))
        self.base = f"/plataforma/proyectos/{self.mw.project.pk}/materiales/"

    def test_access_follows_the_costs_section(self) -> None:
        self.client.force_login(self.mw.cw.viewer)
        page = self.client.get(self.base)
        self.assertContains(page, "CEM-01")
        self.assertContains(page, "2.976.000,00")  # 93 und a 32.000
        self.assertContains(page, "1 salida sin cortar con más de 31 días")
        self.assertContains(self.client.get(f"/plataforma/proyectos/{self.mw.project.pk}/costos/"),
                            f'href="{self.base}"')  # fmt: skip
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.base).status_code, 404)
        other = material_world("Otra constructora")
        self.client.force_login(self.world.admin)
        self.assertEqual(
            self.client.get(f"/plataforma/proyectos/{other.project.pk}/materiales/").status_code,
            404,
        )

    def test_kardex_of_a_material_in_a_warehouse(self) -> None:
        self.client.force_login(self.mw.cw.viewer)
        url = f"{self.base}kardex/{self.mw.warehouse.pk}/{self.mw.cement.pk}/"
        page = self.client.get(url)
        self.assertContains(page, "CM-001")
        self.assertContains(page, "SA-001")
        self.assertContains(page, "2.976.000,00")
        elsewhere = Project.objects.create(org=self.world.org, name="Otra obra",
                                           created_by=self.world.admin)  # fmt: skip
        foreign = make_warehouse(self.world, "Bodega ajena", project=elsewhere)
        bad = [
            f"{self.base}kardex/{foreign.pk}/{self.mw.cement.pk}/",
            f"{self.base}kardex/{self.mw.warehouse.pk}/{material_world('Otra').cement.pk}/",
        ]
        for wrong in bad:
            with self.subTest(url=wrong):
                self.assertEqual(self.client.get(wrong).status_code, 404)
        sheet = self.client.get(url + "kardex.xlsx")
        self.assertEqual(sheet.status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="materials.exported").exists())

    def test_inventory_export(self) -> None:
        self.client.force_login(self.mw.cw.viewer)
        response = self.client.get(self.base + "inventario.xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"PK"))

    def test_warehouses_are_created_renamed_and_deactivated(self) -> None:
        self.client.force_login(self.mw.cw.contributor)
        url = self.base + "bodegas/"
        self.assertRedirects(self.client.post(url, {"name": "Frente norte"}), url)
        north = Warehouse.objects.get(name="Frente norte")
        self.client.post(f"{url}{north.pk}/", {"accion": "renombrar", "name": "Frente sur"})
        self.client.post(f"{url}{north.pk}/", {"accion": "desactivar"})
        north.refresh_from_db()
        self.assertEqual((north.name, north.active), ("Frente sur", False))
        self.client.force_login(self.world.admin)
        page = self.client.post(f"{url}{self.mw.warehouse.pk}/", {"accion": "desactivar"},
                                follow=True)  # fmt: skip
        self.assertContains(page, "La bodega tiene inventario")
        self.client.force_login(self.mw.cw.viewer)
        self.assertEqual(self.client.post(url, {"name": "X"}).status_code, 403)
