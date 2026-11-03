from __future__ import annotations

import unittest
from decimal import Decimal

from django.test import TestCase

from visitacampo.budgets.tests.factories import apply_rows
from visitacampo.budgets.tests.samples import V2_ROWS
from visitacampo.budgets.tests.xlsx import build_xlsx
from visitacampo.core.models import (
    AuditEvent,
    QuantityPlan,
    QuantityPlanImport,
    QuantityPlanImportStatus,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.importer import (
    PlanRejected,
    apply_plan,
    discard_plan,
    stage_plan,
    template_bytes,
)
from visitacampo.matrix.locations import load_tree
from visitacampo.matrix.template import header, read_template

from .factories import (
    budget_world,
    import_schedule,
    item,
    plan,
    quantity_row,
    quantity_visit,
    suno_levels,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

HEADER = header(["Tramo", "PR"])


def sheet(*rows: list[object]) -> bytes:
    return build_xlsx(rows, header=HEADER, sheet="Matriz")


@skip_without_db
class ImporterTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        self.ctx = self.world.ctx()
        suno_levels(self.world)

    def _stage(self, data: bytes) -> QuantityPlanImport:
        return stage_plan(self.ctx, self.world.project, file_name="m.xlsx", data=data)

    def test_stage_and_apply(self) -> None:
        staged = self._stage(
            sheet(
                [3, "", "", "Tramo 1", "", "", "1.1", "", "", 60],
                [3, "", "", "Tramo 1", "", "", "2.1", "", "", 4],
                [2, "", "", "Tramo 2", "PR 1", "", "", "", "", None],
            )
        )
        self.assertEqual(staged.status, QuantityPlanImportStatus.PENDING)
        self.assertEqual(staged.summary["added"], 2)
        self.assertEqual(staged.summary["new_nodes"], 3)
        self.assertEqual(staged.summary["item_counts"]["under"], 5)
        applied = apply_plan(self.ctx, staged.pk)
        self.assertEqual(applied.number, 1)
        self.assertEqual(
            dict(QuantityPlan.objects.values_list("item__code", "planned_quantity")),
            {"1.1": Decimal("60.0000"), "2.1": Decimal("4.0000")},
        )
        tree = load_tree(self.ctx, self.world.project)
        self.assertEqual(tree.label(self.world.task(2).location_id), "Tramo 2 / PR 1")
        self.assertEqual(tree.label(self.world.task(3).location_id), "Tramo 1")
        self.assertTrue(AuditEvent.objects.filter(action="matrix.import_applied").exists())

    def test_replace_keeps_executed_pairs_at_zero(self) -> None:
        concrete, steel = item(self.budget, "2.1"), item(self.budget, "2.2")
        plan(self.world, 3, concrete, "10")
        plan(self.world, 3, steel, "100")
        self.world.approve(quantity_visit(self.world, 3, quantity_row(concrete, "2")))
        staged = self._stage(sheet([3, "", "", "", "", "", "1.1", "", "", 1]))
        self.assertEqual(
            (staged.summary["added"], staged.summary["removed"], staged.summary["kept_zero"]),
            (1, 1, 1),
        )
        apply_plan(self.ctx, staged.pk)
        self.assertEqual(
            dict(QuantityPlan.objects.values_list("item__code", "planned_quantity")),
            {"1.1": Decimal("1.0000"), "2.1": Decimal("0.0000")},
        )

    def test_errors_reject_the_upload(self) -> None:
        with self.assertRaises(PlanRejected) as caught:
            self._stage(sheet([99, "", "", "", "", "", "1.1", "", "", 1]))
        self.assertIn("UniqueID 99", caught.exception.errors[0])
        self.assertFalse(QuantityPlanImport.objects.exists())

    def test_not_xlsx(self) -> None:
        with self.assertRaises(PlanRejected):
            stage_plan(self.ctx, self.world.project, file_name="m.xls", data=b"x")

    def test_new_upload_discards_previous_pending(self) -> None:
        first = self._stage(sheet([3, "", "", "", "", "", "1.1", "", "", 1]))
        self._stage(sheet([3, "", "", "", "", "", "1.1", "", "", 2]))
        first.refresh_from_db()
        self.assertEqual(first.status, QuantityPlanImportStatus.DISCARDED)

    def test_stale_preview(self) -> None:
        staged = self._stage(sheet([3, "", "", "", "", "", "1.1", "", "", 1]))
        apply_rows(self.ctx, self.budget, V2_ROWS)
        with self.assertRaisesMessage(PlanRejected, "Vuelve a subir"):
            apply_plan(self.ctx, staged.pk)

    def test_discard(self) -> None:
        staged = self._stage(sheet([3, "", "", "", "", "", "1.1", "", "", 1]))
        discard_plan(self.ctx, staged.pk)
        staged.refresh_from_db()
        self.assertEqual(staged.status, QuantityPlanImportStatus.DISCARDED)
        with self.assertRaisesMessage(PlanRejected, "ya fue aplicada o descartada"):
            apply_plan(self.ctx, staged.pk)

    def test_template_round_trip_changes_nothing(self) -> None:
        plan(self.world, 3, item(self.budget, "2.1"), "4")
        data = template_bytes(self.ctx, self.world.project)
        rows = read_template(data)
        self.assertEqual(list(rows[0][:10]), HEADER)
        uids = [row[0] for row in rows[1:]]
        self.assertEqual(uids, [2, 3, 5, 6])  # hojas vigentes en orden del Project
        staged = self._stage(data)
        self.assertEqual(
            (staged.summary["added"], staged.summary["changed"], staged.summary["removed"]),
            (0, 0, 0),
        )

    def test_project_mode_ignores_level_columns(self) -> None:
        from .factories import located_xml, project_levels_from_xml

        world, _ = budget_world("Modo Project")
        project_levels_from_xml(world)
        import_schedule(world, located_xml())
        staged = stage_plan(
            world.ctx(),
            world.project,
            file_name="m.xlsx",
            data=sheet([3, "", "", "Otro", "", "", "1.1", "", "", 1]),
        )
        apply_plan(world.ctx(), staged.pk)
        tree = load_tree(world.ctx(), world.project)
        self.assertEqual(tree.label(world.task(3).location_id), "Tramo 1")
