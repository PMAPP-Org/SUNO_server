"""Saldos y kárdex (spec, 1.3 y 2.1): bloqueo, secuencia, promedio y deshacer."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.test import TestCase

from suno.core.models import MovementKind, StockBalance, StockMovement
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.materials.common import MaterialRejected
from suno.materials.kardex import Stock, lock_balances

from .factories import (
    assert_consistent,
    balance_of,
    bare_issue,
    draft_purchase,
    make_warehouse,
    material_world,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
DAY = date(2026, 3, 2)
IN, OUT = str(MovementKind.PURCHASE), str(MovementKind.ISSUE)


@skip_without_db
class StockTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.ctx = self.mw.ctx()
        self.purchase = draft_purchase(self.mw)
        self.issue = bare_issue(self.mw)
        self.pair = (self.mw.warehouse.pk, self.mw.sand.pk)

    def stock(self) -> Stock:
        return Stock(self.ctx, self.mw.project, [self.pair])

    def test_entries_and_exits_at_the_moving_average(self) -> None:
        with transaction.atomic():
            stock = self.stock()
            stock.add(IN, *self.pair, D("10"), D("1000000.00"), DAY, purchase=self.purchase)
            stock.add(IN, *self.pair, D("20"), D("2200000.00"), DAY, purchase=self.purchase)
            out = stock.take(OUT, *self.pair, D("7"), DAY, issue=self.issue)
            stock.save()
        self.assertEqual((out.unit_cost, out.value), (D("106666.666667"), D("-746666.67")))
        self.assertEqual(balance_of(self.mw, self.mw.sand), (D("23"), D("2453333.33")))
        self.assertEqual(
            list(StockMovement.objects.order_by("sequence").values_list("sequence", flat=True)),
            [1, 2, 3],
        )
        self.assertEqual(StockBalance.objects.get().last_sequence, 3)
        assert_consistent(self, self.mw)

    def test_taking_everything_leaves_exactly_zero(self) -> None:
        with transaction.atomic():
            stock = self.stock()
            stock.add(IN, *self.pair, D("30"), D("3200000.00"), DAY, purchase=self.purchase)
            stock.take(OUT, *self.pair, D("7"), DAY, issue=self.issue)
            stock.take(OUT, *self.pair, D("23"), DAY, issue=self.issue)
            stock.save()
        self.assertEqual(balance_of(self.mw, self.mw.sand), (D("0"), D("0.00")))
        assert_consistent(self, self.mw)

    def test_a_shortage_is_refused_with_the_quantity_unit_and_warehouse(self) -> None:
        with self.assertRaises(MaterialRejected) as caught, transaction.atomic():
            stock = self.stock()
            stock.add(IN, *self.pair, D("3"), D("300000.00"), DAY, purchase=self.purchase)
            stock.take(OUT, *self.pair, D("5"), DAY, issue=self.issue)
        self.assertEqual(
            caught.exception.errors, ["Solo hay 3 m3 de Arena de río en Almacén principal."]
        )
        self.assertFalse(StockMovement.objects.exists())

    def test_restore_gives_back_what_the_movements_took_and_deletes_them(self) -> None:
        with transaction.atomic():
            stock = self.stock()
            stock.add(IN, *self.pair, D("10"), D("1000000.00"), DAY, purchase=self.purchase)
            stock.take(OUT, *self.pair, D("4"), DAY, issue=self.issue)
            stock.save()
        taken = list(StockMovement.objects.filter(issue=self.issue))
        with transaction.atomic():
            stock = self.stock()
            stock.restore(taken)
            stock.save()
        self.assertEqual(balance_of(self.mw, self.mw.sand), (D("10"), D("1000000.00")))
        self.assertFalse(StockMovement.objects.filter(issue=self.issue).exists())
        self.assertEqual(StockBalance.objects.get().last_sequence, 2)
        assert_consistent(self, self.mw)

    def test_lock_creates_the_missing_balances_once(self) -> None:
        north = make_warehouse(self.mw.world, "Frente norte")
        pairs = [(north.pk, self.mw.cement.pk), self.pair, self.pair]
        with transaction.atomic():
            first = lock_balances(self.ctx, self.mw.project, pairs)
        with transaction.atomic():
            again = lock_balances(self.ctx, self.mw.project, pairs)
        self.assertEqual(set(first), {(north.pk, self.mw.cement.pk), self.pair})
        self.assertEqual({row.pk for row in first.values()}, {row.pk for row in again.values()})
        self.assertEqual(StockBalance.objects.count(), 2)
        self.assertEqual(lock_balances(self.ctx, self.mw.project, []), {})
