"""Importación de cronogramas: vista previa y aplicación."""

from __future__ import annotations

import dataclasses
import unittest

from django.db import connection, connections
from django.http import Http404
from django.test import TestCase, TransactionTestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import (
    Project,
    Role,
    ScheduleImport,
    ScheduleImportStatus,
    TaskDependency,
    TaskResource,
    TaskResponsible,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.importer import (
    ImportRejected,
    apply_import,
    discard_import,
    stage_import,
    summarize_changes,
)
from visitacampo.projects.mspdi import parse_mspdi

from .samples import project_xml, task_xml, v1, v2

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


class SummarizeChangesTests(unittest.TestCase):
    def test_first_import_is_all_added(self) -> None:
        summary = summarize_changes({}, parse_mspdi(v1()), set())
        self.assertEqual(
            (summary["task_count"], summary["added"], summary["retired"], summary["modified"]),
            (6, 6, 0, 0),
        )
        self.assertFalse(summary["baseline_changed"])
        self.assertEqual(summary["warnings"], [])

    def test_no_baseline_warning(self) -> None:
        summary = summarize_changes({}, parse_mspdi(project_xml([task_xml(1, "A", "1")])), set())
        self.assertIn("Sin línea base: no se calcularán atrasos.", summary["warnings"])

    def test_parser_warnings_are_included(self) -> None:
        parsed = dataclasses.replace(parse_mspdi(v1()), warnings=("Aviso del lector.",))
        summary = summarize_changes({}, parsed, set())
        self.assertIn("Aviso del lector.", summary["warnings"])


@skip_without_db
class StageImportTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.admin)

    def test_stage_creates_pending_import_with_summary(self) -> None:
        imp = stage_import(self.ctx, self.project, file_name="obra.xml", data=v1())
        self.assertEqual(imp.status, ScheduleImportStatus.PENDING)
        self.assertIsNone(imp.number)
        self.assertEqual(imp.summary["added"], 6)
        self.assertEqual(str(imp.status_date), "2026-03-20")
        self.assertEqual(self.project.tasks.count(), 0)  # nada aplicado aún

    def test_stage_rejects_empty_schedule(self) -> None:
        with self.assertRaises(ImportRejected) as caught:
            stage_import(self.ctx, self.project, file_name="x.xml", data=project_xml([]))
        self.assertIn("no contiene tareas", str(caught.exception))

    def test_stage_refuses_foreign_project(self) -> None:
        other_org = make_org("Otra")
        other_admin = make_user(other_org, username="otro", role=Role.ADMIN)
        foreign = Project.objects.create(org=other_org, name="Ajena", created_by=other_admin)
        with self.assertRaises(Http404):
            stage_import(self.ctx, foreign, file_name="x.xml", data=v1())

    def test_stage_rejects_invalid_file(self) -> None:
        with self.assertRaises(ImportRejected) as caught:
            stage_import(self.ctx, self.project, file_name="x.xml", data=b"nada")
        self.assertIn("no es un XML", str(caught.exception))


