"""Push de registros con avance de campo (costos, etapa 1): referencias, permisos
y que el pull devuelva lo guardado. Requiere PostgreSQL."""

from __future__ import annotations

import unittest
import uuid
from decimal import Decimal
from typing import Any

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from suno.authn.tests.factories import make_org, make_user
from suno.core.context import Principal, resolve_request_context
from suno.core.models import Project, Role, Task, TaskResponsible, RecordReview
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.projects.importer import apply_import, stage_import
from suno.projects.tests.samples import v1, v2
from suno.sync.cursor import CURSOR_ZERO

from .factories import build_scenario, create_op, delete_op, pull, push, update_op, record_of

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class TaskProgressPushTests(TestCase):
    """El agente del escenario es responsable de la etapa 1 (uid 1): cubre 1.1 y 1.2."""

    def setUp(self) -> None:
        self.scenario = build_scenario()
        self.admin = make_user(self.scenario.org, username="admin-cronograma", role=Role.ADMIN)
        self.admin_ctx = resolve_request_context(
            Principal(user_id=self.admin.pk, org_id=self.scenario.org.pk)
        )
        self.project = Project.objects.create(
            org=self.scenario.org, name="Obra", created_by=self.admin
        )
        self.apply(v1())
        self.assign(1)

    def apply(self, data: bytes) -> None:
        staged = stage_import(self.admin_ctx, self.project, file_name="x.xml", data=data)
        apply_import(self.admin_ctx, staged.pk)

    def assign(self, uid: int) -> None:
        TaskResponsible.objects.create(
            org=self.scenario.org,
            task=self.task(uid),
            user=self.scenario.user,
            assigned_by=self.admin,
        )

    def task(self, uid: int) -> Task:
        return Task.objects.get(project=self.project, uid=uid)

    def push_one(self, op: dict[str, Any]) -> dict[str, Any]:
        response = push(self.scenario, [op])
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["results"][0]

    def linked(self, uid: int, pct: object, record_id: uuid.UUID | None = None) -> dict[str, Any]:
        return create_op(
            self.scenario, record_id=record_id, task_id=str(self.task(uid).pk), progress_pct=pct
        )

    def assertRejected(self, result: dict[str, Any], code: str, field: str, reason: str) -> None:
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["error"]["code"], code)
        self.assertEqual(result["error"]["details"], {"field": field, "reason": reason})

    def test_responsible_by_branch_reports_progress(self) -> None:
        record_id = uuid.uuid4()
        result = self.push_one(self.linked(3, 35.5, record_id))
        self.assertEqual(result["status"], "applied", result)
        record = record_of(record_id)
        self.assertEqual((record.task_id, record.progress_pct), (self.task(3).pk, Decimal("35.50")))

    def test_direct_responsible_reports_progress(self) -> None:
        self.assign(5)
        self.assertEqual(self.push_one(self.linked(5, 10))["status"], "applied")

    def test_someone_else_s_task_is_forbidden(self) -> None:
        self.assertRejected(
            self.push_one(self.linked(5, 10)), "forbidden", "task_id", "not_responsible"
        )

    def test_a_summary_task_is_rejected(self) -> None:
        self.assertRejected(
            self.push_one(self.linked(1, 10)), "validation_error", "task_id", "task_is_summary"
        )

    def test_a_retired_task_is_rejected(self) -> None:
        self.assign(4)
        columnas = self.task(5)
        self.apply(v2())  # retira la 2.1 Columnas
        op = create_op(self.scenario, task_id=str(columnas.pk), progress_pct=10)
        self.assertRejected(self.push_one(op), "validation_error", "task_id", "task_retired")

    def test_a_task_of_another_organization_does_not_exist(self) -> None:
        other = make_org("Ajena")
        owner = make_user(other, username="admin-ajena", role=Role.ADMIN)
        project = Project.objects.create(org=other, name="Ajena", created_by=owner)
        foreign = Task.objects.create(
            org=other,
            project=project,
            uid=1,
            outline_number="1",
            outline_level=1,
            sort_order=1,
            name="Ajena",
        )
        op = create_op(self.scenario, task_id=str(foreign.pk), progress_pct=10)
        self.assertRejected(self.push_one(op), "not_found", "task_id", "unknown_reference")

    def test_progress_out_of_range_is_rejected(self) -> None:
        result = self.push_one(self.linked(3, 100.01))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["code"], "validation_error")
        fields = {e["field"] for e in result["error"]["details"]["errors"]}
        self.assertIn("progress_pct", fields)

    def test_a_task_without_progress_is_rejected(self) -> None:
        op = create_op(self.scenario, task_id=str(self.task(3).pk))
        self.assertRejected(
            self.push_one(op), "validation_error", "progress_pct", "task_progress_pair"
        )

    def test_update_moves_the_progress_and_needs_the_pair(self) -> None:
        record_id = uuid.uuid4()
        self.push_one(self.linked(3, 20, record_id))
        version = record_of(record_id).version
        both = update_op(record_id, version, task_id=str(self.task(3).pk), progress_pct=50)
        self.assertEqual(self.push_one(both)["status"], "applied")
        self.assertEqual(record_of(record_id).progress_pct, Decimal("50.00"))

        only_pct = update_op(record_id, version + 1, progress_pct=60)
        self.assertRejected(
            self.push_one(only_pct), "validation_error", "task_id", "task_progress_pair"
        )

    def test_unlinking_needs_no_responsibility(self) -> None:
        record_id = uuid.uuid4()
        self.push_one(self.linked(3, 20, record_id))
        TaskResponsible.objects.filter(user=self.scenario.user).delete()
        version = record_of(record_id).version
        result = self.push_one(update_op(record_id, version, task_id=None, progress_pct=None))
        self.assertEqual(result["status"], "applied", result)
        record = record_of(record_id)
        self.assertEqual((record.task_id, record.progress_pct), (None, None))

    def test_pull_returns_task_and_progress(self) -> None:
        record_id = uuid.uuid4()
        self.push_one(self.linked(3, 35.5, record_id))
        rows = pull(self.scenario, CURSOR_ZERO).json()["rows"]
        data = next(
            r["data"] for r in rows if r["entity"] == "record" and r["data"]["id"] == str(record_id)
        )
        self.assertEqual((data["task_id"], data["progress_pct"]), (str(self.task(3).pk), 35.5))

    def test_linked_operations_cost_one_query_each(self) -> None:
        """La resolución de tareas va en bloque: no crece con el lote (RD-07)."""

        def queries(size: int) -> int:
            ops = [self.linked(3, 10) for _ in range(size)]
            with CaptureQueriesContext(connection) as captured:
                response = push(self.scenario, ops)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertTrue(all(r["status"] == "applied" for r in response.json()["results"]))
            return len(captured.captured_queries)

        self.assertEqual(queries(200), queries(1) + 199)


