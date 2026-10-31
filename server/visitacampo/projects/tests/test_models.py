"""Restricciones de BD del modelo de proyectos."""

from __future__ import annotations

import unittest

from django.db import IntegrityError, transaction
from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.models import Project, Role, ScheduleImport, Task, TaskResponsible
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class ProjectModelTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.user = make_user(self.org, username="admin", role=Role.ADMIN)
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.user)

    def _task(self, uid: int) -> Task:
        return Task.objects.create(
            org=self.org,
            project=self.project,
            uid=uid,
            outline_number=str(uid),
            outline_level=1,
            sort_order=uid,
            name=f"Tarea {uid}",
        )

    def test_task_uid_is_unique_per_project(self) -> None:
        self._task(1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._task(1)

    def test_project_name_is_unique_per_org_ignoring_case(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            Project.objects.create(org=self.org, name="OBRA", created_by=self.user)

    def test_import_number_is_unique_per_project_but_pending_has_none(self) -> None:
        def make(number: int | None) -> ScheduleImport:
            return ScheduleImport.objects.create(
                org=self.org,
                project=self.project,
                number=number,
                file_name="v.xml",
                source_xml=b"",
                uploaded_by=self.user,
            )

        make(None)
        make(None)
        make(1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make(1)

    def test_responsible_is_unique_per_task_and_user(self) -> None:
        task = self._task(1)
        TaskResponsible.objects.create(
            org=self.org, task=task, user=self.user, assigned_by=self.user
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TaskResponsible.objects.create(
                org=self.org, task=task, user=self.user, assigned_by=self.user
            )
