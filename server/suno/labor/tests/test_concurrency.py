"""El tope diario con dos escrituras a la vez en obras distintas (spec 2.2 y 4 "Tope diario").
Requiere `TransactionTestCase`: con la transacción envolvente de `TestCase` los hilos no verían
los datos ni habría concurrencia real (como `materials/tests/test_concurrency.py`).

Como los borradores cuentan para el tope, dos aprobaciones no pueden sumar por encima entre sí
(lo que aprueban ya contaba al guardarse). Las carreras que importan son dos guardados a la vez
y un guardado contra una aprobación: las dos se serializan por las filas de los trabajadores."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock, skipUnless

from django.db import connections
from django.db.models import Sum
from django.test import TransactionTestCase

from suno.core.models import Project, ProjectWorker, TimesheetLine, Worker
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload
from suno.labor import approval, assignments, registry, timesheets
from suno.labor.assignments import AssignmentData
from suno.labor.common import LaborRejected

from .factories import TODAY, WEEK, labor_world, line_data, other_project

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MON = WEEK[0]
RETIRED = date(2026, 3, 1)


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


@skip_without_db
class RetireRaceTests(TransactionTestCase):
    """I5: retirar a un trabajador mientras se guarda su tareo o se le asigna una obra. El que
    guarda o asigna bloquea la fila del trabajador antes de validar; si no, los dos pasaban y
    quedaba tareo o una asignación abierta después del retiro."""

    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx(self.lw.contributor)
        self.admin = self.lw.ctx()

    def race(self, slow: Callable[[], object], fast: Callable[[], object]) -> list[str]:
        """`slow` empieza primero (y se demora dentro de su validación); `fast`, 0,2 s después."""
        barrier = threading.Barrier(2)

        def run(action: Callable[[], object], delay: float) -> str:
            try:
                barrier.wait(timeout=10)
                time.sleep(delay)
                action()
                return "ok"
            except LaborRejected as exc:
                return exc.errors[0]
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, slow, 0), pool.submit(run, fast, 0.2)]
            return [f.result() for f in futures]

    def retire(self) -> object:
        return registry.retire_worker(self.admin, self.lw.pedro.pk, RETIRED)

    def test_a_save_against_a_retirement_never_both_pass(self) -> None:
        sheet = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        coverage = timesheets._coverage

        def slow_coverage(*args: Any, **kwargs: Any) -> Any:
            found = coverage(*args, **kwargs)
            time.sleep(0.6)
            return found

        with mock.patch("suno.labor.timesheets._coverage", side_effect=slow_coverage):
            saved, retired = self.race(
                lambda: timesheets.save_lines(self.ctx, self.lw.project, sheet.pk,
                                              [line_data(self.lw.pedro, MON)], [MON]),
                self.retire,
            )  # fmt: skip
        self.assertEqual(saved, "ok")
        self.assertIn("Tiene tareo hasta el 02/03/2026", retired)
        self.assertIsNone(Worker.objects.get(pk=self.lw.pedro.pk).retired_on)

    def test_an_assignment_against_a_retirement_stays_inside_it(self) -> None:
        project = Project.objects.create(org=self.lw.world.org, name="Obra 3",
                                         created_by=self.lw.world.admin)  # fmt: skip
        check = assignments._check

        def slow_check(*args: Any, **kwargs: Any) -> Any:
            found = check(*args, **kwargs)
            time.sleep(0.6)
            return found

        data = AssignmentData(self.lw.pedro.pk, date(2026, 2, 1), None)
        with mock.patch("suno.labor.assignments._check", side_effect=slow_check):
            self.race(lambda: assignments.create_assignment(self.admin, project, data),
                      self.retire)  # fmt: skip
        self.assertEqual(Worker.objects.get(pk=self.lw.pedro.pk).retired_on, RETIRED)
        for row in ProjectWorker.objects.filter(worker=self.lw.pedro):
            self.assertIsNotNone(row.ends_on)
            self.assertLessEqual(row.ends_on, RETIRED)
