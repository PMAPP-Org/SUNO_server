from __future__ import annotations

import unittest
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from suno.budgets.tests.factories import apply_rows, make_budget, stage_rows
from suno.budgets.tests.samples import SPLIT_ROWS, V2_ROWS
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.matrix.quantities import measured_progress, suno_pct_by_task
from suno.projects.field_tasks import field_tasks, list_version, project_items
from suno.projects.tests.factories import make_field_world

from .factories import budget_world, item, locate, plan, quantity_record, quantity_row, suno_levels

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
        before = list_version(field_tasks(self.world.ctx(self.world.agent)), [])
        plan(self.world, 3, item(self.budget, "2.2"), "5")
        after = list_version(field_tasks(self.world.ctx(self.world.agent)), [])
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


@skip_without_db
class ProjectItemsTests(TestCase):
    """Pieza 4b: el catálogo para "Agregar ítem no planeado"."""

    def setUp(self) -> None:
        self.world, self.budget = budget_world()

    def catalog(self) -> list[dict[str, object]]:
        return project_items(self.world.ctx(self.world.agent), [self.world.project.pk])

    def test_current_items_without_chapters_nor_prices(self) -> None:
        catalog = self.catalog()
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["project_id"], str(self.world.project.pk))
        items = catalog[0]["items"]
        self.assertEqual(
            [i["code"] for i in items],  # type: ignore[attr-defined]
            ["1.1", "1.2", "2.1", "2.2", "2.4"],
        )
        self.assertEqual(
            items[2],  # type: ignore[index]
            {
                "item_id": str(item(self.budget, "2.1").pk),
                "code": "2.1",
                "description": "Concreto zapatas",
                "unit": "m3",
            },
        )
        self.assertNotIn("price", str(catalog))
        self.assertNotIn("total", str(catalog))

    def test_follows_the_current_version(self) -> None:
        apply_rows(self.world.ctx(), self.budget, V2_ROWS)
        codes = [i["code"] for i in self.catalog()[0]["items"]]  # type: ignore[attr-defined]
        self.assertEqual(codes, ["1.1", "2.1", "2.2", "2.3", "2.4"])

    def test_every_budget_of_the_project(self) -> None:
        other = make_budget(self.world.project, self.world.admin, code="C-02", name="Adicional")
        rows: list[list[object]] = [["9", "Extras"], ["9.1", "Señalización", "un", 4, 1000, 4000]]
        apply_rows(self.world.ctx(), other, rows)
        codes = [i["code"] for i in self.catalog()[0]["items"]]  # type: ignore[attr-defined]
        self.assertEqual(codes, ["1.1", "1.2", "2.1", "2.2", "2.4", "9.1"])

    def test_project_without_current_budget(self) -> None:
        world = make_field_world("Sin presupuesto")
        catalog = project_items(world.ctx(world.agent), [world.project.pk])
        self.assertEqual(catalog, [{"project_id": str(world.project.pk), "items": []}])

    def test_no_projects_no_queries(self) -> None:
        ctx = self.world.ctx(self.world.agent)
        with self.assertNumQueries(0):
            self.assertEqual(project_items(ctx, []), [])

    def test_two_queries(self) -> None:
        ctx = self.world.ctx(self.world.agent)
        with self.assertNumQueries(2):
            project_items(ctx, [self.world.project.pk])

    def test_the_catalog_is_part_of_the_version(self) -> None:
        self.world.responsible(3)
        ctx = self.world.ctx(self.world.agent)
        tasks = field_tasks(ctx)
        before = list_version(tasks, project_items(ctx, [self.world.project.pk]))
        apply_rows(self.world.ctx(), self.budget, V2_ROWS)
        after = list_version(tasks, project_items(ctx, [self.world.project.pk]))
        self.assertNotEqual(before, after)

    def test_a_pending_version_is_not_the_current_one(self) -> None:
        stage_rows(self.world.ctx(), self.budget, V2_ROWS)
        codes = [i["code"] for i in self.catalog()[0]["items"]]  # type: ignore[attr-defined]
        self.assertEqual(codes, ["1.1", "1.2", "2.1", "2.2", "2.4"])

    def test_another_organization_s_catalog_never_appears(self) -> None:
        other, _ = budget_world("Otra")
        catalog = project_items(self.world.ctx(self.world.agent), [other.project.pk])
        self.assertEqual(catalog, [{"project_id": str(other.project.pk), "items": []}])
