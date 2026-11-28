"""Cuadrícula semanal y vista por día (spec 3.2), listas y consultas de número fijo."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from suno.core.models import BudgetItemVersion, Timesheet, WorkerWage
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import assignable_tasks, load_catalog
from suno.costs.tests.factories import item
from suno.labor import grid, queries, timesheets
from suno.labor.calc import is_stale
from suno.labor.queries import stale_timesheets

from .factories import (
    TODAY,
    WEEK,
    bare_line,
    bare_timesheet,
    labor_world,
    line_data,
    to_item,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MON, TUE = WEEK[0], date(2026, 3, 3)


@skip_without_db
class GridTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx()
        self.sheet = timesheets.create_timesheet(self.ctx, self.lw.project, date(2026, 3, 4),
                                                 date(2026, 3, 17))  # fmt: skip
        timesheets.save_lines(self.ctx, self.lw.project, self.sheet.pk, [
            line_data(self.lw.pedro, date(2026, 3, 4)),
            line_data(self.lw.pedro, date(2026, 3, 5), "0.5"),
            line_data(self.lw.pedro, date(2026, 3, 5), "0.5", to_item(self.lw)),
            line_data(self.lw.pedro, date(2026, 3, 10), "1"),
        ], timesheets.period_days(date(2026, 3, 4), date(2026, 3, 17)))  # fmt: skip
        self.sheet.refresh_from_db()
        self.catalog = load_catalog(self.ctx, self.lw.project)
        self.tasks = assignable_tasks(self.ctx, self.lw.project)

    def test_weeks_are_monday_to_sunday_clipped_to_the_period(self) -> None:
        first = grid.week_of(self.sheet, None)
        self.assertEqual(first.days[0], date(2026, 3, 4))  # miércoles: empieza la planilla
        self.assertEqual(first.days[-1], date(2026, 3, 8))  # domingo
        self.assertIsNone(first.previous)
        self.assertEqual(first.next, date(2026, 3, 9))
        last = grid.week_of(self.sheet, date(2026, 3, 16))
        self.assertEqual(last.days, (date(2026, 3, 16), date(2026, 3, 17)))
        self.assertEqual(last.previous, date(2026, 3, 9))
        self.assertIsNone(last.next)
        self.assertEqual(grid.week_of(self.sheet, date(2027, 1, 1)).days[0], date(2026, 3, 4))

    def test_rows_by_worker_and_destination_with_totals(self) -> None:
        with self.assertNumQueries(6):
            built = grid.build_grid(self.ctx, self.lw.project, self.sheet, self.catalog,
                                    self.tasks, date(2026, 3, 2))  # fmt: skip
        rows = [(r.worker.split(" · ")[0], r.destination, r.cells) for r in built.rows]
        self.assertEqual(rows, [
            ("María Gómez", "", ("", "", "", "", "")),        # asignada, sin tareo esta semana
            ("Pedro Pérez", "", ("1", "0,5", "", "", "")),     # indirecto
            ("Pedro Pérez", built.rows[2].destination, ("", "0,5", "", "", "")),
        ])  # fmt: skip
        self.assertTrue(built.rows[2].destination.startswith("2.1"))
        self.assertEqual(built.day_totals, (D("1"), D("1"), D("0"), D("0"), D("0")))
        # Totales del periodo completo (también la semana siguiente).
        self.assertEqual(built.days, D("3.0000"))
        self.assertEqual(built.total, D("364800.00"))
        by_worker = {label.split(" · ")[0]: days for label, days, _ in built.by_worker}
        self.assertEqual(by_worker, {"Pedro Pérez": D("3.0000")})
        self.assertFalse(built.changed)

    def test_without_a_stored_summary_the_grid_and_the_list_agree(self) -> None:
        """M1 (#106): sin huella guardada la aprobación rechaza; la ficha y la lista avisan."""
        Timesheet.objects.filter(pk=self.sheet.pk).update(summary={})
        self.sheet.refresh_from_db()
        built = grid.build_grid(self.ctx, self.lw.project, self.sheet, self.catalog, self.tasks,
                                date(2026, 3, 2))  # fmt: skip
        self.assertTrue(built.changed)
        self.assertEqual(stale_timesheets(self.ctx, [self.sheet]), {self.sheet.pk})
        evaluation = timesheets.evaluate(self.ctx, self.sheet, self.catalog)
        self.assertTrue(is_stale(evaluation.summary, self.sheet.summary))
        self.assertFalse(is_stale(evaluation.summary, {
            "fingerprint": evaluation.summary.fingerprint()}))  # fmt: skip

    def test_the_period_table_prints_the_stored_destination_labels(self) -> None:
        """M6 (#88): el PDF y el Excel nombran el destino como el resumen guardado y el libro;
        un resumen sin clave (anterior) usa el nombre vigente."""
        BudgetItemVersion.objects.filter(item=item(self.lw.cw.budget, "2.1")).update(
            description="Concreto ciclópeo"
        )
        catalog = load_catalog(self.ctx, self.lw.project)
        labels = {row[1] for row in grid.period_table(self.ctx, self.sheet, catalog).rows}
        self.assertIn("2.1 Concreto zapatas", labels)
        self.assertNotIn("2.1 Concreto ciclópeo", labels)
        legacy = {**self.sheet.summary, "groups": [
            {k: v for k, v in group.items() if k != "key"} for group in self.sheet.summary["groups"]
        ]}  # fmt: skip
        Timesheet.objects.filter(pk=self.sheet.pk).update(summary=legacy)
        self.sheet.refresh_from_db()
        labels = {row[1] for row in grid.period_table(self.ctx, self.sheet, catalog).rows}
        self.assertIn("2.1 Concreto ciclópeo", labels)

    def test_day_view_lists_the_rows_of_one_day(self) -> None:
        built = grid.build_grid(self.ctx, self.lw.project, self.sheet, self.catalog, self.tasks,
                                date(2026, 3, 2))  # fmt: skip
        day = grid.day_rows(built, date(2026, 3, 5))
        self.assertEqual(
            [(r.worker.split(" · ")[0], value) for r, value in day],
            [("María Gómez", ""), ("Pedro Pérez", "0,5"), ("Pedro Pérez", "0,5")],
        )

    def test_lists(self) -> None:
        bare_timesheet(self.lw, number=2, status="approved", start=date(2026, 2, 2),
                       end=date(2026, 2, 8))  # fmt: skip
        with self.assertNumQueries(2):
            page = queries.timesheet_page(self.ctx, self.lw.project, page=1)
            codes = [row.code for row in page]
        self.assertEqual(codes, ["TA-002", "TA-001"])
        self.assertEqual(queries.pending_count(self.ctx, self.lw.project), 0)
        with self.assertNumQueries(1):
            assigned = list(queries.assignment_rows(self.ctx, self.lw.project))
        self.assertEqual([a.worker.full_name for a in assigned], ["María Gómez", "Pedro Pérez"])
        result = queries.worker_page(self.ctx, queries.WorkerFilters(text="pedro"), page=1)
        self.assertEqual([w.full_name for w in result], ["Pedro Pérez"])
        self.assertEqual(queries.positions(self.ctx), ["Ayudante", "Oficial"])


@skip_without_db
class FrozenGridTests(TestCase):
    """Una planilla aprobada o anulada muestra lo congelado, no el jornal de hoy (C2)."""

    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx()
        self.catalog = load_catalog(self.ctx, self.lw.project)
        self.tasks = assignable_tasks(self.ctx, self.lw.project)

    def frozen_sheet(self, status: str, number: int = 1) -> Timesheet:
        sheet = bare_timesheet(self.lw, status=status, number=number)
        bare_line(sheet, self.lw.pedro, MON, frozen=("70000.00", "1.4000", "98000.00"))
        sheet.summary = {"fingerprint": {"lines": 1, "days": "1.0000", "total": "98000.00"}}
        sheet.save()
        # Hoy el jornal vigente es otro: no debe aparecer en la planilla congelada.
        WorkerWage.objects.filter(worker=self.lw.pedro).update(daily_wage=D("90000.00"))
        return sheet

    def test_grid_and_period_table_use_frozen_values(self) -> None:
        for number, status in enumerate(("approved", "reversed"), start=1):
            with self.subTest(status=status):
                sheet = self.frozen_sheet(status, number)
                built = grid.build_grid(
                    self.ctx, self.lw.project, sheet, self.catalog, self.tasks, MON
                )
                pedro = [r for r in built.rows if r.worker.startswith("Pedro")][0]
                self.assertEqual(pedro.cost, D("98000.00"))
                self.assertEqual(built.total, D("98000.00"))
                self.assertEqual([a for _, _, a in built.by_worker], [D("98000.00")])
                self.assertFalse(built.changed)
                table = grid.period_table(self.ctx, sheet, self.catalog)
                self.assertEqual([row[4] for row in table.rows], [D("98000.00")])
