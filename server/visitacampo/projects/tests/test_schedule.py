"""Filas del cronograma con indicadores, responsables y ejecutores."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from django.http import Http404
from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import Project, Role, TaskResponsible
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.importer import apply_import, stage_import
from visitacampo.projects.schedule import build_schedule, filter_rows

from .samples import v1, v2

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class BuildScheduleTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.agent = make_user(self.org, username="agente", role=Role.FIELD_AGENT)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.admin)
        staged = stage_import(self.ctx, self.project, file_name="v1.xml", data=v1())
        apply_import(self.ctx, staged.pk)
        self.project.refresh_from_db()

    def test_rows_in_project_order_with_indicators(self) -> None:
        schedule = build_schedule(self.ctx, self.project)
        self.assertEqual(schedule.cut, date(2026, 3, 20))
        self.assertEqual([r.task.uid for r in schedule.rows], [1, 2, 3, 4, 5, 6])
        row = next(r for r in schedule.rows if r.task.uid == 3)
        assert row.indicators is not None
        self.assertEqual(row.indicators.project.delay_days, 5)
        self.assertEqual(row.executors, ["Cuadrilla A"])
        assert schedule.total is not None
        self.assertEqual(schedule.total.planned_pct, 83.3)
        self.assertEqual([s.uid for s in schedule.stages], [1, 4])

    def test_cut_date_can_be_overridden(self) -> None:
        schedule = build_schedule(self.ctx, self.project, cut=date(2026, 3, 6))
        row = next(r for r in schedule.rows if r.task.uid == 3)
        assert row.indicators is not None
        self.assertEqual(row.indicators.planned_pct, 0.0)

    def test_suno_progress_map_is_applied(self) -> None:
        task = self.project.tasks.get(uid=3)
        schedule = build_schedule(self.ctx, self.project, suno_pct={task.pk: 100.0})
        row = next(r for r in schedule.rows if r.task.uid == 3)
        assert row.indicators is not None
        self.assertEqual(row.indicators.suno.actual_pct, 100.0)

    def test_suno_progress_accepts_decimals(self) -> None:
        task = self.project.tasks.get(uid=3)
        pct = {task.pk: Decimal("100")}
        schedule = build_schedule(self.ctx, self.project, suno_pct=pct)  # type: ignore[arg-type]
        row = next(r for r in schedule.rows if r.task.uid == 3)
        assert row.indicators is not None
        self.assertEqual(row.indicators.suno.actual_pct, 100.0)

    def test_foreign_project_is_refused(self) -> None:
        other_org = make_org("Otra")
        other_admin = make_user(other_org, username="otro", role=Role.ADMIN)
        foreign = Project.objects.create(org=other_org, name="Ajena", created_by=other_admin)
        with self.assertRaises(Http404):
            build_schedule(self.ctx, foreign)

    def test_retired_tasks_are_not_listed(self) -> None:
        staged = stage_import(self.ctx, self.project, file_name="v2.xml", data=v2())
        apply_import(self.ctx, staged.pk)
        self.project.refresh_from_db()
        uids = [r.task.uid for r in build_schedule(self.ctx, self.project).rows]
        self.assertNotIn(5, uids)
        self.assertIn(7, uids)

    def test_filters(self) -> None:
        stage = self.project.tasks.get(uid=4)
        TaskResponsible.objects.create(
            org=self.org, task=stage, user=self.agent, assigned_by=self.admin
        )
        rows = build_schedule(self.ctx, self.project).rows
        self.assertEqual([r.task.uid for r in filter_rows(rows, stage_id=stage.pk)], [4, 5, 6])
        self.assertEqual(
            [r.task.uid for r in filter_rows(rows, responsible_id=self.agent.pk)], [4, 5, 6]
        )
        self.assertEqual([r.task.uid for r in filter_rows(rows, critical_only=True)], [3, 5, 6])
        # Con la fuente Project, la 2 va al día; todas las demás van atrasadas.
        self.assertNotIn(
            2, [r.task.uid for r in filter_rows(rows, delayed_only=True, source="project")]
        )
