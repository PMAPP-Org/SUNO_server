from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.core.admin import VisitAdmin
from visitacampo.core.models import Visit
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import budget_world, item, plan, quantity_row, quantity_visit

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class VisitAdminQuantitiesTests(TestCase):
    def setUp(self) -> None:
        self.world, budget = budget_world()
        self.concrete, self.steel = item(budget, "2.1"), item(budget, "2.2")
        self.admin = VisitAdmin(Visit, None)  # type: ignore[arg-type]

    def test_quantities_are_listed_with_flags(self) -> None:
        plan(self.world, 3, self.concrete, "10")
        self.world.approve(quantity_visit(self.world, 3, quantity_row(self.concrete, "8")))
        visit = quantity_visit(
            self.world,
            3,
            quantity_row(self.concrete, "5"),
            quantity_row(self.steel, "2.5", note="no planeado <b>"),
            pct="30",
            hours=1,
        )
        text = str(self.admin.quantities_pretty(visit))
        self.assertIn("2.1 Concreto zapatas: 5 m3", text)
        self.assertIn("supera el plan (13 de 10)", text)
        self.assertIn("2.2 Acero de refuerzo: 2,5 kg", text)
        self.assertIn("fuera del plan", text)
        self.assertIn("no planeado &lt;b&gt;", text)
        self.assertIn("% manual ignorado", text)

    def test_within_plan_without_pct(self) -> None:
        plan(self.world, 3, self.concrete, "10")
        visit = quantity_visit(self.world, 3, quantity_row(self.concrete, "4"))
        text = str(self.admin.quantities_pretty(visit))
        self.assertIn("2.1 Concreto zapatas: 4 m3", text)
        self.assertNotIn("supera", text)
        self.assertNotIn("ignorado", text)

    def test_visit_without_quantities(self) -> None:
        visit = self.world.visit(3, "40")
        self.assertEqual(self.admin.quantities_pretty(visit), "—")
