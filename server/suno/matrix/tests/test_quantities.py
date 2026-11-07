from __future__ import annotations

import unittest
from decimal import Decimal

from django.test import TestCase

from suno.budgets.tests.factories import apply_rows
from suno.budgets.tests.samples import V2_ROWS
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.matrix.quantities import (
    executed_by_pair,
    item_facts,
    measured_progress,
    suno_pct_by_task,
)

from .factories import budget_world, item, plan, quantity_record, quantity_row

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class QuantitiesTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        self.ctx = self.world.ctx()
        self.concrete = item(self.budget, "2.1")  # 450.000 / m3 en V1
        self.steel = item(self.budget, "2.2")  # 4.200 / kg en V1
        plan(self.world, 3, self.concrete, "10")
        plan(self.world, 3, self.steel, "500")

    def test_only_validated_records_count(self) -> None:
        approved = quantity_record(self.world, 3, quantity_row(self.concrete, "4"))
        self.world.approve(approved)
        rejected = quantity_record(self.world, 3, quantity_row(self.concrete, "3"), hours=1)
        self.world.reject(rejected)
        quantity_record(self.world, 3, quantity_row(self.concrete, "2"), hours=2)  # sin revisar
        executed = executed_by_pair(self.ctx, project_ids=[self.world.project.pk])
        self.assertEqual(executed, {(self.world.task(3).pk, self.concrete.pk): Decimal("4")})

    def test_adjustments_add_up(self) -> None:
        for hours, qty in enumerate(("4", "-1")):
            record = quantity_record(
                self.world, 3, quantity_row(self.concrete, qty, note="ajuste"), hours=hours
            )
            self.world.approve(record)
        executed = executed_by_pair(self.ctx, task_ids=[self.world.task(3).pk])
        self.assertEqual(executed[(self.world.task(3).pk, self.concrete.pk)], Decimal("3"))

    def test_measured_progress_and_suno_pct(self) -> None:
        record = quantity_record(
            self.world, 3, quantity_row(self.concrete, "5"), quantity_row(self.steel, "500")
        )
        self.world.approve(record)
        manual = self.world.record(2, "40")  # tarea sin plan: % manual
        self.world.approve(manual)
        # (5 × 450.000 + 500 × 4.200) / (10 × 450.000 + 500 × 4.200) = 4.350.000 / 6.600.000
        progress = measured_progress(self.ctx, project_ids=[self.world.project.pk])
        self.assertEqual(progress[self.world.task(3).pk].pct, Decimal("65.91"))
        pct = suno_pct_by_task(self.ctx, project_ids=[self.world.project.pk])
        self.assertEqual(pct[self.world.task(3).pk], Decimal("65.91"))
        self.assertEqual(pct[self.world.task(2).pk], Decimal("40"))

    def test_quantities_override_manual_pct_on_planned_tasks(self) -> None:
        self.world.approve(self.world.record(3, "90"))
        pct = suno_pct_by_task(self.ctx, task_ids=[self.world.task(3).pk])
        self.assertEqual(pct[self.world.task(3).pk], Decimal("0.00"))

    def test_item_facts_use_last_applied_version_containing_the_item(self) -> None:
        apply_rows(self.ctx, self.budget, V2_ROWS)  # 2.1 sube a 470.000; 1.2 se elimina
        removed = item(self.budget, "1.2")
        facts = item_facts(self.ctx, [self.concrete.pk, removed.pk])
        self.assertEqual(facts[self.concrete.pk].unit_price, Decimal("470000.00"))
        self.assertEqual(facts[removed.pk].unit_price, Decimal("20000.00"))
        self.assertEqual(facts[removed.pk].code, "1.2")

    def test_query_count(self) -> None:
        with self.assertNumQueries(4):
            suno_pct_by_task(self.ctx, project_ids=[self.world.project.pk])

    def test_empty_ids(self) -> None:
        self.assertEqual(suno_pct_by_task(self.ctx, task_ids=[]), {})
