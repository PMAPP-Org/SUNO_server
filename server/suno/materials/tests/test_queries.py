"""Consultas de la pestaña "Materiales" (spec, 3.2, 3.3 y 4 "Carga"): número fijo de consultas."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from suno.core.models import PurchaseKind
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.materials import purchases, queries
from suno.materials.purchases import PurchaseData, PurchaseLineData
from suno.materials.queries import DocumentFilters, InventoryFilters

from .factories import issue, make_warehouse, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)


@skip_without_db
class QueryTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.ctx = self.mw.ctx()
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00"), (self.mw.sand, "10", "100000.00")])
        stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])
        issue(self.mw, [(self.mw.cement, "7")], day=date(2026, 3, 10))
        issue(self.mw, [(self.mw.cement, "3")], day=date(2026, 4, 10))
        make_warehouse(self.mw.world, "Frente norte")

    def test_inventory_in_two_queries_with_filters(self) -> None:
        with self.assertNumQueries(2):
            page = queries.inventory_page(self.ctx, self.project, InventoryFilters())
        self.assertEqual([row.balance.material.code for row in page.rows], ["ARE-01", "CEM-01"])
        self.assertEqual((page.count, page.value), (2, D("5550000.00")))
        cement = page.rows[1]
        self.assertEqual((cement.balance.quantity, cement.average), (D("140"), D("32500.000000")))
        found = queries.inventory_page(
            self.ctx, self.project, InventoryFilters.from_query({"texto": "arena"})
        )
        self.assertEqual([row.balance.material.code for row in found.rows], ["ARE-01"])
        by_category = queries.inventory_page(
            self.ctx, self.project, InventoryFilters.from_query({"categoria": "Cementos"})
        )
        self.assertEqual(by_category.count, 1)
        self.assertEqual(queries.stock_value(self.ctx, self.project), D("5550000.00"))

    def test_kardex_with_running_balance_and_documents(self) -> None:
        with self.assertNumQueries(1):
            sheet = queries.kardex(self.ctx, self.project, self.mw.warehouse, self.mw.cement)
        self.assertEqual([row.document for row in sheet.rows],
                         ["CM-001", "CM-002", "SA-001", "SA-002"])  # fmt: skip
        self.assertEqual(
            [(row.balance_quantity, row.balance_value) for row in sheet.rows],
            [(D("100"), D("3200000.00")), (D("150"), D("4875000.00")),
             (D("143"), D("4647500.00")), (D("140"), D("4550000.00"))],
        )  # fmt: skip
        self.assertEqual((sheet.rows[2].entry, sheet.rows[2].exit), (None, D("7")))
        later = queries.kardex(
            self.ctx, self.project, self.mw.warehouse, self.mw.cement, start=date(2026, 4, 1)
        )
        self.assertEqual([row.document for row in later.rows], ["SA-002"])
        self.assertEqual(later.rows[0].balance_quantity, D("140"))  # el saldo sigue la secuencia

    def test_lists_alerts_and_inbox(self) -> None:
        purchases.create_purchase(
            self.mw.ctx(self.mw.cw.contributor), self.project,
            PurchaseData(str(PurchaseKind.WAREHOUSE), self.mw.warehouse.pk, "Ferretería", "",
                         "invoice", "F-1", date(2026, 4, 12),
                         (PurchaseLineData(self.mw.cement.pk, D("1"), D("1.00")),)),
        )  # fmt: skip
        rows, more = queries.purchase_rows(
            self.ctx, self.project, DocumentFilters.from_query({"estado": "draft"})
        )
        self.assertEqual(([row.code for row in rows], more), (["CM-003"], False))
        issues, _ = queries.issue_rows(
            self.ctx, self.project, DocumentFilters.from_query({"desde": "2026-04-01"})
        )
        self.assertEqual([row.code for row in issues], ["SA-002"])
        with self.assertNumQueries(4):
            alerts = queries.tab_alerts(self.ctx, self.project)
        self.assertEqual(
            [alert.text for alert in alerts],
            ["1 salida sin cortar con más de 31 días (la más vieja, del 10/03/2026).",
             "1 compra en borrador por aprobar."],
        )  # fmt: skip
        supervisor = self.mw.ctx(self.mw.world.supervisor)
        with self.assertNumQueries(3):
            self.assertEqual(queries.inbox_count(supervisor, self.project), 1)
        self.assertEqual(queries.inbox_count(self.mw.ctx(self.mw.cw.contributor), self.project), 0)
        self.assertEqual([p.code for p in queries.inbox_items(supervisor, self.project).purchases],
                         ["CM-003"])  # fmt: skip
