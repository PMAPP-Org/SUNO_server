"""Salidas, devoluciones y traslados en la web (spec, 3.2 "Salidas" y "Traslados")."""

from __future__ import annotations

import unittest
from datetime import date
from typing import Any
from unittest import mock

from django.test import TestCase

from suno.core.models import MaterialIssue, MaterialIssueLine, MaterialReturn, MaterialTransfer
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import balance_of, issue, make_warehouse, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TODAY = date(2026, 4, 15)


@skip_without_db
class MovementPageTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        self.base = f"/plataforma/proyectos/{self.mw.project.pk}/materiales/"
        self.client.force_login(self.mw.cw.contributor)

    def lines(self, quantity: str) -> dict[str, Any]:
        return {
            "lineas-TOTAL_FORMS": "1",
            "lineas-INITIAL_FORMS": "0",
            "lineas-0-material": "CEM-01",
            "lineas-0-quantity": quantity,
        }

    def issue_post(self, quantity: str, **extra: str) -> Any:
        return self.client.post(self.base + "salidas/", {
            "accion": "guardar", "warehouse": str(self.mw.warehouse.pk),
            "issued_on": "2026-03-10", "received_by": "Maestro", "destination": "2.1",
            "task": "", "notes": "", **self.lines(quantity), **extra,
        })  # fmt: skip

    def test_the_quick_issue_form_and_the_list(self) -> None:
        self.assertRedirects(self.issue_post("7"), self.base + "salidas/")
        created = MaterialIssue.objects.get()
        self.assertEqual((created.code, created.scope, created.total), ("SA-001", "item", 224000))
        shortage = self.issue_post("94")
        self.assertContains(shortage, "Solo hay 93 und de Cemento gris 50 kg en Almacén principal.")
        page = self.client.get(self.base + "salidas/?corte=sin")
        self.assertContains(page, "SA-001")
        self.assertContains(page, "2.1 Concreto zapatas")

    def test_return_from_the_issue_and_no_more_edits(self) -> None:
        out = issue(self.mw, [(self.mw.cement, "7")])
        line = MaterialIssueLine.objects.get(issue=out)
        url = f"{self.base}salidas/{out.pk}/"
        self.assertContains(self.client.get(url), "Registrar devolución")
        self.client.post(url + "devolver/", {"returned_on": "2026-03-12", "notes": "",
                                             f"devolver_{line.pk}": "2"})  # fmt: skip
        self.assertEqual(MaterialReturn.objects.get().code, "DV-001")
        self.assertEqual(balance_of(self.mw, self.mw.cement)[0], 95)
        edit = self.client.post(url + "editar/", {"accion": "guardar"}, follow=True)
        self.assertContains(edit, "La salida tiene devoluciones")

    def test_editing_and_deleting_an_issue(self) -> None:
        out = issue(self.mw, [(self.mw.cement, "7")])
        url = f"{self.base}salidas/{out.pk}/"
        self.assertContains(self.client.get(url + "editar/"), 'value="CEM-01 · Cemento gris 50 kg"')
        self.client.post(url + "editar/", {
            "accion": "guardar", "warehouse": str(self.mw.warehouse.pk),
            "issued_on": "2026-03-10", "received_by": "", "destination": "", "task": "",
            "notes": "", **self.lines("10"),
        })  # fmt: skip
        out.refresh_from_db()
        self.assertEqual(out.total, 320000)
        self.client.post(url + "borrar/")
        self.assertFalse(MaterialIssue.objects.exists())

    def test_transfers_and_the_issues_excel(self) -> None:
        north = make_warehouse(self.mw.world, "Frente norte")
        response = self.client.post(self.base + "traslados/", {
            "accion": "guardar", "source": str(self.mw.warehouse.pk), "target": str(north.pk),
            "moved_on": "2026-03-11", "notes": "", **self.lines("10"),
        })  # fmt: skip
        transfer = MaterialTransfer.objects.get()
        self.assertRedirects(response, f"{self.base}traslados/{transfer.pk}/")
        self.assertEqual(balance_of(self.mw, self.mw.cement, north)[0], 10)
        issue(self.mw, [(self.mw.cement, "1")])
        sheet = self.client.get(self.base + "salidas.xlsx?desde=2026-03-01&hasta=2026-03-31")
        self.assertEqual(sheet.status_code, 200)
        self.assertTrue(sheet.content.startswith(b"PK"))
