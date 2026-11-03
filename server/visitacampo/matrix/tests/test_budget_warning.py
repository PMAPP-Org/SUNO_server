from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.budgets.tests.factories import stage_rows
from visitacampo.budgets.tests.samples import V2_ROWS
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
class BudgetPreviewWarningTests(TestCase):
    def test_removed_item_in_matrix_is_warned(self) -> None:
        world, budget = budget_world()
        plan(world, 3, item(budget, "1.2"), "5")  # V2 elimina el 1.2
        version = stage_rows(world.ctx(), budget, V2_ROWS)
        self.assertIn(
            "1 ítem(s) eliminado(s) están en la matriz o tienen cantidades ejecutadas: 1.2",
            " ".join(version.summary["warnings"]),
        )

    def test_removed_item_with_validated_execution_is_warned(self) -> None:
        world, budget = budget_world()
        visit = quantity_visit(world, 3, quantity_row(item(budget, "1.2"), "2"), pct="10")
        world.approve(visit)
        version = stage_rows(world.ctx(), budget, V2_ROWS)
        self.assertIn(
            "1 ítem(s) eliminado(s) están en la matriz o tienen cantidades ejecutadas: 1.2",
            " ".join(version.summary["warnings"]),
        )

    def test_no_warning_without_matrix(self) -> None:
        world, budget = budget_world()
        version = stage_rows(world.ctx(), budget, V2_ROWS)
        self.assertFalse(any("matriz" in w for w in version.summary["warnings"]))
