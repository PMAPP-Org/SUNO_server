from __future__ import annotations

import unittest
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from suno.budgets.tests.factories import apply_rows
from suno.budgets.tests.samples import SPLIT_ROWS
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.matrix.quantities import measured_progress, suno_pct_by_task
from suno.projects.field_tasks import field_tasks, list_version

from .factories import budget_world, item, locate, plan, quantity_row, quantity_record, suno_levels

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class FieldTasksMatrixTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        suno_levels(self.world)
        locate(self.world, 3, "Tramo 2")
        locate(self.world, 5, "Tramo 2", "PR 1")
        self.concrete = item(self.budget, "2.1")
        plan(self.world, 3, self.concrete, "10")
        self.world.responsible(3)
        self.world.responsible(2)

    def _items(self) -> dict[int, dict[str, object]]:
        """Las tareas del agente por uid."""
        ids = {str(self.world.task(uid).pk): uid for uid in (2, 3)}
        return {ids[i["id"]]: i for i in field_tasks(self.world.ctx(self.world.agent))}

    def test_planned_task(self) -> None:
        self.world.approve(quantity_record(self.world, 3, quantity_row(self.concrete, "4")))
        task = self._items()[3]
        self.assertEqual(task["measured_by"], "quantities")
        self.assertEqual(task["location"]["label"], "Tramo 2")  # type: ignore[index]
        self.assertEqual(
            [o["label"] for o in task["location_options"]],  # type: ignore[attr-defined]
            ["Tramo 2", "Tramo 2 / PR 1"],
        )
        self.assertEqual(
            task["planned_items"],
            [
                {
                    "item_id": str(self.concrete.pk),
                    "code": "2.1",
                    "description": "Concreto zapatas",
                    "unit": "m3",
                    "planned_quantity": 10.0,
                    "executed_quantity": 4.0,
                }
            ],
        )
        self.assertNotIn("unit_price", str(task))

    def test_task_without_plan(self) -> None:
        task = self._items()[2]
        self.assertEqual(task["measured_by"], "pct")
        self.assertIsNone(task["location"])
        self.assertEqual(task["location_options"], [])
        self.assertEqual(task["planned_items"], [])

    def test_retired_child_is_not_an_option(self) -> None:
        child = self.world.task(5).location
        assert child is not None
        child.retired_at = timezone.now()
        child.save(update_fields=["retired_at"])
        options = self._items()[3]["location_options"]
        self.assertEqual([o["label"] for o in options], ["Tramo 2"])  # type: ignore[attr-defined]

    def test_version_changes_with_the_plan(self) -> None:
        before = list_version(field_tasks(self.world.ctx(self.world.agent)))
        plan(self.world, 3, item(self.budget, "2.2"), "5")
        after = list_version(field_tasks(self.world.ctx(self.world.agent)))
        self.assertNotEqual(before, after)

    def test_queries_are_bounded(self) -> None:
        plan(self.world, 3, item(self.budget, "2.2"), "5")
        self.world.approve(quantity_record(self.world, 3, quantity_row(self.concrete, "4")))
        ctx = self.world.ctx(self.world.agent)
        with self.assertNumQueries(7):
            field_tasks(ctx)

    def test_zero_planned_quantity_is_not_offered(self) -> None:
        """Sin cantidad planeada el ítem no mide la tarea: tampoco se ofrece."""
        plan(self.world, 3, item(self.budget, "2.2"), "0")
        codes = [i["code"] for i in self._items()[3]["planned_items"]]  # type: ignore[attr-defined]
        self.assertEqual(codes, ["2.1"])


@skip_without_db
class ItemThatBecameAChapterTests(TestCase):
    """V1 tiene el 2.1 como ítem planeado en la tarea 1.2; la versión siguiente lo parte en
    2.1.1 y 2.1.2 y lo vuelve capítulo. La fila del plan sobrevive, pero ya no mide."""

    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        self.concrete = item(self.budget, "2.1")
        plan(self.world, 3, self.concrete, "10")
        self.world.responsible(3)
        apply_rows(self.world.ctx(), self.budget, SPLIT_ROWS)

    def test_field_tasks_do_not_offer_it(self) -> None:
        task = next(
            t
            for t in field_tasks(self.world.ctx(self.world.agent))
            if t["id"] == str(self.world.task(3).pk)
        )
        self.assertEqual(task["planned_items"], [])
        self.assertEqual(task["measured_by"], "pct")

    def test_progress_ignores_it(self) -> None:
        self.world.approve(
            quantity_record(self.world, 3, quantity_row(self.concrete, "4"), pct="30")
        )
        ctx = self.world.ctx()
        task_id = self.world.task(3).pk
        self.assertEqual(measured_progress(ctx, task_ids=[task_id]), {})
        self.assertEqual(suno_pct_by_task(ctx, task_ids=[task_id])[task_id], Decimal("30.00"))
