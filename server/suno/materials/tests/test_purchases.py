"""Compras (spec, 2.2, 2.3 y 2.7): a bodega, directas, aprobación, rechazo y anulación."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.test import TestCase, override_settings

from suno.core.models import (
    CostEntry,
    CostEntryItem,
    CostKind,
    CostScope,
    MaterialPurchase,
    MovementKind,
    PurchaseKind,
    StockMovement,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import item, pdf_upload, support_blob
from suno.materials import purchases
from suno.materials.common import SELF_DECISION, MaterialRejected
from suno.materials.destination import INDIRECT, DestinationData
from suno.materials.kardex import Stock
from suno.materials.purchases import PurchaseData, PurchaseLineData
from suno.materials.warehouses import set_warehouse_active

from .factories import assert_consistent, balance_of, bare_issue, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)


@skip_without_db
class PurchaseTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.contributor = self.mw.ctx(self.mw.cw.contributor)
        self.supervisor = self.mw.ctx(self.mw.world.supervisor)
        self.admin = self.mw.ctx()
        self.concrete = item(self.mw.cw.budget, "2.1")

    def data(self, *lines: PurchaseLineData, **fields: Any) -> PurchaseData:
        values: dict[str, Any] = {
            "kind": str(PurchaseKind.DIRECT),
            "warehouse_id": None,
            "supplier": "Ferretería El Constructor",
            "supplier_tax_id": "900123456",
            "document_type": "invoice",
            "document_number": "FE-200",
            "purchased_on": date(2026, 3, 5),
            "lines": lines,
        }
        values.update(fields)
        return PurchaseData(**values)

    def direct(self) -> MaterialPurchase:
        to_concrete = DestinationData(
            str(CostScope.ITEM), (self.concrete.pk,), task_id=self.mw.world.task(5).pk
        )
        return purchases.create_purchase(
            self.contributor,
            self.project,
            self.data(
                PurchaseLineData(self.mw.sand.pk, D("5"), D("120000.00"), to_concrete),
                PurchaseLineData(self.mw.cement.pk, D("10"), D("32000.00"), INDIRECT),
            ),
        )

    def supported(self, purchase: MaterialPurchase) -> None:
        MaterialPurchase.objects.filter(pk=purchase.pk).update(attachment=support_blob())

    def test_a_warehouse_purchase_enters_the_stock_when_approved(self) -> None:
        first = stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        second = stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])
        self.assertEqual((first.code, second.code), ("CM-001", "CM-002"))
        self.assertEqual((second.status, second.total), ("approved", D("1675000.00")))
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("150"), D("4875000.00")))
        self.assertFalse(CostEntry.objects.exists())  # el costo llega con las salidas
        assert_consistent(self, self.mw)

    def test_a_direct_purchase_writes_one_approved_cost_per_line(self) -> None:
        purchase = self.direct()
        self.supported(purchase)
        purchases.approve_purchase(self.supervisor, self.project, purchase.pk)
        entries = CostEntry.objects.filter(material_purchase=purchase).order_by("-amount")
        self.assertEqual([e.amount for e in entries], [D("600000.00"), D("320000.00")])
        sand = entries[0]
        self.assertEqual(
            (sand.description, sand.third_party, sand.kind, sand.incurred_on),
            ("Compra CM-001 · ARE-01 Arena de río", "Ferretería El Constructor (NIT 900123456)",
             CostKind.MATERIALS, date(2026, 3, 5)),
        )  # fmt: skip
        self.assertEqual((sand.created_by, sand.decided_by),
                         (self.mw.cw.contributor, self.mw.world.supervisor))  # fmt: skip
        self.assertEqual((sand.scope, sand.task_id), ("item", self.mw.world.task(5).pk))
        self.assertEqual(sand.chapter_id, item(self.mw.cw.budget, "2").pk)
        self.assertEqual(CostEntryItem.objects.get(entry=sand).item_id, self.concrete.pk)
        self.assertEqual(entries[1].scope, "indirect")
        self.assertFalse(StockMovement.objects.exists())

    def test_what_a_purchase_needs(self) -> None:
        with self.assertRaises(MaterialRejected) as caught:
            purchases.create_purchase(
                self.contributor,
                self.project,
                self.data(
                    PurchaseLineData(self.mw.sand.pk, D("0"), D("1000.001")),
                    PurchaseLineData(self.mw.cement.pk, D("2"), D("1000.00"), INDIRECT),
                    kind=str(PurchaseKind.WAREHOUSE),
                    supplier=" ",
                    purchased_on=date(2026, 4, 16),
                ),
            )
        self.assertEqual(
            caught.exception.errors,
            [
                "Elige una bodega activa del proyecto.",
                "Escribe el proveedor.",
                "La fecha de la compra no puede ser futura ni anterior a 2000.",
                "Línea 1: La cantidad es mayor que cero, con máximo 4 decimales.",
                "Línea 1: El valor unitario es mayor que cero, en pesos y centavos.",
                "Línea 2: Una compra a bodega no lleva destino: el costo llega con las salidas.",
            ],
        )
        with self.assertRaises(MaterialRejected) as caught:
            purchases.create_purchase(
                self.contributor,
                self.project,
                self.data(PurchaseLineData(self.mw.sand.pk, D("1"), D("1000.00"))),
            )
        self.assertEqual(caught.exception.errors, ["Línea 1: Escribe el destino."])

    def test_nobody_approves_their_own_purchase_and_the_invoice_is_needed(self) -> None:
        purchase = self.direct()
        with self.assertRaises(MaterialRejected) as caught:
            purchases.approve_purchase(self.supervisor, self.project, purchase.pk)
        self.assertEqual(caught.exception.errors, ["Adjunta la factura antes de aprobar."])
        with self.assertRaises(PermissionDenied):
            purchases.approve_purchase(self.contributor, self.project, purchase.pk)
        purchases.update_purchase(
            self.admin, self.project, purchase.pk,
            self.data(PurchaseLineData(self.mw.cement.pk, D("10"), D("32000.00"), INDIRECT)),
        )  # fmt: skip
        self.supported(purchase)
        with self.assertRaises(MaterialRejected) as caught:
            purchases.approve_purchase(self.admin, self.project, purchase.pk)
        self.assertEqual(caught.exception.errors, [SELF_DECISION])
        approved = purchases.approve_purchase(self.supervisor, self.project, purchase.pk)
        self.assertEqual((approved.status, approved.total), ("approved", D("320000.00")))

    def test_a_rejection_has_a_reason_and_its_author_resubmits(self) -> None:
        purchase = self.direct()
        with self.assertRaises(MaterialRejected) as caught:
            purchases.reject_purchase(self.supervisor, self.project, purchase.pk, " ")
        self.assertEqual(caught.exception.errors, ["Escribe el motivo del rechazo."])
        purchases.reject_purchase(self.supervisor, self.project, purchase.pk, "Falta el NIT")
        with self.assertRaises(PermissionDenied):
            purchases.update_purchase(
                self.supervisor, self.project, purchase.pk,
                self.data(PurchaseLineData(self.mw.cement.pk, D("1"), D("1.00"), INDIRECT)),
            )  # fmt: skip
        again = purchases.update_purchase(
            self.contributor, self.project, purchase.pk,
            self.data(PurchaseLineData(self.mw.cement.pk, D("1"), D("1.00"), INDIRECT)),
        )  # fmt: skip
        self.assertEqual((again.status, again.decided_by_id, again.decision_note),
                         ("draft", None, ""))  # fmt: skip

    def test_attaching_the_invoice_is_not_an_edit(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=tmp.name):
            purchase = self.direct()
            purchases.attach_support(
                self.supervisor, self.project, purchase.pk, pdf_upload(), "factura.pdf"
            )
            purchase.refresh_from_db()
            self.assertEqual((purchase.updated_by, purchase.attachment_name),
                             (self.mw.cw.contributor, "factura.pdf"))  # fmt: skip
            purchases.approve_purchase(self.supervisor, self.project, purchase.pk)

    def take(self, quantity: str) -> None:
        """Saca cemento con el motor (las salidas por servicio llegan en la Task 8)."""
        pair = (self.mw.warehouse.pk, self.mw.cement.pk)
        with transaction.atomic():
            stock = Stock(self.admin, self.project, [pair])
            stock.take(str(MovementKind.ISSUE), *pair, D(quantity), date(2026, 3, 10),
                       issue=bare_issue(self.mw))  # fmt: skip
            stock.save()

    def test_voiding_a_warehouse_purchase_needs_the_stock(self) -> None:
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        second = stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])
        self.take("120")
        with self.assertRaises(PermissionDenied):  # el Revisor no anula
            purchases.void_purchase(self.supervisor, self.project, second.pk, "Error")
        with self.assertRaises(MaterialRejected) as caught:
            purchases.void_purchase(self.admin, self.project, second.pk, "Factura errada")
        self.assertEqual(
            caught.exception.errors,
            ["No se puede anular: de Cemento gris 50 kg quedan 30 und en la bodega y la compra "
             "trajo 50."],
        )  # fmt: skip

    def test_voiding_a_warehouse_purchase_takes_out_what_it_brought(self) -> None:
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        second = stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])
        voided = purchases.void_purchase(self.admin, self.project, second.pk, "Factura errada")
        self.assertEqual((voided.status, voided.void_note), ("voided", "Factura errada"))
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("100"), D("3200000.00")))
        movement = StockMovement.objects.get(kind="purchase_void")
        self.assertEqual((movement.moved_on, movement.value), (TODAY, D("-1675000.00")))
        assert_consistent(self, self.mw)

    def test_voiding_a_direct_purchase_reverses_its_costs_today(self) -> None:
        purchase = self.direct()
        self.supported(purchase)
        purchases.approve_purchase(self.supervisor, self.project, purchase.pk)
        purchases.void_purchase(self.admin, self.project, purchase.pk, "Proveedor equivocado")
        reversals = CostEntry.objects.filter(material_purchase=purchase, reverses__isnull=False)
        self.assertEqual(reversals.count(), 2)
        self.assertEqual({r.incurred_on for r in reversals}, {TODAY})
        self.assertEqual(
            sum(e.amount for e in CostEntry.objects.filter(material_purchase=purchase)), D("0")
        )
        with self.assertRaises(MaterialRejected):
            purchases.void_purchase(self.admin, self.project, purchase.pk, "Otra vez")

    def test_drafts_are_deleted_and_an_inactive_warehouse_blocks_approval(self) -> None:
        purchase = purchases.create_purchase(
            self.contributor, self.project,
            self.data(PurchaseLineData(self.mw.cement.pk, D("1"), D("1000.00")),
                      kind=str(PurchaseKind.WAREHOUSE), warehouse_id=self.mw.warehouse.pk),
        )  # fmt: skip
        self.supported(purchase)
        set_warehouse_active(self.admin, self.project, self.mw.warehouse.pk, False)
        with self.assertRaises(MaterialRejected) as caught:
            purchases.approve_purchase(self.supervisor, self.project, purchase.pk)
        self.assertEqual(
            caught.exception.errors,
            ["La bodega de la compra está inactiva: actívala o cambia la bodega."],
        )
        purchases.delete_purchase(self.contributor, self.project, purchase.pk)
        self.assertFalse(MaterialPurchase.objects.exists())
