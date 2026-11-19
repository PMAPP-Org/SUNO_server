"""Cuadrícula semanal y vista por día (spec 3.2), listas y consultas de número fijo."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import assignable_tasks, load_catalog
from suno.labor import grid, queries, timesheets

from .factories import TODAY, WEEK, bare_timesheet, labor_world, line_data, to_item

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
