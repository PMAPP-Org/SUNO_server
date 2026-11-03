from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.field_tasks import field_tasks
from visitacampo.projects.schedule import build_schedule

from .factories import budget_world, item, plan, quantity_row, quantity_visit

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class ScheduleProgressTests(TestCase):
    def setUp(self) -> None:
        self.world, budget = budget_world()
        self.concrete = item(budget, "2.1")
        plan(self.world, 3, self.concrete, "10")
        visit = quantity_visit(self.world, 3, quantity_row(self.concrete, "4"))
        self.world.approve(visit)

    def test_schedule_uses_quantity_pct(self) -> None:
        schedule = build_schedule(self.world.ctx(), self.world.project)
        row = next(r for r in schedule.rows if r.task.uid == 3)
        assert row.indicators is not None
        self.assertEqual(row.indicators.suno.actual_pct, 40.0)
        self.assertEqual(row.indicators.value, 4500000.0)

    def test_field_tasks_report_quantity_pct(self) -> None:
        self.world.responsible(3)
        items = field_tasks(self.world.ctx(self.world.agent))
        self.assertEqual(items[0]["last_validated_pct"], 40.0)

    def test_task_page_shows_quantity_pct(self) -> None:
        self.client.force_login(self.world.admin)
        response = self.client.get(
            f"/plataforma/proyectos/{self.world.project.pk}/tareas/{self.world.task(3).pk}/"
        )
        self.assertContains(response, "40 %")
        self.assertContains(response, "por cantidades")
