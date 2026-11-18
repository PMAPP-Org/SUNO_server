"""Costos y Control con materiales (spec, 1.4 y 3.3): orígenes del libro, reverso, bandeja y
valor en bodega."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest import mock, skipUnless

from django.test import TestCase

from suno.core.models import (
    ConsumptionCut,
    CostEntry,
    CostScope,
    MaterialPurchase,
    PurchaseKind,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs import services as cost_services
from suno.costs.catalog import load_catalog
from suno.costs.ledger import ORIGINS, LedgerFilters, ledger_page
from suno.costs.services import CostRejected
from suno.costs.tests.factories import entry, item, support_blob
from suno.materials import cuts, purchases
from suno.materials.destination import INDIRECT
from suno.materials.purchases import PurchaseData, PurchaseLineData
from suno.matrix.tests.factories import plan

from .factories import issue, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)


@skip_without_db
class LedgerIntegrationTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.admin = self.mw.ctx()
        self.supervisor = self.mw.ctx(self.mw.world.supervisor)
        contributor = self.mw.ctx(self.mw.cw.contributor)
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])  # CM-001, a bodega
        direct = purchases.create_purchase(
            contributor, self.project,
            PurchaseData(str(PurchaseKind.DIRECT), None, "Arenas SAS", "", "invoice", "F-9",
                         date(2026, 3, 5),
                         (PurchaseLineData(self.mw.sand.pk, D("2"), D("100000.00"), INDIRECT),)),
        )  # fmt: skip
        MaterialPurchase.objects.filter(pk=direct.pk).update(attachment=support_blob("f"))
        self.direct = purchases.approve_purchase(self.supervisor, self.project, direct.pk)
        issue(self.mw, [(self.mw.cement, "7")], day=date(2026, 3, 10))
        cut = cuts.create_cut(contributor, self.project, date(2026, 3, 31))
        ConsumptionCut.objects.filter(pk=cut.pk).update(attachment=support_blob("r"))
        self.cut = cuts.approve_cut(self.supervisor, self.project, cut.pk)
        self.manual = entry(self.mw.world, status="approved", description="Manual",
                            scope=CostScope.INDIRECT)  # fmt: skip
        self.catalog = load_catalog(self.admin, self.project)

    def descriptions(self, origin: str) -> list[str]:
        page = ledger_page(self.admin, self.project, LedgerFilters.from_query({"origen": origin}),
                           page=1, catalog=self.catalog)  # fmt: skip
        return [line.description for line in page.lines]

    def test_origin_filters_and_labels(self) -> None:
        self.assertEqual(
            (ORIGINS["compras"], ORIGINS["cortes"], ORIGINS["ajustes"]),
            ("Compras", "Cortes", "Ajustes"),
        )
        self.assertEqual(self.descriptions("compras"), ["Compra CM-002 · ARE-01 Arena de río"])
        self.assertEqual(self.descriptions("cortes"),
                         ["Corte 1 de consumos · Indirecto (proyecto)"])  # fmt: skip
        self.assertEqual(self.descriptions("libro"), ["Manual"])
        page = ledger_page(self.admin, self.project,
                           LedgerFilters.from_query({"origen": "cortes"}), page=1,
                           catalog=self.catalog)  # fmt: skip
        line = page.lines[0]
        self.assertEqual((line.origin_label, line.source_label), ("Corte", "Corte 1"))
        self.assertEqual(
            line.source_url,
            f"/plataforma/proyectos/{self.project.pk}/materiales/cortes/{self.cut.pk}/",
        )

    def test_the_ledger_refuses_to_reverse_a_material_cost(self) -> None:
        cost = CostEntry.objects.get(material_purchase=self.direct)
        with self.assertRaises(CostRejected) as caught:
            cost_services.reverse(self.admin, self.project, cost.pk, "No")
        self.assertEqual(caught.exception.errors, [cost_services.FROM_MATERIALS])

    def test_pages_of_costs_and_control(self) -> None:
        self.client.force_login(self.mw.world.admin)
        base = f"/plataforma/proyectos/{self.project.pk}"
        cost = CostEntry.objects.get(consumption_cut=self.cut)
        detail = self.client.get(f"{base}/costos/{cost.pk}/")
        self.assertContains(detail, f"{base}/materiales/cortes/{self.cut.pk}/")
        self.assertNotContains(detail, "Reversar")
        purchases.create_purchase(
            self.mw.ctx(self.mw.cw.contributor), self.project,
            PurchaseData(str(PurchaseKind.WAREHOUSE), self.mw.warehouse.pk, "Ferretería", "",
                         "invoice", "F-10", date(2026, 4, 1),
                         (PurchaseLineData(self.mw.cement.pk, D("1"), D("1.00")),)),
        )  # fmt: skip
        inbox = self.client.get(f"{base}/costos/por-aprobar/")
        self.assertContains(inbox, "Compra CM-003")
        self.assertContains(inbox, f"{base}/materiales/compras/")
        # Sin pares planeados en la matriz no hay resultado del control: se planea uno.
        plan(self.mw.world, 5, item(self.mw.cw.budget, "2.1"), "8")
        control = self.client.get(f"{base}/control/?desglose=item&corte=2026-04-15")
        self.assertContains(control, "Valor en bodega del proyecto")
        self.assertContains(control, "2.976.000,00")
