"""Dos salidas a la vez sobre el mismo saldo (spec, 4 "Inventario negativo"): una pasa y la
otra se rechaza. Requiere `TransactionTestCase`: con la transacción envolvente de `TestCase`
los hilos no verían los datos ni habría concurrencia real (como `attachments/tests/
test_concurrency.py`)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from unittest import mock, skipUnless

from django.db import connections
from django.test import TransactionTestCase

from suno.core.models import ConsumptionCut, MaterialIssue
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import support_blob
from suno.materials import cuts
from suno.materials.common import MaterialRejected

from .factories import assert_consistent, balance_of, issue, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = skipUnless(SKIP_REASON is None, SKIP_REASON or "")

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


@skip_without_db
class CutApprovalRaceTests(TransactionTestCase):
    """Aprobar un corte mientras otra persona registra una salida dentro de su periodo: el
    bloqueo del proyecto los ordena y nunca queda una salida del periodo fuera del corte."""

    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=date(2026, 4, 15))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        stock_in(self.mw, [(self.mw.cement, "10", "32000.00")])
        issue(self.mw, [(self.mw.cement, "1")], day=date(2026, 3, 10))
        self.cut = cuts.create_cut(self.mw.ctx(self.mw.cw.contributor), self.mw.project,
                                   date(2026, 3, 31))  # fmt: skip
        ConsumptionCut.objects.filter(pk=self.cut.pk).update(attachment=support_blob("r"))

    def _run(self, barrier: threading.Barrier, action: str) -> str:
        try:
            barrier.wait(timeout=10)
            if action == "aprobar":
                cuts.approve_cut(self.mw.ctx(self.mw.world.supervisor), self.mw.project,
                                 self.cut.pk)  # fmt: skip
            else:
                issue(self.mw, [(self.mw.cement, "1")], day=date(2026, 3, 20))
            return f"{action}: ok"
        except MaterialRejected as exc:
            return f"{action}: {exc.errors[0]}"
        finally:
            connections.close_all()

    def test_approval_and_an_issue_inside_the_period_never_both_pass(self) -> None:
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._run, barrier, a) for a in ("aprobar", "salida")]
            results = sorted(future.result() for future in futures)
        self.assertEqual(sum(result.endswith(": ok") for result in results), 1, results)
        cut = ConsumptionCut.objects.get(pk=self.cut.pk)
        in_period = MaterialIssue.objects.filter(
            project=self.mw.project, issued_on__range=(cut.period_start, cut.period_end)
        )
        if cut.status == "approved":
            self.assertFalse(in_period.filter(cut__isnull=True).exists())
            self.assertIn("La fecha cae en un periodo ya cortado", results[1])
        else:
            self.assertEqual(results[0], f"aprobar: {cuts.CHANGED}")
            self.assertEqual(in_period.count(), 2)
        assert_consistent(self, self.mw)
