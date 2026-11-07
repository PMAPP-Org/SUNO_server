from __future__ import annotations

import unittest
from unittest import mock

from django.test import TestCase

from suno.budgets.tests.factories import stage_rows
from suno.budgets.tests.samples import SPLIT_ROWS, V2_ROWS
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import budget_world, item, plan, quantity_record, quantity_row

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
        record = quantity_record(world, 3, quantity_row(item(budget, "1.2"), "2"), pct="10")
        world.approve(record)
        version = stage_rows(world.ctx(), budget, V2_ROWS)
        self.assertIn(
            "1 ítem(s) eliminado(s) están en la matriz o tienen cantidades ejecutadas: 1.2",
            " ".join(version.summary["warnings"]),
        )

    def test_no_warning_without_matrix(self) -> None:
        world, budget = budget_world()
        version = stage_rows(world.ctx(), budget, V2_ROWS)
        self.assertFalse(any("matriz" in w for w in version.summary["warnings"]))

    def test_execution_only_does_not_mention_matrix_rows(self) -> None:
        world, budget = budget_world()
        world.approve(quantity_record(world, 3, quantity_row(item(budget, "1.2"), "2")))
        version = stage_rows(world.ctx(), budget, V2_ROWS)
        warning = next(w for w in version.summary["warnings"] if "ítem(s) eliminado(s)" in w)
        self.assertNotIn("filas de la matriz se conservan", warning)
        self.assertIn(
            "cantidades ejecutadas de los ítems sin filas en la matriz se conservan", warning
        )

    def test_matrix_only_does_not_mention_execution_kept(self) -> None:
        world, budget = budget_world()
        plan(world, 3, item(budget, "1.2"), "5")
        version = stage_rows(world.ctx(), budget, V2_ROWS)
        warning = next(w for w in version.summary["warnings"] if "ítem(s) eliminado(s)" in w)
        self.assertIn("Sus filas de la matriz se conservan", warning)
        self.assertNotIn("sin filas en la matriz", warning)

    def test_records_are_not_read_when_every_removed_item_is_in_the_matrix(self) -> None:
        world, budget = budget_world()
        plan(world, 3, item(budget, "1.2"), "5")
        with mock.patch("suno.matrix.quantities.executed_by_pair") as executed:
            stage_rows(world.ctx(), budget, V2_ROWS)
        executed.assert_not_called()

    def test_records_are_read_when_a_removed_item_is_not_in_the_matrix(self) -> None:
        world, budget = budget_world()
        with mock.patch("suno.matrix.quantities.executed_by_pair", return_value={}) as executed:
            stage_rows(world.ctx(), budget, V2_ROWS)
        executed.assert_called_once()

    def test_item_that_becomes_a_chapter_in_matrix_is_warned(self) -> None:
        world, budget = budget_world()
        plan(world, 3, item(budget, "2.1"), "5")  # SPLIT_ROWS vuelve capítulo el 2.1
        version = stage_rows(world.ctx(), budget, SPLIT_ROWS)
        warning = next(w for w in version.summary["warnings"] if "pasan a ser capítulos" in w)
        self.assertIn("Los ítems 2.1 pasan a ser capítulos", warning)
        self.assertIn("dejan de contar para el avance", warning)
        self.assertFalse(any("eliminado(s)" in w for w in version.summary["warnings"]))

    def test_item_that_becomes_a_chapter_with_execution_is_warned(self) -> None:
        world, budget = budget_world()
        world.approve(quantity_record(world, 3, quantity_row(item(budget, "2.1"), "2")))
        version = stage_rows(world.ctx(), budget, SPLIT_ROWS)
        self.assertIn("Los ítems 2.1 pasan a ser capítulos", " ".join(version.summary["warnings"]))

    def test_item_that_becomes_a_chapter_unused_is_not_warned(self) -> None:
        world, budget = budget_world()
        version = stage_rows(world.ctx(), budget, SPLIT_ROWS)
        self.assertFalse(any("capítulos" in w for w in version.summary.get("warnings", [])))
