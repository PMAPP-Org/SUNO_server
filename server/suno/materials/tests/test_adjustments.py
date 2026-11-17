"""Ajuste por conteo físico (spec, 2.5 y 4 "Ajuste")."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.test import TestCase

from suno.core.models import (
    CostEntry,
    InventoryAdjustment,
    InventoryAdjustmentLine,
    MovementKind,
    StockMovement,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import support_blob
from suno.materials import adjustments
from suno.materials.adjustments import CHANGED_COUNT, AdjustmentData, AdjustmentLineData
from suno.materials.common import SELF_DECISION, MaterialRejected
from suno.materials.destination import INDIRECT
from suno.materials.kardex import Stock

from .factories import (
    assert_consistent,
    balance_of,
    draft_purchase,
    issue,
    material_world,
    stock_in,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)


@skip_without_db
class AdjustmentTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.contributor = self.mw.ctx(self.mw.cw.contributor)
        self.supervisor = self.mw.ctx(self.mw.world.supervisor)
        self.admin = self.mw.ctx()
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])  # 150 / 4.875.000,00

    def count(self, *lines: AdjustmentLineData) -> InventoryAdjustment:
        adjustment = adjustments.create_adjustment(
            self.contributor,
            self.project,
            AdjustmentData(self.mw.warehouse.pk, date(2026, 3, 31), INDIRECT, lines),
        )
        InventoryAdjustment.objects.filter(pk=adjustment.pk).update(
            attachment=support_blob("acta de conteo")
        )
        return adjustment

    def test_a_shortage_leaves_at_the_average_and_is_an_approved_cost(self) -> None:
        draft = self.count(AdjustmentLineData(self.mw.cement.pk, D("145")))
        line = InventoryAdjustmentLine.objects.get(adjustment=draft)
        self.assertEqual((draft.code, line.expected, line.difference),
                         ("AJ-001", D("150"), D("-5")))  # fmt: skip
        approved = adjustments.approve_adjustment(self.supervisor, self.project, draft.pk)
        self.assertEqual(approved.shortage_total, D("162500.00"))
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("145"), D("4712500.00")))
        entry = CostEntry.objects.get(inventory_adjustment=approved)
        self.assertEqual(
            (entry.amount, entry.incurred_on, entry.scope, entry.document_type, entry.decided_by),
            (D("162500.00"), date(2026, 3, 31), "indirect", "inventory_count",
             self.mw.world.supervisor),
        )  # fmt: skip
        assert_consistent(self, self.mw)

    def test_a_surplus_enters_at_the_average_or_the_last_entry(self) -> None:
        stock_in(self.mw, [(self.mw.sand, "10", "100000.00")])
        issue(self.mw, [(self.mw.sand, "10")])  # arena en cero; última entrada 100.000
        draft = self.count(
            AdjustmentLineData(self.mw.cement.pk, D("152")),
            AdjustmentLineData(self.mw.sand.pk, D("2")),
            AdjustmentLineData(self.mw.steel.pk, D("5"), D("4100.00")),
        )
        approved = adjustments.approve_adjustment(self.supervisor, self.project, draft.pk)
        self.assertEqual(approved.surplus_total, D("65000.00") + D("200000.00") + D("20500.00"))
        self.assertEqual(balance_of(self.mw, self.mw.sand), (D("2"), D("200000.00")))
        self.assertEqual(balance_of(self.mw, self.mw.steel), (D("5"), D("20500.00")))
        self.assertFalse(CostEntry.objects.filter(inventory_adjustment=approved).exists())
        assert_consistent(self, self.mw)

    def test_a_surplus_without_balance_nor_entries_needs_a_unit_cost(self) -> None:
        with self.assertRaises(MaterialRejected) as caught:
            self.count(AdjustmentLineData(self.mw.steel.pk, D("5")))
        self.assertEqual(
            caught.exception.errors,
            ["Varilla de 1/2 pulgada: no hay saldo ni entradas de este material en la bodega; "
             "escribe el costo unitario del sobrante."],
        )  # fmt: skip
        with self.assertRaises(MaterialRejected) as caught:
            self.count(AdjustmentLineData(self.mw.cement.pk, D("150")))
        self.assertEqual(
            caught.exception.errors,
            ["El conteo coincide con el inventario: no hay nada que ajustar."],
        )

    def test_the_stock_changed_since_the_count(self) -> None:
        draft = self.count(AdjustmentLineData(self.mw.cement.pk, D("145")))
        issue(self.mw, [(self.mw.cement, "1")], day=date(2026, 4, 1))
        with self.assertRaises(MaterialRejected) as caught:
            adjustments.approve_adjustment(self.supervisor, self.project, draft.pk)
        self.assertEqual(caught.exception.errors, [CHANGED_COUNT])
        with self.assertRaises(PermissionDenied):  # el Colaborador no aprueba
            adjustments.approve_adjustment(self.contributor, self.project, draft.pk)
        adjustments.update_adjustment(
            self.supervisor, self.project, draft.pk,
            AdjustmentData(self.mw.warehouse.pk, date(2026, 4, 2), INDIRECT,
                           (AdjustmentLineData(self.mw.cement.pk, D("145")),)),
        )  # fmt: skip
        InventoryAdjustment.objects.filter(pk=draft.pk).update(attachment=support_blob("acta"))
        with self.assertRaises(MaterialRejected) as caught:
            adjustments.approve_adjustment(self.supervisor, self.project, draft.pk)
        self.assertEqual(caught.exception.errors, [SELF_DECISION])

    def test_editing_recomputes_the_expected_and_removes_the_signed_count(self) -> None:
        draft = self.count(AdjustmentLineData(self.mw.cement.pk, D("145")))
        issue(self.mw, [(self.mw.cement, "1")], day=date(2026, 4, 1))
        edited = adjustments.update_adjustment(
            self.contributor, self.project, draft.pk,
            AdjustmentData(self.mw.warehouse.pk, date(2026, 4, 2), INDIRECT,
                           (AdjustmentLineData(self.mw.cement.pk, D("145")),)),
        )  # fmt: skip
        self.assertIsNone(edited.attachment_id)
        self.assertEqual(InventoryAdjustmentLine.objects.get(adjustment=edited).expected, D("149"))

    def test_voiding_puts_back_the_shortage_and_takes_out_the_surplus(self) -> None:
        shortage = self.count(AdjustmentLineData(self.mw.cement.pk, D("145")))
        adjustments.approve_adjustment(self.supervisor, self.project, shortage.pk)
        adjustments.void_adjustment(self.admin, self.project, shortage.pk, "Conteo errado")
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("150"), D("4875000.00")))
        entries = CostEntry.objects.filter(inventory_adjustment=shortage)
        self.assertEqual(sum(e.amount for e in entries), D("0"))
        surplus = self.count(AdjustmentLineData(self.mw.cement.pk, D("152")))
        adjustments.approve_adjustment(self.supervisor, self.project, surplus.pk)
        issue(self.mw, [(self.mw.cement, "151")], day=date(2026, 4, 1))
        with self.assertRaises(MaterialRejected) as caught:
            adjustments.void_adjustment(self.admin, self.project, surplus.pk, "Error")
        self.assertEqual(
            caught.exception.errors,
            ["No se puede anular: de Cemento gris 50 kg quedan 1 und en la bodega y el sobrante "
             "fue 2."],
        )  # fmt: skip
        self.assertEqual(StockMovement.objects.filter(kind="adjustment_void").count(), 1)
        assert_consistent(self, self.mw)

    def test_a_last_entry_at_zero_cost_is_a_valid_cost_for_a_surplus(self) -> None:
        pair = (self.mw.warehouse.pk, self.mw.steel.pk)
        with transaction.atomic():
            stock = Stock(self.admin, self.project, [pair])
            stock.add(str(MovementKind.PURCHASE), *pair, D("5"), D("0.00"), date(2026, 3, 2),
                      purchase=draft_purchase(self.mw, number=99))  # fmt: skip
            stock.save()
        issue(self.mw, [(self.mw.steel, "5")])  # varilla en cero; última entrada a 0
        draft = self.count(AdjustmentLineData(self.mw.steel.pk, D("2")))  # sin costo dado
        approved = adjustments.approve_adjustment(self.supervisor, self.project, draft.pk)
        line = InventoryAdjustmentLine.objects.get(adjustment=approved)
        self.assertEqual((line.unit_cost, line.amount), (D("0.000000"), D("0.00")))
        self.assertEqual(balance_of(self.mw, self.mw.steel), (D("2"), D("0.00")))
        assert_consistent(self, self.mw)

    def test_voiding_a_surplus_can_leave_quantity_without_value(self) -> None:
        surplus = self.count(AdjustmentLineData(self.mw.steel.pk, D("5"), D("4100.00")))
        adjustments.approve_adjustment(self.supervisor, self.project, surplus.pk)
        stock_in(self.mw, [(self.mw.steel, "10", "100.00")])  # 15 / 21.500,00
        issue(self.mw, [(self.mw.steel, "9")])  # 9 × 1.433,333333 = 12.900,00
        self.assertEqual(balance_of(self.mw, self.mw.steel), (D("6"), D("8600.00")))
        adjustments.void_adjustment(self.admin, self.project, surplus.pk, "Conteo errado")
        self.assertEqual(balance_of(self.mw, self.mw.steel), (D("1"), D("0.00")))
        void = StockMovement.objects.get(kind="adjustment_void")
        self.assertEqual((void.quantity, void.value), (D("-5"), D("-8600.00")))
        assert_consistent(self, self.mw)