@skip_without_db
class ApplyImportTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.agent = make_user(self.org, username="agente", role=Role.FIELD_AGENT)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.admin)

    def _import(self, data: bytes) -> ScheduleImport:
        staged = stage_import(self.ctx, self.project, file_name="obra.xml", data=data)
        return apply_import(self.ctx, staged.pk)

    def test_first_apply_creates_tasks_hierarchy_and_links(self) -> None:
        imp = self._import(v1())
        self.assertEqual((imp.number, imp.status), (1, ScheduleImportStatus.APPLIED))
        tasks = {t.uid: t for t in self.project.tasks.all()}
        self.assertEqual(sorted(tasks), [1, 2, 3, 4, 5, 6])
        self.assertEqual(tasks[3].parent_id, tasks[1].pk)
        self.assertEqual(tasks[3].stage_id, tasks[1].pk)
        self.assertEqual(tasks[1].stage_id, tasks[1].pk)
        self.assertIsNone(tasks[1].parent_id)
        self.assertEqual(TaskDependency.objects.filter(project=self.project).count(), 3)
        self.assertEqual(
            {
                (link.task.uid, link.resource.name)
                for link in TaskResource.objects.filter(task__project=self.project)
            },
            {(2, "Topógrafo"), (3, "Cuadrilla A"), (5, "Cuadrilla A")},
        )
        self.project.refresh_from_db()
        self.assertEqual(str(self.project.status_date), "2026-03-20")
        self.assertEqual(self.project.calendar["exceptions"][0]["start"], "2026-03-23")

    def test_second_version_updates_retires_and_keeps_responsibles(self) -> None:
        self._import(v1())
        columns = self.project.tasks.get(uid=5)
        TaskResponsible.objects.create(
            org=self.org, task=columns, user=self.agent, assigned_by=self.admin
        )
        staged = stage_import(self.ctx, self.project, file_name="v2.xml", data=v2())
        self.assertEqual((staged.summary["added"], staged.summary["retired"]), (1, 1))
        self.assertTrue(staged.summary["baseline_changed"])
        self.assertIn("2.1 Columnas", staged.summary["warnings"][-1])
        imp = apply_import(self.ctx, staged.pk)
        self.assertEqual(imp.number, 2)
        columns.refresh_from_db()
        self.assertEqual(columns.retired_in_id, imp.pk)
        self.assertTrue(TaskResponsible.objects.filter(task=columns, user=self.agent).exists())
        self.assertEqual(str(self.project.tasks.get(uid=3).project_pct), "100.00")
        parent = self.project.tasks.get(uid=7).parent
        assert parent is not None
        self.assertEqual(parent.uid, 4)

    def test_retired_task_is_reactivated_when_it_comes_back(self) -> None:
        self._import(v1())
        self._import(v2())
        staged = stage_import(self.ctx, self.project, file_name="v3.xml", data=v1())
        self.assertEqual(staged.summary["reactivated"], 1)
        apply_import(self.ctx, staged.pk)
        self.assertIsNone(self.project.tasks.get(uid=5).retired_in_id)
        self.assertIsNotNone(self.project.tasks.get(uid=7).retired_in_id)

    def test_cannot_apply_twice(self) -> None:
        staged = stage_import(self.ctx, self.project, file_name="obra.xml", data=v1())
        apply_import(self.ctx, staged.pk)
        with self.assertRaises(ImportRejected):
            apply_import(self.ctx, staged.pk)

    def test_stale_preview_is_rejected(self) -> None:
        old = stage_import(self.ctx, self.project, file_name="a.xml", data=v1())
        newer = stage_import(self.ctx, self.project, file_name="b.xml", data=v2())
        apply_import(self.ctx, newer.pk)
        with self.assertRaises(ImportRejected) as caught:
            apply_import(self.ctx, old.pk)
        self.assertIn("Vuelve a subir", str(caught.exception))

    def test_discard(self) -> None:
        staged = stage_import(self.ctx, self.project, file_name="obra.xml", data=v1())
        discard_import(self.ctx, staged.pk)
        staged.refresh_from_db()
        self.assertEqual(staged.status, ScheduleImportStatus.DISCARDED)
        with self.assertRaises(ImportRejected):
            apply_import(self.ctx, staged.pk)


@skip_without_db
class ConcurrentImportTests(TransactionTestCase):
    """Otra transacción tiene bloqueado el proyecto: se rechaza sin esperar."""

    def test_apply_is_rejected_while_project_is_locked(self) -> None:
        org = make_org("Constructora")
        admin = make_user(org, username="admin", role=Role.ADMIN)
        ctx = resolve_request_context(Principal(user_id=admin.pk, org_id=org.pk))
        project = Project.objects.create(org=org, name="Obra", created_by=admin)
        staged = stage_import(ctx, project, file_name="obra.xml", data=v1())

        other = connections.create_connection("default")
        other.set_autocommit(False)
        try:
            with other.cursor() as cursor:
                table = connection.ops.quote_name(Project._meta.db_table)
                cursor.execute(f"SELECT id FROM {table} WHERE id = %s FOR UPDATE", [project.pk])
            with self.assertRaises(ImportRejected) as caught:
                apply_import(ctx, staged.pk)
            self.assertIn("en curso", str(caught.exception))
        finally:
            other.rollback()
            other.close()
        staged.refresh_from_db()
        self.assertEqual(staged.status, ScheduleImportStatus.PENDING)
