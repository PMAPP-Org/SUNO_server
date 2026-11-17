"""Ajustes en la web (spec, 3.2 "Ajustes"): conteo que trae lo esperado, "Recalcular" y firma."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from typing import Any
from unittest import mock

from django.test import TestCase, override_settings

from suno.core.models import CostEntry, InventoryAdjustment
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload

from .factories import material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TODAY = date(2026, 4, 15)


@skip_without_db
class AdjustmentPageTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        storage = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        storage.enable()
        self.addCleanup(storage.disable)
        self.mw = material_world()
        stock_in(self.mw, [(self.mw.cement, "150", "32500.00")])
        self.base = f"/plataforma/proyectos/{self.mw.project.pk}/materiales/ajustes/"
        self.client.force_login(self.mw.cw.contributor)

    def post(self, action: str, **rows: str) -> Any:
        return self.client.post(self.base + "nuevo/", {
            "accion": action, "warehouse": str(self.mw.warehouse.pk), "counted_on": "2026-03-31",
            "destination": "", "task": "", "notes": "", "lineas-TOTAL_FORMS": "1",
            "lineas-INITIAL_FORMS": "0", "lineas-0-material": "CEM-01",
            "lineas-0-counted": "145", "lineas-0-unit_cost": "", **rows,
        })  # fmt: skip

    def test_bring_the_stock_recalculate_and_save(self) -> None:
        brought = self.post("traer")
        self.assertContains(brought, 'value="CEM-01 · Cemento gris 50 kg"')
        recalculated = self.post("calcular")
        self.assertContains(recalculated, '<td class="num">150</td>', html=False)
        self.assertFalse(InventoryAdjustment.objects.exists())
        saved = self.post("guardar")
        adjustment = InventoryAdjustment.objects.get()
        self.assertRedirects(saved, f"{self.base}{adjustment.pk}/")

    def test_signed_count_approval_and_the_cost(self) -> None:
        self.post("guardar")
        adjustment = InventoryAdjustment.objects.get()
        url = f"{self.base}{adjustment.pk}/"
        self.client.post(url + "soporte/", {"file": pdf_upload()})
        self.client.force_login(self.mw.world.supervisor)
        self.assertRedirects(self.client.post(url + "aprobar/"), url)
        entry = CostEntry.objects.get(inventory_adjustment=adjustment)
        self.assertEqual(entry.amount, 162500)
        self.assertContains(self.client.get(url), "162.500,00")

    def test_bringing_the_stock_of_a_bad_warehouse_id_is_a_form_error(self) -> None:
        for bad in ("no-es-un-uuid", "00000000-0000-0000-0000-000000000000"):
            with self.subTest(warehouse=bad):
                page = self.post("traer", warehouse=bad)
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, "Bodega:")
        self.assertFalse(InventoryAdjustment.objects.exists())