@skip_without_db
class ValidatedProgressImmutableTests(TaskProgressPushTests):
    """Un registro validado no cambia su tarea ni su % sin una nueva revisión."""

    def validated_record(self, status: str = "approved") -> uuid.UUID:
        record_id = uuid.uuid4()
        self.push_one(self.linked(3, 20, record_id))
        RecordReview.objects.create(
            org=self.scenario.org,
            record=record_of(record_id),
            status=status,
            reviewer=self.admin,
        )
        return record_id

    def update(self, record_id: uuid.UUID, uid: int | None, pct: object) -> dict[str, Any]:
        task_id = None if uid is None else str(self.task(uid).pk)
        op = update_op(record_id, record_of(record_id).version, task_id=task_id, progress_pct=pct)
        return self.push_one(op)

    def test_approved_progress_change_is_rejected(self) -> None:
        record_id = self.validated_record()
        self.assertRejected(
            self.update(record_id, 3, 50), "validation_error", "progress_pct", "record_validated"
        )
        self.assertEqual(record_of(record_id).progress_pct, Decimal("20.00"))

    def test_approved_task_change_is_rejected(self) -> None:
        record_id = self.validated_record()
        self.assertRejected(
            self.update(record_id, 2, 20), "validation_error", "task_id", "record_validated"
        )

    def test_approved_unlinking_is_rejected(self) -> None:
        record_id = self.validated_record()
        self.assertRejected(
            self.update(record_id, None, None), "validation_error", "task_id", "record_validated"
        )

    def test_approved_same_values_are_accepted(self) -> None:
        record_id = self.validated_record()
        self.assertEqual(self.update(record_id, 3, 20)["status"], "applied")

    def test_approved_update_without_the_pair_is_accepted(self) -> None:
        record_id = self.validated_record()
        op = update_op(record_id, record_of(record_id).version, lat=5.0)
        self.assertEqual(self.push_one(op)["status"], "applied")

    def test_rejected_review_allows_the_change(self) -> None:
        record_id = self.validated_record("rejected")
        self.assertEqual(self.update(record_id, 3, 50)["status"], "applied")

    def test_a_later_rejection_allows_the_change(self) -> None:
        record_id = self.validated_record()
        RecordReview.objects.create(
            org=self.scenario.org,
            record=record_of(record_id),
            status="rejected",
            reviewer=self.admin,
        )
        self.assertEqual(self.update(record_id, 3, 50)["status"], "applied")

    def test_no_review_allows_the_change(self) -> None:
        record_id = uuid.uuid4()
        self.push_one(self.linked(3, 20, record_id))
        self.assertEqual(self.update(record_id, 3, 50)["status"], "applied")

    def test_validated_check_costs_one_query_for_the_batch(self) -> None:
        ids = [self.validated_record() for _ in range(5)]

        def queries(targets: list[uuid.UUID]) -> int:
            ops = [
                update_op(v, record_of(v).version, task_id=str(self.task(3).pk), progress_pct=20)
                for v in targets
            ]
            with CaptureQueriesContext(connection) as captured:
                response = push(self.scenario, ops)
            self.assertEqual(response.status_code, 200, response.content)
            return len(captured.captured_queries)

        self.assertEqual(queries(ids), queries(ids[:1]) + 4)

    def test_a_stale_update_conflicts_instead_of_being_rejected(self) -> None:
        record_id = self.validated_record()
        old_version = record_of(record_id).version
        self.push_one(update_op(record_id, old_version, lat=5.0))
        stale = update_op(record_id, old_version, task_id=str(self.task(3).pk), progress_pct=50)
        result = self.push_one(stale)
        self.assertEqual(result["status"], "conflict", result)
        self.assertIsNotNone(result["server_row"])

    def test_approved_record_cannot_be_deleted(self) -> None:
        record_id = self.validated_record()
        result = self.push_one(delete_op(record_id, record_of(record_id).version))
        self.assertRejected(result, "validation_error", "id", "record_validated")
        self.assertIsNone(record_of(record_id).deleted_at)

    def test_stale_delete_of_an_approved_record_conflicts(self) -> None:
        record_id = self.validated_record()
        old_version = record_of(record_id).version
        self.push_one(update_op(record_id, old_version, lat=5.0))
        self.assertEqual(self.push_one(delete_op(record_id, old_version))["status"], "conflict")

    def test_rejected_or_unreviewed_records_can_be_deleted(self) -> None:
        rejected = self.validated_record("rejected")
        self.assertEqual(
            self.push_one(delete_op(rejected, record_of(rejected).version))["status"], "applied"
        )
        plain = uuid.uuid4()
        self.push_one(self.linked(3, 20, plain))
        self.assertEqual(
            self.push_one(delete_op(plain, record_of(plain).version))["status"], "applied"
        )

    def test_the_validated_rows_are_locked(self) -> None:
        record_id = self.validated_record()
        op = update_op(
            record_id,
            record_of(record_id).version,
            lat=5.0,
            progress_pct=20,
            task_id=str(self.task(3).pk),
        )
        with CaptureQueriesContext(connection) as captured:
            push(self.scenario, [op])
        self.assertTrue(
            any(
                "FOR UPDATE" in q["sql"] and "record_review" in q["sql"]
                for q in captured.captured_queries
            )
        )
