from __future__ import annotations

import unittest
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from suno.budgets.tests.factories import apply_rows
from suno.budgets.tests.samples import SPLIT_ROWS
from suno.core.models import ScheduleImport
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.matrix.locations import load_tree
from suno.matrix.reports import filter_item_rows, item_rows, location_rows

from .factories import budget_world, item, locate, plan, quantity_row, quantity_visit, suno_levels

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class ReportsTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        self.ctx = self.world.ctx()
        suno_levels(self.world)
        locate(self.world, 3, "Tramo 1")
        locate(self.world, 5, "Tramo 2", "PR 1")
        self.concrete = item(self.budget, "2.1")  # 10 m3 a 450.000
        plan(self.world, 3, self.concrete, "4")
        plan(self.world, 5, self.concrete, "8")
        self.world.approve(quantity_visit(self.world, 5, quantity_row(self.concrete, "2")))

    def test_item_rows(self) -> None:
        rows = {row.code: row for row in item_rows(self.ctx, self.world.project)}
        concrete = rows["2.1"]
        self.assertEqual(concrete.budgeted, Decimal("10.0000"))
        self.assertEqual(concrete.planned, Decimal("12.0000"))
        self.assertEqual(concrete.executed, Decimal("2"))
        self.assertEqual(concrete.status, "over")
        self.assertEqual(
            [(t.task.uid, t.location) for t in concrete.tasks],
            [(3, "Tramo 1"), (5, "Tramo 2 / PR 1")],
        )
        self.assertEqual(rows["1.1"].status, "under")  # sin asignar
        self.assertEqual(rows["1.1"].planned, Decimal(0))

    def test_filters(self) -> None:
        rows = item_rows(self.ctx, self.world.project)
        tramo_2 = load_tree(self.ctx, self.world.project).find(("Tramo 2",))
        filtered = filter_item_rows(
            rows, location_id=tramo_2, tree=load_tree(self.ctx, self.world.project)
        )
        self.assertEqual([r.code for r in filtered], ["2.1"])
        self.assertEqual(filtered[0].planned, Decimal("8.0000"))
        over = filter_item_rows(rows, status="over")
        self.assertEqual([r.code for r in over], ["2.1"])
        chapter = filter_item_rows(rows, chapter_code="1")
        self.assertEqual([r.code for r in chapter], ["1.1", "1.2"])

    def test_location_rows(self) -> None:
        rows = {row.label: row for row in location_rows(self.ctx, self.world.project)}
        self.assertEqual(rows["Tramo 2"].planned_value, Decimal("3600000.00"))
        self.assertEqual(rows["Tramo 2"].executed_value, Decimal("900000.00"))
        self.assertEqual(rows["Tramo 2"].pct, Decimal("25.00"))
        self.assertEqual(rows["Tramo 2 / PR 1"].depth, 2)
        self.assertEqual(rows["Tramo 1"].pct, Decimal("0.00"))
        self.assertEqual(rows["Total"].planned_value, Decimal("5400000.00"))

    def test_location_rows_skip_pairs_kept_at_zero(self) -> None:
        """Un par que quedó en 0 con ejecución no mide la tarea (`task_progress`): ni su
        planeado ni su ejecutado suman."""
        steel = item(self.budget, "2.2")
        plan(self.world, 3, steel, "0")
        self.world.approve(quantity_visit(self.world, 3, quantity_row(steel, "100"), hours=1))
        rows = {row.label: row for row in location_rows(self.ctx, self.world.project)}
        self.assertEqual(rows["Tramo 1"].executed_value, Decimal("0.00"))
        self.assertEqual(rows["Total"].planned_value, Decimal("5400000.00"))
        self.assertEqual(rows["Total"].executed_value, Decimal("900000.00"))

    def test_location_rows_skip_chapters_and_retired_tasks(self) -> None:
        steel = item(self.budget, "2.2")  # 800 kg a 4.200
        plan(self.world, 3, steel, "10")
        task = self.world.task(5)
        task.retired_in = ScheduleImport.objects.get(project=self.world.project, number=1)
        task.save(update_fields=["retired_in"])
        rows = {row.label: row for row in location_rows(self.ctx, self.world.project)}
        self.assertNotIn("Tramo 2", rows)
        self.assertEqual(rows["Total"].planned_value, Decimal("1842000.00"))
        apply_rows(self.ctx, self.budget, SPLIT_ROWS)  # el 2.1 pasa a ser capítulo
        rows = {row.label: row for row in location_rows(self.ctx, self.world.project)}
        self.assertEqual(rows["Tramo 1"].planned_value, Decimal("42000.00"))
        self.assertEqual(rows["Total"].planned_value, Decimal("42000.00"))

    def test_item_pct_is_capped_at_100(self) -> None:
        self.world.approve(
            quantity_visit(self.world, 5, quantity_row(self.concrete, "20"), hours=1)
        )
        rows = {row.code: row for row in item_rows(self.ctx, self.world.project)}
        self.assertEqual(rows["2.1"].executed, Decimal("22"))
        self.assertEqual(rows["2.1"].pct_executed, Decimal("100.00"))

    def test_item_that_became_a_chapter_is_not_retired(self) -> None:
        apply_rows(self.ctx, self.budget, SPLIT_ROWS)
        rows = {row.code: row for row in item_rows(self.ctx, self.world.project)}
        self.assertEqual(rows["2.1"].status, "chapter")
        self.assertEqual(rows["2.1"].status_label, "Ahora es capítulo")
        self.assertEqual(rows["2.1"].budget, self.budget)
        self.assertEqual(filter_item_rows(list(rows.values()), status="retired"), [])

    def test_query_count_does_not_grow(self) -> None:
        with CaptureQueriesContext(connection) as small:
            item_rows(self.ctx, self.world.project)
            location_rows(self.ctx, self.world.project)
        for code in ("1.1", "1.2", "2.2", "2.4"):
            plan(self.world, 2, item(self.budget, code), "1")
            plan(self.world, 6, item(self.budget, code), "1")
        with CaptureQueriesContext(connection) as large:
            item_rows(self.ctx, self.world.project)
            location_rows(self.ctx, self.world.project)
        self.assertEqual(len(small), len(large))
