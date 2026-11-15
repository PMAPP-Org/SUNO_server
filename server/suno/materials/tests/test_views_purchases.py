"""Compras en la web (spec, 3.2 "Compras"): formulario sin JavaScript, ficha y decisiones."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from typing import Any
from unittest import mock

from django.test import TestCase, override_settings

from suno.core.models import MaterialPurchase
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
class PurchasePageTests(TestCase):
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
        self.base = f"/plataforma/proyectos/{self.mw.project.pk}/materiales/compras/"
        self.client.force_login(self.mw.cw.contributor)

    def post(self, action: str = "guardar", **overrides: Any) -> Any:
        data: dict[str, Any] = {
            "accion": action,
            "kind": "warehouse",
            "warehouse": str(self.mw.warehouse.pk),
            "supplier": "Ferretería El Constructor",
            "supplier_tax_id": "900123456",
            "document_type": "invoice",
            "document_number": "FE-1",
            "purchased_on": "2026-03-02",
            "lineas-TOTAL_FORMS": "2",
            "lineas-INITIAL_FORMS": "0",
            "lineas-0-material": "CEM-01 · Cemento gris 50 kg",
            "lineas-0-quantity": "50",
            "lineas-0-unit_price": "32000",
            "lineas-1-material": "",
        }
        data.update(overrides)
        return self.client.post(self.base + "nueva/", data)

    def test_new_purchase_with_add_lines_and_recalculate(self) -> None:
        more = self.post("lineas")
        self.assertContains(more, 'name="lineas-4-material"')
        again = self.post("calcular")
        self.assertContains(again, "1.600.000,00")
        self.assertFalse(MaterialPurchase.objects.exists())
        saved = self.post()
        purchase = MaterialPurchase.objects.get()
        self.assertRedirects(saved, f"{self.base}{purchase.pk}/")
        self.assertEqual(purchase.total, 1600000)

    def test_a_direct_purchase_shows_destination_errors(self) -> None:
        page = self.post(kind="direct", warehouse="", **{"lineas-0-destination": "9.9"})
        self.assertContains(
            page, "Línea 1: «9.9» no es un ítem ni un capítulo del presupuesto vigente."
        )
        ok = self.post(kind="direct", warehouse="",
                       **{"lineas-0-destination": "2.1", "lineas-0-task": "2.1"})  # fmt: skip
        self.assertEqual(ok.status_code, 302)
        detail = self.client.get(ok["Location"])
        self.assertContains(detail, "2.1 Concreto zapatas · tarea 2.1 Columnas")

    def test_invoice_approval_and_void_from_the_sheet(self) -> None:
        self.post()
        purchase = MaterialPurchase.objects.get()
        url = f"{self.base}{purchase.pk}/"
        self.client.post(url + "soporte/", {"file": pdf_upload()})
        self.client.force_login(self.mw.world.supervisor)
        page = self.client.get(url)
        self.assertContains(page, "Aprobar")
        self.assertRedirects(self.client.post(url + "aprobar/"), url)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, "approved")
        self.client.force_login(self.mw.world.admin)
        self.client.post(url + "anular/", {"motivo": "Factura duplicada"})
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, "voided")
        self.assertEqual(self.client.get(url + "soporte/ver/").status_code, 302)

    def test_list_filters_and_isolation(self) -> None:
        stock_in(self.mw, [(self.mw.cement, "1", "1000.00")])
        self.post()
        drafts = self.client.get(self.base + "?estado=draft")
        self.assertContains(drafts, "CM-002")
        self.assertNotContains(drafts, "CM-001")
        other = material_world("Otra constructora")
        foreign = stock_in(other, [(other.cement, "1", "1000.00")])
        self.assertEqual(self.client.get(f"{self.base}{foreign.pk}/").status_code, 404)
        self.client.force_login(self.mw.cw.viewer)
        self.assertEqual(self.client.get(self.base + "nueva/").status_code, 403)
