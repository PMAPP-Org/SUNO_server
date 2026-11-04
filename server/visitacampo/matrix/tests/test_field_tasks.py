from __future__ import annotations

import unittest

from django.test import TestCase
from django.utils import timezone

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.field_tasks import field_tasks, list_version

from .factories import budget_world, item, locate, plan, quantity_row, quantity_visit, suno_levels

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
        self.world.approve(quantity_visit(self.world, 3, quantity_row(self.concrete, "4")))
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
