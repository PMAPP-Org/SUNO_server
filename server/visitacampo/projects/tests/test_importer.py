"""Importación de cronogramas: vista previa y aplicación."""

from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import Project, Role, ScheduleImportStatus
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.importer import ImportRejected, stage_import, summarize_changes
from visitacampo.projects.mspdi import parse_mspdi

from .samples import project_xml, task_xml, v1

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

    def test_stage_rejects_invalid_file(self) -> None:
        with self.assertRaises(ImportRejected) as caught:
            stage_import(self.ctx, self.project, file_name="x.xml", data=b"nada")
        self.assertIn("no es un XML", str(caught.exception))
