"""Asignaciones de trabajadores al proyecto (spec 1.2 y 3.2 "Asignaciones"; decisión 9)."""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent, Project, ProjectWorker
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import assignments
from suno.labor.assignments import AssignmentData
from suno.labor.common import LaborRejected

from .factories import TODAY, WEEK, bare_line, bare_timesheet, labor_world, make_worker

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class AssignmentTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.luis = make_worker(self.lw.world.org, "70111222", "Luis Torres",
                                hired=date(2026, 2, 1))  # fmt: skip
        self.ctx = self.lw.ctx(self.lw.contributor)

    def test_create_within_employment_and_without_overlaps(self) -> None:
        project = self.lw.project
        row = assignments.create_assignment(
            self.ctx, project, AssignmentData(self.luis.pk, date(2026, 2, 1), date(2026, 3, 31))
        )
        self.assertEqual(row.created_by, self.lw.contributor)
        for data, message in (
            (AssignmentData(self.luis.pk, date(2026, 3, 1), None), "Se cruza"),
            (AssignmentData(self.luis.pk, date(2026, 1, 15), None), "antes del ingreso"),
            (AssignmentData(self.luis.pk, date(2026, 4, 10), date(2026, 4, 1)), "termina antes"),
        ):
            with self.subTest(data=data), self.assertRaises(LaborRejected) as rejected:
                assignments.create_assignment(self.ctx, project, data)
            self.assertIn(message, rejected.exception.errors[0])
        assignments.create_assignment(
            self.ctx, project, AssignmentData(self.luis.pk, date(2026, 4, 1), None)
        )
        self.assertEqual(ProjectWorker.objects.filter(worker=self.luis).count(), 2)
        self.assertEqual(AuditEvent.objects.filter(action="labor_assignment.changed").count(), 2)

    def test_a_worker_may_work_in_several_projects(self) -> None:
        other = Project.objects.create(
            org=self.lw.world.org, name="Obra 2", created_by=self.lw.world.admin
        )
        assignments.create_assignment(
            self.lw.ctx(), other, AssignmentData(self.lw.pedro.pk, date(2026, 1, 5), None)
        )
        self.assertEqual(ProjectWorker.objects.filter(worker=self.lw.pedro).count(), 2)

    def test_retired_worker_and_permissions(self) -> None:
        self.luis.retired_on, self.luis.is_active = date(2026, 3, 15), False
        self.luis.save()
        with self.assertRaises(LaborRejected) as rejected:
            assignments.create_assignment(
                self.ctx, self.lw.project, AssignmentData(self.luis.pk, date(2026, 3, 1), None)
            )
        self.assertIn("retiro", rejected.exception.errors[0])
        with self.assertRaises(PermissionDenied):
            assignments.create_assignment(
                self.lw.ctx(self.lw.viewer), self.lw.project,
                AssignmentData(self.lw.maria.pk, date(2026, 6, 1), None),
            )  # fmt: skip

    def test_edit_and_delete_keep_tareo_inside(self) -> None:
        pedro_row = ProjectWorker.objects.get(worker=self.lw.pedro)
        bare_line(bare_timesheet(self.lw), self.lw.pedro, WEEK[1])
        admin = self.lw.ctx()
        with self.assertRaises(LaborRejected) as cut:
            assignments.update_assignment(
                admin, self.lw.project, pedro_row.pk,
                AssignmentData(self.lw.pedro.pk, date(2026, 1, 5), date(2026, 3, 5)),
            )  # fmt: skip
        self.assertIn("08/03/2026", cut.exception.errors[0])
        with self.assertRaises(LaborRejected):
            assignments.delete_assignment(admin, self.lw.project, pedro_row.pk)
        assignments.update_assignment(
            admin, self.lw.project, pedro_row.pk,
            AssignmentData(self.lw.pedro.pk, date(2026, 1, 5), date(2026, 3, 31)),
        )  # fmt: skip
        maria_row = ProjectWorker.objects.get(worker=self.lw.maria)
        assignments.delete_assignment(admin, self.lw.project, maria_row.pk)
        self.assertFalse(ProjectWorker.objects.filter(pk=maria_row.pk).exists())

    def test_contributor_only_edits_its_own(self) -> None:
        pedro_row = ProjectWorker.objects.get(worker=self.lw.pedro)  # la creó la administración
        with self.assertRaises(PermissionDenied):
            assignments.delete_assignment(self.ctx, self.lw.project, pedro_row.pk)
