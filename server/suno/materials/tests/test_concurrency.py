"""Dos salidas a la vez sobre el mismo saldo (spec, 4 "Inventario negativo"): una pasa y la
otra se rechaza. Requiere `TransactionTestCase`: con la transacción envolvente de `TestCase`
los hilos no verían los datos ni habría concurrencia real (como `attachments/tests/
test_concurrency.py`)."""

from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from unittest import mock

from django.db import connections
from django.test import TransactionTestCase

from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.materials.common import MaterialRejected

from .factories import assert_consistent, balance_of, issue, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


@skip_without_db
class ConcurrentIssueTests(TransactionTestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=date(2026, 4, 15))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        stock_in(self.mw, [(self.mw.cement, "10", "32000.00")])

    def _issue(self, barrier: threading.Barrier) -> str:
        try:
            barrier.wait(timeout=10)
            issue(self.mw, [(self.mw.cement, "7")])
            return "ok"
        except MaterialRejected as exc:
            return exc.errors[0]
        finally:
            connections.close_all()

    def test_two_issues_on_the_same_balance_end_with_one_refused(self) -> None:
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                future.result() for future in [pool.submit(self._issue, barrier) for _ in range(2)]
            )
        self.assertEqual(
            results, ["Solo hay 3 und de Cemento gris 50 kg en Almacén principal.", "ok"]
        )
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("3"), D("96000.00")))
        assert_consistent(self, self.mw)
