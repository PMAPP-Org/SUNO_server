"""Restricciones de la matriz y de la ubicación en la base de datos."""

from __future__ import annotations

import unittest
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from suno.budgets.tests.factories import apply_rows, make_budget
from suno.budgets.tests.samples import V1_ROWS
from suno.core.models import (
    BudgetItem,
    LocationLevel,
    LocationNode,
    QuantityPlan,
    QuantityPlanImport,
    QuantityPlanImportStatus,
    Visit,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.projects.tests.factories import make_field_world, make_visit

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class LocationModelTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        self.level = LocationLevel.objects.create(
            org=self.world.org, project=self.world.project, name="Tramo", depth=1
        )

    def test_depth_is_unique_per_project(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            LocationLevel.objects.create(
                org=self.world.org, project=self.world.project, name="Otro", depth=1
            )

    def test_depth_between_one_and_six(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            LocationLevel.objects.create(
                org=self.world.org, project=self.world.project, name="Siete", depth=7
            )

    def test_level_name_is_unique_ignoring_case(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            LocationLevel.objects.create(
                org=self.world.org, project=self.world.project, name="TRAMO", depth=2
            )

    def test_node_key_is_unique_per_parent_also_at_the_root(self) -> None:
        LocationNode.objects.create(
            org=self.world.org,
            project=self.world.project,
            level=self.level,
            name="Tramo 2",
            key="tramo 2",
            path="/a/",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            LocationNode.objects.create(
                org=self.world.org,
                project=self.world.project,
                level=self.level,
                name="TRAMO 2",
                key="tramo 2",
                path="/b/",
            )

    def test_task_location(self) -> None:
        node = LocationNode.objects.create(
            org=self.world.org,
            project=self.world.project,
            level=self.level,
            name="Tramo 1",
            key="tramo 1",
            path="/a/",
        )
        task = self.world.task(3)
        task.location = node
        task.save(update_fields=["location"])
        self.assertEqual(node.tasks.get(), task)


@skip_without_db
class QuantityPlanModelTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        budget = make_budget(self.world.project, self.world.admin)
        apply_rows(self.world.ctx(), budget, V1_ROWS)
        self.item = BudgetItem.objects.get(budget=budget, code="1.1")

    def _plan(self, quantity: str) -> QuantityPlan:
        return QuantityPlan.objects.create(
            org=self.world.org,
            project=self.world.project,
            task=self.world.task(3),
            item=self.item,
            planned_quantity=Decimal(quantity),
            updated_by=self.world.admin,
        )

    def test_pair_is_unique(self) -> None:
        self._plan("10")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._plan("5")

    def test_planned_quantity_not_negative(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._plan("-1")

    def test_import_number_iff_applied(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuantityPlanImport.objects.create(
                org=self.world.org,
                project=self.world.project,
                status=QuantityPlanImportStatus.PENDING,
                number=1,
                file_name="m.xlsx",
                source_file=b"x",
                uploaded_by=self.world.admin,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuantityPlanImport.objects.create(
                org=self.world.org,
                project=self.world.project,
                status=QuantityPlanImportStatus.APPLIED,
                file_name="m.xlsx",
                source_file=b"x",
                uploaded_by=self.world.admin,
            )


@skip_without_db
class VisitQuantitiesConstraintTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()

    def _save(self, visit: Visit, **values: object) -> None:
        for name, value in values.items():
            setattr(visit, name, value)
        with transaction.atomic():
            visit.save()

    def test_quantities_default_to_empty_list(self) -> None:
        visit = make_visit(self.world.org, self.world.agent)
        self.assertEqual(visit.quantities, [])

    def test_task_with_quantities_and_no_pct_is_valid(self) -> None:
        visit = make_visit(self.world.org, self.world.agent)
        row = {"item_id": "x", "location_id": None, "quantity": "1.0000", "note": ""}
        self._save(visit, task=self.world.task(3), progress_pct=None, quantities=[row])

    def test_task_without_pct_or_quantities_is_rejected(self) -> None:
        visit = make_visit(self.world.org, self.world.agent)
        with self.assertRaises(IntegrityError):
            self._save(visit, task=self.world.task(3), progress_pct=None, quantities=[])

    def test_quantities_without_task_are_rejected(self) -> None:
        visit = make_visit(self.world.org, self.world.agent)
        row = {"item_id": "x", "location_id": None, "quantity": "1.0000", "note": ""}
        with self.assertRaises(IntegrityError):
            self._save(visit, quantities=[row])

    def test_pct_range_still_checked(self) -> None:
        visit = make_visit(self.world.org, self.world.agent)
        with self.assertRaises(IntegrityError):
            self._save(visit, task=self.world.task(3), progress_pct=Decimal("101"))
