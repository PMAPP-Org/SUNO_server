"""El tope diario con dos escrituras a la vez en obras distintas (spec 2.2 y 4 "Tope diario").
Requiere `TransactionTestCase`: con la transacción envolvente de `TestCase` los hilos no verían
los datos ni habría concurrencia real (como `materials/tests/test_concurrency.py`).

Como los borradores cuentan para el tope, dos aprobaciones no pueden sumar por encima entre sí
(lo que aprueban ya contaba al guardarse). Las carreras que importan son dos guardados a la vez
y un guardado contra una aprobación: las dos se serializan por las filas de los trabajadores."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any
from unittest import mock, skipUnless

from django.db import connections
from django.db.models import Sum
from django.test import TransactionTestCase

from suno.core.models import TimesheetLine
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload
from suno.labor import approval, timesheets
from suno.labor.common import LaborRejected

from .factories import TODAY, WEEK, labor_world, line_data, other_project

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MON = WEEK[0]


@skip_without_db
class DailyCapRaceTests(TransactionTestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.other = other_project(self.lw)
        self.ctx = self.lw.ctx(self.lw.contributor)
        self.first = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        self.second = timesheets.create_timesheet(self.ctx, self.other, *WEEK)

    def race(self, *actions: Callable[[], object]) -> list[str]:
        barrier = threading.Barrier(len(actions))

        def run(action: Callable[[], object]) -> str:
            try:
                barrier.wait(timeout=10)
                action()
                return "ok"
            except LaborRejected as exc:
                return exc.errors[0]
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return sorted(f.result() for f in [pool.submit(run, a) for a in actions])

    def total(self) -> Decimal:
        found = TimesheetLine.objects.filter(worker=self.lw.pedro, worked_on=MON).aggregate(
            total=Sum("fraction")
        )["total"]
        return found or D("0")

    def save(self, project: Any, sheet: Any) -> Callable[[], object]:
        return lambda: timesheets.save_lines(
            self.ctx, project, sheet.pk, [line_data(self.lw.pedro, MON)], [MON]
        )

    def test_two_saves_in_two_projects_end_with_one_refused(self) -> None:
        results = self.race(self.save(self.lw.project, self.first),
                            self.save(self.other, self.second))  # fmt: skip
        self.assertEqual(results[1], "ok")
        self.assertIn("suma 2 jornales el 02/03/2026", results[0])
        self.assertEqual(self.total(), D("1"))

    def test_a_save_against_an_approval_ends_with_one_refused(self) -> None:
        self.save(self.lw.project, self.first)()
        timesheets.attach_signed(self.ctx, self.lw.project, self.first.pk, pdf_upload(), "f.pdf")
        approve = lambda: approval.approve_timesheet(  # noqa: E731
            self.lw.ctx(self.lw.world.supervisor), self.lw.project, self.first.pk
        )
        results = self.race(approve, self.save(self.other, self.second))
        # La aprobación ya contaba el borrador: el otro guardado es el que se rechaza.
        self.assertEqual(results[1], "ok")
        self.assertIn("suma 2 jornales", results[0])
        self.assertEqual(self.total(), D("1"))
