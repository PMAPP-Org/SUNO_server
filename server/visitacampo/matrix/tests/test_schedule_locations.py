from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.core.models import ScheduleImport
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.locations import load_tree
from visitacampo.projects.importer import apply_import, stage_import
from visitacampo.projects.tests.factories import make_field_world
from visitacampo.projects.tests.samples import TEXT1, project_xml, task_xml

from .factories import import_schedule, located_xml, plan, project_levels_from_xml, suno_levels

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class ScheduleLocationTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        self.ctx = self.world.ctx()

    def _label(self, uid: int) -> str:
        task = self.world.task(uid)
        return load_tree(self.ctx, self.world.project).label(task.location_id)

    def test_summary_lists_custom_fields(self) -> None:
        staged = stage_import(self.ctx, self.world.project, file_name="c.xml", data=located_xml())
        self.assertEqual(
            staged.summary["custom_fields"],
            [
                {"field_id": TEXT1, "label": "Text1 (Tramo)"},
                {"field_id": "188743734", "label": "Text2 (PR)"},
            ],
        )

    def test_apply_assigns_leaf_locations(self) -> None:
        project_levels_from_xml(self.world)
        import_schedule(self.world, located_xml())
        self.assertEqual(self._label(3), "Tramo 1")
        self.assertEqual(self._label(5), "Tramo 2 / PR 12+300")
        self.assertEqual(self._label(2), "")
        self.assertIsNone(self.world.task(4).location_id)  # resumen

    def test_preview_warns_changes_and_incomplete_paths(self) -> None:
        project_levels_from_xml(self.world)
        import_schedule(self.world, located_xml())
        data = located_xml(tramo_3="Tramo 9", tramo_5="")
        staged = stage_import(self.ctx, self.world.project, file_name="c.xml", data=data)
        warnings = " ".join(staged.summary["warnings"])
        self.assertIn("2 tarea(s) cambian de ubicación", warnings)
        self.assertIn("1.2 Descapote: Tramo 1 → Tramo 9", warnings)
        self.assertIn("1 tarea(s) con la ubicación incompleta", warnings)
        apply_import(self.ctx, staged.pk)
        self.assertEqual(self._label(3), "Tramo 9")
        self.assertEqual(self._label(5), "")  # sin Tramo, el PR solo no sirve

    def test_missing_field_keeps_previous_locations(self) -> None:
        project_levels_from_xml(self.world)
        import_schedule(self.world, located_xml())
        plain = project_xml([task_xml(3, "Descapote", "1")])
        staged = stage_import(self.ctx, self.world.project, file_name="c.xml", data=plain)
        self.assertIn(
            "El archivo no trae los campos de ubicación Tramo, PR",
            " ".join(staged.summary["warnings"]),
        )
        apply_import(self.ctx, staged.pk)
        self.assertEqual(self._label(3), "Tramo 1")

    def test_suno_levels_do_not_touch_locations(self) -> None:
        suno_levels(self.world)
        import_schedule(self.world, located_xml())
        self.assertEqual(self._label(5), "")

    def test_retired_task_with_plan_is_warned(self) -> None:
        from .factories import budget_world, item

        world, budget = budget_world("Con plan")
        plan(world, 5, item(budget, "2.1"), "10")
        data = project_xml([task_xml(3, "Descapote", "1")])
        staged = stage_import(world.ctx(), world.project, file_name="c.xml", data=data)
        self.assertIn(
            "tarea(s) retirada(s) con cantidades planeadas: 2.1 Columnas",
            " ".join(staged.summary["warnings"]),
        )
        self.assertEqual(ScheduleImport.objects.filter(project=world.project).count(), 2)
