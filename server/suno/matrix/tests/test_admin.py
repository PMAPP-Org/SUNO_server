from __future__ import annotations

import unittest

from django.test import TestCase

from suno.budgets.tests.factories import apply_rows
from suno.budgets.tests.samples import SPLIT_ROWS
from suno.core.admin import RecordAdmin
from suno.core.models import Record
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import budget_world, item, locate, plan, quantity_record, quantity_row, suno_levels

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class RecordAdminQuantitiesTests(TestCase):
    def setUp(self) -> None:
        self.world, budget = budget_world()
        self.concrete, self.steel = item(budget, "2.1"), item(budget, "2.2")
        self.admin = RecordAdmin(Record, None)

    def test_quantities_are_listed_with_flags(self) -> None:
        plan(self.world, 3, self.concrete, "10")
        self.world.approve(quantity_record(self.world, 3, quantity_row(self.concrete, "8")))
        record = quantity_record(
            self.world,
            3,
            quantity_row(self.concrete, "5"),
            quantity_row(self.steel, "2.5", note="no planeado <b>"),
            pct="30",
            hours=1,
        )
        text = str(self.admin.quantities_pretty(record))
        self.assertIn("2.1 Concreto zapatas: 5 m3", text)
        self.assertIn("supera el plan (13 de 10)", text)
        self.assertIn("2.2 Acero de refuerzo: 2,5 kg", text)
        self.assertIn("fuera del plan", text)
        self.assertIn("no planeado &lt;b&gt;", text)
        self.assertIn("% manual ignorado", text)

    def test_within_plan_without_pct(self) -> None:
        plan(self.world, 3, self.concrete, "10")
        record = quantity_record(self.world, 3, quantity_row(self.concrete, "4"))
        text = str(self.admin.quantities_pretty(record))
        self.assertIn("2.1 Concreto zapatas: 4 m3", text)
        self.assertNotIn("supera", text)
        self.assertNotIn("ignorado", text)

    def test_unplanned_without_note_and_negative_without_note(self) -> None:
        plan(self.world, 3, self.concrete, "10")
        record = quantity_record(
            self.world, 3, quantity_row(self.steel, "2"), quantity_row(self.concrete, "-1")
        )
        text = str(self.admin.quantities_pretty(record))
        self.assertIn("2.2 Acero de refuerzo: 2 kg — no planeado y sin nota", text)
        self.assertIn("ajuste negativo sin nota", text)

    def test_location_outside_the_current_task_location(self) -> None:
        suno_levels(self.world)
        plan(self.world, 3, self.concrete, "10")
        inside = locate(self.world, 3, "Tramo 2", "PR 1").location_id
        outside = locate(self.world, 3, "Tramo 3").location_id
        locate(self.world, 3, "Tramo 2")
        record = quantity_record(
            self.world,
            3,
            quantity_row(self.concrete, "1", location=inside),
            quantity_row(self.concrete, "2", location=outside),
        )
        lines = str(self.admin.quantities_pretty(record)).split("</p>")
        self.assertNotIn("ubicación fuera", lines[0])
        self.assertIn("ubicación fuera de la ubicación actual de la tarea", lines[1])
        task = self.world.task(3)
        task.location = None
        task.save(update_fields=["location"])
        record.refresh_from_db()
        lines = str(self.admin.quantities_pretty(record)).split("</p>")
        self.assertIn("ubicación fuera de la ubicación actual de la tarea", lines[0])

    def test_pct_is_not_ignored_when_the_plan_only_has_a_chapter(self) -> None:
        """El 2.1 planeado se partió en un capítulo: la tarea ya no se mide por cantidades
        (`measured_by`), así que el % manual cuenta."""
        plan(self.world, 3, self.concrete, "10")
        apply_rows(self.world.ctx(), self.concrete.budget, SPLIT_ROWS)
        record = quantity_record(self.world, 3, quantity_row(self.concrete, "1"), pct="30")
        self.assertNotIn("ignorado", str(self.admin.quantities_pretty(record)))

    def test_pct_is_not_ignored_when_the_plan_is_at_zero(self) -> None:
        plan(self.world, 3, self.concrete, "0")
        record = quantity_record(self.world, 3, quantity_row(self.concrete, "1"), pct="30")
        self.assertNotIn("ignorado", str(self.admin.quantities_pretty(record)))

    def test_record_without_quantities(self) -> None:
        record = self.world.record(3, "40")
        self.assertEqual(self.admin.quantities_pretty(record), "—")
