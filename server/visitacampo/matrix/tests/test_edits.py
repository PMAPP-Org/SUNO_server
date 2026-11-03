from __future__ import annotations

import unittest
from decimal import Decimal

from django.test import TestCase

from visitacampo.core.models import AuditEvent, QuantityPlan
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.edits import EditRejected, remove_plan, set_plan, set_task_location
from visitacampo.matrix.locations import load_tree

from .factories import (
    budget_world,
    item,
    project_levels_from_xml,
    quantity_row,
    quantity_visit,
    suno_levels,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class EditsTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        self.ctx = self.world.ctx()

    def test_set_and_update_plan(self) -> None:
        task = self.world.task(3)
        set_plan(self.ctx, task, budget_id=self.budget.pk, code="2.1", quantity="4,5")
        set_plan(self.ctx, task, budget_id=self.budget.pk, code="2.1", quantity="6")
        plan = QuantityPlan.objects.get()
        self.assertEqual(plan.planned_quantity, Decimal("6.0000"))
        events = AuditEvent.objects.filter(action="matrix.plan_changed").order_by("created_at")
        self.assertEqual(
            [(e.metadata["before"], e.metadata["after"]) for e in events],
            [(None, "4.5000"), ("4.5000", "6.0000")],
        )

    def test_rejections(self) -> None:
        summary = self.world.task(1)
        with self.assertRaisesMessage(EditRejected, "tareas de detalle"):
            set_plan(self.ctx, summary, budget_id=self.budget.pk, code="2.1", quantity="1")
        task = self.world.task(3)
        with self.assertRaisesMessage(EditRejected, "no existe"):
            set_plan(self.ctx, task, budget_id=self.budget.pk, code="9.9", quantity="1")
        with self.assertRaisesMessage(EditRejected, "capítulo"):
            set_plan(self.ctx, task, budget_id=self.budget.pk, code="2", quantity="1")
        with self.assertRaisesMessage(EditRejected, "número"):
            set_plan(self.ctx, task, budget_id=self.budget.pk, code="2.1", quantity="x")
        with self.assertRaisesMessage(EditRejected, "negativa"):
            set_plan(self.ctx, task, budget_id=self.budget.pk, code="2.1", quantity="-1")

    def test_remove_keeps_executed_at_zero(self) -> None:
        concrete = item(self.budget, "2.1")
        task = self.world.task(3)
        set_plan(self.ctx, task, budget_id=self.budget.pk, code="2.1", quantity="4")
        self.world.approve(quantity_visit(self.world, 3, quantity_row(concrete, "1")))
        self.assertFalse(remove_plan(self.ctx, task, concrete.pk))
        self.assertEqual(QuantityPlan.objects.get().planned_quantity, Decimal("0"))
        set_plan(self.ctx, task, budget_id=self.budget.pk, code="2.2", quantity="4")
        self.assertTrue(remove_plan(self.ctx, task, item(self.budget, "2.2").pk))
        self.assertEqual(QuantityPlan.objects.count(), 1)

    def test_task_location_in_suno_mode(self) -> None:
        suno_levels(self.world)
        task = self.world.task(3)
        set_task_location(self.ctx, task, "Tramo 2 / PR 12+300")
        task.refresh_from_db()
        label = load_tree(self.ctx, self.world.project).label(task.location_id)
        self.assertEqual(label, "Tramo 2 / PR 12+300")
        set_task_location(self.ctx, task, "")
        task.refresh_from_db()
        self.assertIsNone(task.location_id)
        with self.assertRaisesMessage(EditRejected, "3 niveles"):
            set_task_location(self.ctx, task, "a / b / c")

    def test_task_location_blocked_in_project_mode(self) -> None:
        project_levels_from_xml(self.world)
        with self.assertRaisesMessage(EditRejected, "MS Project"):
            set_task_location(self.ctx, self.world.task(3), "Tramo 1")
