"""Cargador del control (costos, pieza 5): queries fijas y qué entra."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from django.test import TestCase

from suno.budgets.tests.factories import apply_rows, make_budget
from suno.budgets.tests.samples import V1_ROWS, V2_ROWS
from suno.control.earned_value import ControlInput, Execution, PercentTask, TaskWindow
from suno.control.loader import load_control_input
from suno.control.periods import MONTH
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.matrix.tests.factories import (
    budget_world,
    import_schedule,
    item,
    locate,
    plan,
    quantity_record,
    quantity_row,
    suno_levels,
)
from suno.projects.tests.factories import T0, make_field_world, make_record
from suno.projects.tests.samples import v2

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
CUT = date(2026, 3, 20)


@skip_without_db
class LoaderTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        self.ctx = self.world.ctx()
        suno_levels(self.world)
        locate(self.world, 3, "Tramo 1")
        self.concrete = item(self.budget, "2.1")
        plan(self.world, 3, self.concrete, "4")
        plan(self.world, 5, self.concrete, "8")
        self.world.approve(quantity_record(self.world, 5, quantity_row(self.concrete, "2")))

    def load(self, cut: date = CUT) -> ControlInput:
        return load_control_input(self.ctx, self.world.project, cut=cut, kind=MONTH)

    def test_query_count_does_not_grow_with_the_data(self) -> None:
        with self.assertNumQueries(9):
            self.load()
        location = item(self.budget, "1.1")
        plan(self.world, 2, location, "50")
        for hours in (1, 2, 3):
            self.world.approve(
                quantity_record(self.world, 2, quantity_row(location, "5"), hours=hours)
            )
        with self.assertNumQueries(9):
            self.load()

    def test_windows_items_and_executions(self) -> None:
        data = self.load()
        task = self.world.task(3)
        self.assertEqual(
            data.tasks[task.pk],
            TaskWindow(
                task_id=task.pk,
                label="1.2 Descapote",
                location="Tramo 1",
                start=date(2026, 3, 9),
                finish=date(2026, 3, 13),
                from_baseline=True,
                milestone=False,
                order=task.sort_order,
            ),
        )
        priced = data.items[self.concrete.pk]
        self.assertEqual(
            (priced.label, priced.unit, priced.unit_price, priced.chapter),
            ("2.1 Concreto zapatas", "m3", D("450000"), "2 Estructura"),
        )
        self.assertEqual(
            data.executions,
            [Execution(self.world.task(5).pk, self.concrete.pk, date(2026, 3, 10), D("2"))],
        )
        self.assertEqual(sorted(p.planned for p in data.plans), [D("4"), D("8")])
        self.assertTrue(data.has_budget)
        self.assertEqual(data.contractual_direct_cost, D("9130000.00"))
        self.assertFalse(data.calendar.is_working(date(2026, 3, 23)))  # festivo del XML

    def test_only_live_validated_records_until_the_cut(self) -> None:
        row = quantity_row(self.concrete, "1")
        quantity_record(self.world, 5, row, hours=1)  # sin revisar
        self.world.reject(quantity_record(self.world, 5, row, hours=2))
        self.world.approve(
            make_record(
                self.world.org,
                self.world.agent,
                task=self.world.task(5),
                at=T0,
                deleted=True,
                quantities=[row],
            )
        )
        self.world.approve(quantity_record(self.world, 5, row, hours=24 * 30))  # 9 de abril
        # 21 de marzo a las 03:00 UTC: en Bogotá todavía es el 20.
        self.world.approve(
            quantity_record(self.world, 5, quantity_row(self.concrete, "3"), hours=258)
        )
        days = sorted((e.day, e.quantity) for e in self.load().executions)
        self.assertEqual(days, [(date(2026, 3, 10), D("2")), (date(2026, 3, 20), D("3"))])

    def test_current_prices_and_retired_items(self) -> None:
        apply_rows(self.ctx, self.budget, V2_ROWS)
        data = self.load()
        self.assertEqual(data.items[self.concrete.pk].unit_price, D("470000"))
        retired = item(self.budget, "1.2")
        self.assertNotIn(retired.pk, data.items)
        self.assertEqual(data.item_labels[retired.pk], "1.2")
        self.assertEqual(data.contractual_direct_cost, D("9130000.00"))

    def test_several_budgets_name_their_items(self) -> None:
        other = make_budget(self.world.project, self.world.admin, name="Interventoría", code="C-02")
        apply_rows(self.ctx, other, V1_ROWS)
        data = self.load()
        self.assertEqual(data.items[self.concrete.pk].chapter, "2 Estructura · Contrato de obra")
        self.assertEqual(data.contractual_direct_cost, D("18260000.00"))

    def test_retired_tasks_are_left_out(self) -> None:
        import_schedule(self.world, v2())
        self.assertNotIn(self.world.task(5).pk, self.load().tasks)

    def test_percent_tasks_are_the_leaves_without_plan(self) -> None:
        self.world.approve(self.world.record(2, "40"))
        self.assertEqual(self.load().percent_tasks, [PercentTask("1.1 Localización", D("40"))])

    def test_project_without_budget(self) -> None:
        world = make_field_world("Sin presupuesto")
        ctx = world.ctx()
        with self.assertNumQueries(8):
            data = load_control_input(ctx, world.project, cut=CUT, kind=MONTH)
        self.assertFalse(data.has_budget)
        self.assertEqual(data.items, {})
        self.assertEqual(data.plans, [])
        self.assertIsNone(data.contractual_direct_cost)
