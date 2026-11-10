"""La migración 0027 lleva una base con datos reales del esquema viejo al nuevo y de vuelta.

Usa `MigrationExecutor` contra PostgreSQL: las filas se insertan con los modelos históricos
de 0026, se migra hacia delante, se comprueban con los nombres nuevos y se deshace.
`TransactionTestCase` porque cada migración es DDL con su propia transacción, y dentro de
la de un `TestCase` chocaría con los triggers diferidos de las FK.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from .dbsupport import SKIP_REASON, setup_test_database, teardown_test_database

setUpModule = setup_test_database
tearDownModule = teardown_test_database

BEFORE = [("core", "0026_equipment_transport")]
AFTER = [("core", "0027_rename_visit_to_record")]
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def migrate_to(targets):
    """Migra y devuelve los modelos históricos de ese punto del grafo."""
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return MigrationExecutor(connection).loader.project_state(targets).apps


def tables() -> set[str]:
    return set(connection.introspection.table_names())


def catalog_names() -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid::regclass::text IN "
            "('record','record_attachment','record_review','visit','visit_attachment','visit_review')"
        )
        names = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename IN "
            "('record','record_attachment','record_review','visit','visit_attachment','visit_review')"
        )
        return names + [row[0] for row in cursor.fetchall()]


@unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")
class RenameMigrationTests(TransactionTestCase):
    def setUp(self):
        self.apps = migrate_to(BEFORE)
        self.addCleanup(migrate_to, AFTER)
        self._seed()

    def _seed(self):
        apps = self.apps
        org = apps.get_model("core", "Organization").objects.create(name="Obra")
        user_model = apps.get_model("core", "AppUser")
        self.user = user_model.objects.create(username="supervisora", password="x", org=org)
        device = apps.get_model("core", "Device").objects.create(
            id=uuid.uuid4(), org=org, owner=self.user, platform="android", app_version="1"
        )
        template = apps.get_model("core", "FormTemplate").objects.create(org=org, name="F")
        version = apps.get_model("core", "FormTemplateVersion").objects.create(
            org=org,
            template=template,
            version_number=1,
            change_seq=1,
            definition={"dsl": "visitacampo/form@1", "fields": []},
        )
        self.version_id = version.pk
        common = {"org": org, "captured_at": NOW, "change_seq": 2}
        visit = apps.get_model("core", "Visit").objects.create(
            id=uuid.uuid4(),
            template_version=version,
            device=device,
            created_by=self.user,
            answers={"visit_id": "campo del formulario"},
            **common,
        )
        self.visit_id = visit.pk
        self.attachment_id = (
            apps.get_model("core", "VisitAttachment")
            .objects.create(
                id=uuid.uuid4(),
                visit=visit,
                blob_id="a" * 64,
                role="photo",
                **common,
            )
            .pk
        )
        self.review_id = (
            apps.get_model("core", "VisitReview")
            .objects.create(org=org, visit=visit, status="approved", reviewer=self.user)
            .pk
        )
        self.group = apps.get_model("auth", "Group").objects.create(name="supervisión")
        permissions = apps.get_model("auth", "Permission").objects
        self.view_pk = permissions.get(codename="view_visit").pk
        self.review_pk = permissions.get(codename="change_visitreview").pk
        self.group.permissions.add(self.view_pk)
        self.user.user_permissions.add(self.review_pk)

        op_log = apps.get_model("core", "SyncOpLog").objects
        self.op_rejected, self.op_attachment, self.op_conflict = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        base = {"org": org, "device": device, "batch_uuid": uuid.uuid4()}
        op_log.create(
            op_uuid=self.op_rejected,
            entity="visit",
            entity_id=visit.pk,
            result={
                "entity": "visit",
                "status": "rejected",
                "server_row": None,
                "error": {
                    "message": "The visit was validated: it cannot be deleted.",
                    "details": {"field": "visit_id", "reason": "visit_validated"},
                },
            },
            **base,
        )
        op_log.create(
            op_uuid=self.op_attachment,
            entity="visit_attachment",
            entity_id=self.attachment_id,
            result={
                "entity": "visit_attachment",
                "status": "rejected",
                "server_row": None,
                "error": {
                    "message": "Referenced visit_id does not exist in this organization.",
                    "details": {"field": "visit_id", "reason": "unknown_reference"},
                },
            },
            **base,
        )
        op_log.create(
            op_uuid=self.op_conflict,
            entity="visit_attachment",
            entity_id=self.attachment_id,
            result={
                "entity": "visit_attachment",
                "status": "conflict",
                "server_row": {
                    "entity": "visit_attachment",
                    "data": {"visit_id": str(visit.pk), "role": "photo"},
                },
                "error": {"details": {"field": "base_version", "reason": "stale"}},
            },
            **base,
        )
        self.op_record_conflict, self.op_nested, self.op_record_nested = (
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        # Un conflicto de registro: sus respuestas son del usuario, aunque una clave
        # se llame como la FK vieja.
        op_log.create(
            op_uuid=self.op_record_conflict,
            entity="visit",
            entity_id=visit.pk,
            result={
                "entity": "visit",
                "status": "conflict",
                "server_row": {
                    "entity": "visit",
                    "data": {"id": str(visit.pk), "answers": {"visit_id": "respuesta"}},
                },
                "error": {"details": {"field": "base_version", "reason": "stale"}},
            },
            **base,
        )
        nested_errors = {
            "errors": [
                {"field": "visit_id", "reason": "missing"},
                {"field": "role", "reason": "enum"},
            ]
        }
        op_log.create(
            op_uuid=self.op_nested,
            entity="visit_attachment",
            entity_id=self.attachment_id,
            result={
                "entity": "visit_attachment",
                "status": "rejected",
                "server_row": None,
                "error": {"message": "Payload failed validation.", "details": nested_errors},
            },
            **base,
        )
        op_log.create(
            op_uuid=self.op_record_nested,
            entity="visit",
            entity_id=visit.pk,
            result={
                "entity": "visit",
                "status": "rejected",
                "server_row": None,
                "error": {"message": "Payload failed validation.", "details": nested_errors},
            },
            **base,
        )
        self.audit_id = (
            apps.get_model("core", "AuditEvent")
            .objects.create(
                org=org,
                action="backoffice.visit_reviewed",
                target=f"visit:{visit.pk}",
                metadata={"status": "approved"},
            )
            .pk
        )

    def test_forwards_and_back_preserve_every_row(self):
        apps = migrate_to(AFTER)
        self.assertLessEqual({"record", "record_attachment", "record_review"}, tables())
        self.assertFalse({"visit", "visit_attachment", "visit_review"} & tables())
        self.assertFalse([name for name in catalog_names() if "visit" in name])
        self.assertIn("record_attachment_record_org_fk", catalog_names())

        record = apps.get_model("core", "Record").objects.get(pk=self.visit_id)
        self.assertEqual(record.answers, {"visit_id": "campo del formulario"})
        attachment = apps.get_model("core", "RecordAttachment").objects.get(pk=self.attachment_id)
        self.assertEqual(attachment.record_id, self.visit_id)
        review = apps.get_model("core", "RecordReview").objects.get(pk=self.review_id)
        self.assertEqual(review.record_id, self.visit_id)

        permissions = apps.get_model("auth", "Permission").objects
        view = permissions.get(pk=self.view_pk)
        self.assertEqual((view.codename, view.name), ("view_record", "Can view registro"))
        self.assertEqual(view.content_type.model, "record")
        self.assertEqual(
            permissions.get(pk=self.review_pk).codename,
            "change_recordreview",
        )
        self.assertFalse(permissions.filter(codename__contains="visit").exists())
        self.assertEqual(
            list(self.group.permissions.through.objects.values_list("permission_id", flat=True)),
            [self.view_pk],
        )
        self.assertEqual(
            list(
                self.user.user_permissions.through.objects.values_list("permission_id", flat=True)
            ),
            [self.review_pk],
        )

        results = apps.get_model("core", "SyncOpLog").objects
        rejected = results.get(pk=self.op_rejected)
        self.assertEqual(rejected.entity, "record")
        self.assertEqual(rejected.result["entity"], "record")
        details = rejected.result["error"]["details"]
        self.assertEqual(details["reason"], "record_validated")
        # En un registro `field` puede ser un campo del formulario: no se toca.
        self.assertEqual(details["field"], "visit_id")
        self.assertEqual(
            rejected.result["error"]["message"],
            "The record was validated: it cannot be deleted.",
        )
        attachment_op = results.get(pk=self.op_attachment)
        self.assertEqual(attachment_op.entity, "record_attachment")
        self.assertEqual(attachment_op.result["error"]["details"]["field"], "record_id")
        conflict = results.get(pk=self.op_conflict)
        self.assertEqual(conflict.result["server_row"]["entity"], "record_attachment")
        self.assertEqual(conflict.result["server_row"]["data"]["record_id"], str(self.visit_id))
        self.assertNotIn("visit_id", conflict.result["server_row"]["data"])

        record_conflict = results.get(pk=self.op_record_conflict).result
        self.assertEqual(record_conflict["server_row"]["entity"], "record")
        self.assertEqual(
            record_conflict["server_row"]["data"]["answers"], {"visit_id": "respuesta"}
        )
        self.assertEqual(
            results.get(pk=self.op_nested).result["error"]["details"]["errors"],
            [{"field": "record_id", "reason": "missing"}, {"field": "role", "reason": "enum"}],
        )
        self.assertEqual(
            results.get(pk=self.op_record_nested).result["error"]["details"]["errors"][0],
            {"field": "visit_id", "reason": "missing"},
        )

        audit = apps.get_model("core", "AuditEvent").objects.get(pk=self.audit_id)
        self.assertEqual(audit.action, "backoffice.visit_reviewed")
        self.assertEqual(audit.target, f"visit:{self.visit_id}")
        self.assertEqual(
            apps.get_model("core", "FormTemplateVersion")
            .objects.get(pk=self.version_id)
            .definition["dsl"],
            "suno/form@1",
        )

        old_apps = migrate_to(BEFORE)
        self.assertLessEqual({"visit", "visit_attachment", "visit_review"}, tables())
        self.assertFalse({"record", "record_attachment", "record_review"} & tables())
        self.assertFalse([name for name in catalog_names() if "record" in name])

        visit = old_apps.get_model("core", "Visit").objects.get(pk=self.visit_id)
        self.assertEqual(visit.answers, {"visit_id": "campo del formulario"})
        self.assertEqual(
            old_apps.get_model("core", "VisitAttachment")
            .objects.get(pk=self.attachment_id)
            .visit_id,
            self.visit_id,
        )
        self.assertEqual(
            old_apps.get_model("core", "VisitReview").objects.get(pk=self.review_id).visit_id,
            self.visit_id,
        )
        old_permissions = old_apps.get_model("auth", "Permission").objects
        self.assertEqual(
            (
                old_permissions.get(pk=self.view_pk).codename,
                old_permissions.get(pk=self.view_pk).name,
            ),
            ("view_visit", "Can view visita"),
        )
        self.assertEqual(old_permissions.get(pk=self.view_pk).content_type.model, "visit")
        self.assertTrue(
            old_apps.get_model("auth", "Group")
            .objects.get(pk=self.group.pk)
            .permissions.filter(pk=self.view_pk)
            .exists()
        )
        self.assertTrue(
            old_apps.get_model("core", "AppUser")
            .objects.get(pk=self.user.pk)
            .user_permissions.filter(pk=self.review_pk)
            .exists()
        )
        old_results = old_apps.get_model("core", "SyncOpLog").objects
        restored = old_results.get(pk=self.op_rejected)
        self.assertEqual(restored.entity, "visit")
        self.assertEqual(restored.result["error"]["details"]["reason"], "visit_validated")
        restored_conflict = old_results.get(pk=self.op_conflict)
        self.assertEqual(restored_conflict.entity, "visit_attachment")
        self.assertEqual(
            restored_conflict.result["server_row"]["data"]["visit_id"], str(self.visit_id)
        )
        self.assertEqual(
            old_results.get(pk=self.op_nested).result["error"]["details"]["errors"][0],
            {"field": "visit_id", "reason": "missing"},
        )
        self.assertEqual(
            old_results.get(pk=self.op_record_conflict).result["server_row"],
            {
                "entity": "visit",
                "data": {"id": str(self.visit_id), "answers": {"visit_id": "respuesta"}},
            },
        )
        self.assertEqual(
            old_apps.get_model("core", "AuditEvent").objects.get(pk=self.audit_id).action,
            "backoffice.visit_reviewed",
        )

    def test_merges_grants_when_the_new_permission_already_exists(self):
        permissions = self.apps.get_model("auth", "Permission").objects
        visit_view = permissions.get(pk=self.view_pk)
        extra_view = permissions.create(
            content_type=visit_view.content_type,
            codename="view_record",
            name="Can view registro",
        )
        other = self.apps.get_model("auth", "Group").objects.create(name="otro")
        other.permissions.add(extra_view)
        apps = migrate_to(AFTER)
        new_permissions = apps.get_model("auth", "Permission").objects
        self.assertEqual(new_permissions.filter(codename="view_record").count(), 1)
        self.assertFalse(new_permissions.filter(codename="view_visit").exists())
        survivor = new_permissions.get(codename="view_record")
        groups = apps.get_model("auth", "Group").objects
        for group in (self.group, other):
            self.assertTrue(groups.get(pk=group.pk).permissions.filter(pk=survivor.pk).exists())

    def test_merges_a_user_holding_both_permissions_and_reverses_after(self):
        permissions = self.apps.get_model("auth", "Permission").objects
        old = permissions.get(pk=self.review_pk)
        new = permissions.create(
            content_type=old.content_type,
            codename="change_recordreview",
            name="Can change revisión de registro",
        )
        user = self.apps.get_model("core", "AppUser").objects.get(pk=self.user.pk)
        user.user_permissions.add(new)  # ya tenía la vieja
        apps = migrate_to(AFTER)
        merged = apps.get_model("auth", "Permission").objects.filter(codename="change_recordreview")
        self.assertEqual(list(merged.values_list("pk", flat=True)), [new.pk])
        grants = apps.get_model("core", "AppUser").user_permissions.through.objects
        self.assertEqual(
            list(grants.filter(appuser_id=self.user.pk).values_list("permission_id", flat=True)),
            [new.pk],
        )

        old_apps = migrate_to(BEFORE)
        restored = old_apps.get_model("auth", "Permission").objects
        self.assertFalse(restored.filter(codename="change_recordreview").exists())
        self.assertEqual(restored.get(pk=new.pk).codename, "change_visitreview")
        self.assertTrue(
            old_apps.get_model("core", "AppUser")
            .objects.get(pk=self.user.pk)
            .user_permissions.filter(pk=new.pk)
            .exists()
        )
        self.assertTrue(
            old_apps.get_model("auth", "Group")
            .objects.get(pk=self.group.pk)
            .permissions.filter(codename="view_visit")
            .exists()
        )
