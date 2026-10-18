"""Acciones del back-office (Paso 24): políticas, aislamiento y auditoría.

Las acciones se prueban directamente contra los servicios, no a través del
Admin: la política tiene que valer aunque alguien llame al servicio desde
otro sitio (un comando, otra vista).
"""

from __future__ import annotations

import csv
import io
import unittest
import uuid
import zipfile
from unittest import mock
from xml.etree import ElementTree

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from visitacampo.authn.models import RefreshToken
from visitacampo.backoffice import export
from visitacampo.backoffice.export import ExportTooLarge, safe_cell, to_csv, to_xlsx, visit_table
from visitacampo.backoffice.personal_data import (
    SUPPRESSED_PREFIX,
    export_personal_data,
    suppress_personal_data,
)
from visitacampo.backoffice.services import (
    create_assignment,
    retry_job,
    review_visit,
    revoke_device_sessions,
)
from visitacampo.core.audit import AuditAction
from visitacampo.core.models import AuditEvent, Job, Role, Visit, VisitReview
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.sync.changeseq import highest_change_seq

from .factories import Tenant, make_tenant

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


def live_token(tenant: Tenant) -> RefreshToken:
    return RefreshToken.objects.create(
        org=tenant.org,
        selector=uuid.uuid4().hex[:22],
        verifier_hash="h" * 64,
        device=tenant.device,
        user=tenant.agent,
        expires_at=timezone.now() + timezone.timedelta(days=1),
    )


def audited(action: str) -> bool:
    return AuditEvent.objects.filter(action=action).exists()


@skip_without_db
class ReviewTests(TestCase):
    def setUp(self) -> None:
        self.a = make_tenant("Org A", staff_role=Role.SUPERVISOR)
        self.b = make_tenant("Org B")

    def test_supervision_reviews_and_it_is_audited(self) -> None:
        visit = self.a.visit()
        review = review_visit(self.a.ctx(), visit, status="rejected", comment="  falta la foto  ")
        self.assertEqual(review.org_id, self.a.org.pk)
        self.assertEqual(review.reviewer_id, self.a.staff.pk)
        self.assertEqual(review.comment, "falta la foto")
        self.assertTrue(audited(AuditAction.VISIT_REVIEWED))

    def test_each_decision_is_a_new_row(self) -> None:
        visit = self.a.visit()
        review_visit(self.a.ctx(), visit, status="rejected")
        review_visit(self.a.ctx(), visit, status="approved")
        self.assertEqual(VisitReview.objects.filter(visit=visit).count(), 2)

    def test_a_field_agent_cannot_review(self) -> None:
        with self.assertRaises(PermissionDenied):
            review_visit(self.a.ctx(self.a.agent), self.a.visit(), status="approved")

    def test_a_foreign_visit_cannot_be_reviewed(self) -> None:
        with self.assertRaises(PermissionDenied):
            review_visit(self.a.ctx(), self.b.visit(), status="approved")
        self.assertFalse(VisitReview.objects.exists())

    def test_an_unknown_decision_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            review_visit(self.a.ctx(), self.a.visit(), status="maybe")


@skip_without_db
class RevokeDeviceTests(TestCase):
    def setUp(self) -> None:
        self.a = make_tenant("Org A")
        self.b = make_tenant("Org B")

    def test_revokes_every_live_session_of_the_device(self) -> None:
        live_token(self.a), live_token(self.a)
        other = live_token(self.b)
        self.assertEqual(revoke_device_sessions(self.a.ctx(), self.a.device), 2)
        self.assertFalse(
            RefreshToken.objects.filter(device=self.a.device, revoked_at__isnull=True).exists()
        )
        other.refresh_from_db()
        self.assertIsNone(other.revoked_at, "las sesiones de otra organización no se tocan")
        self.assertTrue(audited(AuditAction.DEVICE_REVOKED))

    def test_a_foreign_device_cannot_be_revoked(self) -> None:
        token = live_token(self.b)
        with self.assertRaises(PermissionDenied):
            revoke_device_sessions(self.a.ctx(), self.b.device)
        token.refresh_from_db()
        self.assertIsNone(token.revoked_at)


@skip_without_db
class AssignmentTests(TestCase):
    def setUp(self) -> None:
        self.a = make_tenant("Org A", staff_role=Role.SUPERVISOR)
        self.b = make_tenant("Org B")

    def test_gets_the_next_change_seq_so_the_phone_pulls_it(self) -> None:
        before = highest_change_seq(self.a.ctx())
        assignment = create_assignment(
            self.a.ctx(), template_version=self.a.version, assigned_to=self.a.agent, title="Finca 4"
        )
        self.assertEqual(assignment.change_seq, before + 1)
        self.assertEqual(assignment.org_id, self.a.org.pk)
        self.assertTrue(audited(AuditAction.ASSIGNMENT_CREATED))

    def test_cannot_mix_organizations(self) -> None:
        for version, agent in ((self.b.version, self.a.agent), (self.a.version, self.b.agent)):
            with self.subTest(version=version.pk), self.assertRaises(PermissionDenied):
                create_assignment(self.a.ctx(), template_version=version, assigned_to=agent)

    def test_only_published_versions(self) -> None:
        self.a.version.published_at = None
        self.a.version.save()
        with self.assertRaises(ValueError):
            create_assignment(
                self.a.ctx(), template_version=self.a.version, assigned_to=self.a.agent
            )

    def test_a_field_agent_cannot_assign(self) -> None:
        with self.assertRaises(PermissionDenied):
            create_assignment(
                self.a.ctx(self.a.agent), template_version=self.a.version, assigned_to=self.a.agent
            )


@skip_without_db
class RetryJobTests(TestCase):
    def setUp(self) -> None:
        self.a = make_tenant("Org A")
        self.b = make_tenant("Org B")

    def test_a_dead_letter_job_goes_back_to_pending(self) -> None:
        job = Job.objects.create(
            org=self.a.org, type="t", status="dead_letter", attempts=5, error={"msg": "boom"}
        )
        self.assertTrue(retry_job(self.a.ctx(), job))
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts), ("pending", 0))
        self.assertEqual(job.error, {"msg": "boom"}, "la pista del fallo se conserva")
        self.assertTrue(audited(AuditAction.JOB_RETRIED))

    def test_a_running_job_is_left_alone(self) -> None:
        job = Job.objects.create(org=self.a.org, type="t", status="running")
        self.assertFalse(retry_job(self.a.ctx(), job))
        self.assertFalse(audited(AuditAction.JOB_RETRIED))

    def test_a_foreign_job_cannot_be_retried(self) -> None:
        job = Job.objects.create(org=self.b.org, type="t", status="failed")
        with self.assertRaises(PermissionDenied):
            retry_job(self.a.ctx(), job)


@skip_without_db
class ExportTests(TestCase):
    def setUp(self) -> None:
        self.a = make_tenant("Org A", staff_role=Role.SUPERVISOR)
        self.b = make_tenant("Org B")

    def test_only_the_own_organization_reaches_the_file(self) -> None:
        own = self.a.visit(altura_cm=12, notas='=HYPERLINK("http://x")')
        self.b.visit()
        header, rows = visit_table(self.a.ctx(), Visit.objects.all())
        self.assertEqual([row[0] for row in rows], [str(own.pk)])
        self.assertIn("answers.altura_cm", header)
        notes = rows[0][header.index("answers.notas")]
        self.assertTrue(notes.startswith("'="), "la fórmula sale como texto")
        self.assertEqual(rows[0][header.index("answers.altura_cm")], 12)

    def test_the_latest_review_is_exported(self) -> None:
        visit = self.a.visit()
        review_visit(self.a.ctx(), visit, status="rejected")
        review_visit(self.a.ctx(), visit, status="approved")
        header, rows = visit_table(self.a.ctx(), Visit.objects.all())
        self.assertEqual(rows[0][header.index("revision")], "approved")

    def test_over_the_limit_it_refuses_instead_of_truncating(self) -> None:
        self.a.visit(), self.a.visit()
        with mock.patch.object(export, "MAX_EXPORT_ROWS", 1), self.assertRaises(ExportTooLarge):
            visit_table(self.a.ctx(), Visit.objects.all())

    def test_query_count_does_not_grow_with_the_rows(self) -> None:
        """Paso 25, tarea 2 (H-07): coste fijo en queries, sin N+1 por fila."""

        def queries_for(count: int) -> int:
            tenant = make_tenant(f"Org {count} filas", staff_role=Role.SUPERVISOR)
            for _ in range(count):
                review_visit(tenant.ctx(), tenant.visit(), status="approved")
            ctx = tenant.ctx()
            with CaptureQueriesContext(connection) as captured:
                visit_table(ctx, Visit.objects.all())
            return len(captured.captured_queries)

        self.assertEqual(queries_for(1), queries_for(30))

    def test_csv_and_xlsx_are_readable(self) -> None:
        self.a.visit(notas="ñandú & <b>")
        header, rows = visit_table(self.a.ctx(), Visit.objects.all())

        data = to_csv(header, rows)
        self.assertTrue(data.startswith("﻿".encode()))
        parsed = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(parsed[0], header)
        self.assertIn("ñandú & <b>", parsed[1])

        with zipfile.ZipFile(io.BytesIO(to_xlsx(header, rows))) as book:
            self.assertIsNone(book.testzip())
            self.assertIn("[Content_Types].xml", book.namelist())
            sheet = book.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("ñandú &amp; &lt;b&gt;", sheet, "el XML va escapado")

    def test_control_characters_do_not_break_the_xlsx(self) -> None:
        """Un carácter de control pegado en una respuesta no puede dejar el
        libro ilegible: XML 1.0 no lo admite ni escapado."""
        self.a.visit(notas="antes\x01\x0b\x1fdespués\tcon tab")
        header, rows = visit_table(self.a.ctx(), Visit.objects.all())
        with zipfile.ZipFile(io.BytesIO(to_xlsx(header, rows))) as book:
            for name in book.namelist():
                with self.subTest(part=name):
                    ElementTree.fromstring(book.read(name))  # bien formado
            sheet = book.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("antesdespués\tcon tab", sheet)


class SafeCellTests(TestCase):
    def test_formula_prefixes_are_neutralized(self) -> None:
        for value in ("=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx"):
            with self.subTest(value=value):
                self.assertEqual(safe_cell(value), f"'{value}")

    def test_numbers_and_plain_text_are_kept(self) -> None:
        self.assertEqual(safe_cell(-3), -3, "un número negativo sigue siendo un número")
        self.assertEqual(safe_cell("hola"), "hola")
        self.assertEqual(safe_cell(None), "")
        self.assertEqual(safe_cell(True), "sí")
        self.assertEqual(safe_cell(["=x"]), '["=x"]', "una lista se serializa y no empieza por =")


@skip_without_db
class PersonalDataTests(TestCase):
    def setUp(self) -> None:
        self.a = make_tenant("Org A")
        self.b = make_tenant("Org B")

    def test_export_contains_the_person_and_their_traces(self) -> None:
        visit = self.a.visit()
        data = export_personal_data(self.a.ctx(), self.a.agent)
        self.assertEqual(data["usuario"]["usuario"], self.a.agent.username)
        self.assertEqual([v["id"] for v in data["visitas"]], [str(visit.pk)])
        self.assertEqual([d["id"] for d in data["dispositivos"]], [str(self.a.device.pk)])
        self.assertTrue(audited(AuditAction.PERSONAL_DATA_EXPORTED))

    def test_suppression_anonymizes_and_closes_everything(self) -> None:
        visit = self.a.visit()
        token = live_token(self.a)
        TOTPDevice.objects.create(user=self.a.agent, name="tel", confirmed=True)
        suppress_personal_data(self.a.ctx(), self.a.agent)

        agent = type(self.a.agent).objects.get(pk=self.a.agent.pk)
        self.assertTrue(agent.username.startswith(SUPPRESSED_PREFIX))
        self.assertEqual((agent.first_name, agent.last_name, agent.email), ("", "", ""))
        self.assertFalse(agent.is_active)
        self.assertFalse(agent.has_usable_password())
        token.refresh_from_db()
        self.assertIsNotNone(token.revoked_at)
        self.assertFalse(TOTPDevice.objects.filter(user=agent).exists())
        self.a.device.refresh_from_db()
        self.assertIsNone(self.a.device.display_name)
        self.assertTrue(
            Visit.objects.filter(pk=visit.pk).exists(), "las visitas son de la organización"
        )
        self.assertTrue(audited(AuditAction.PERSONAL_DATA_SUPPRESSED))

    def test_no_one_suppresses_themselves(self) -> None:
        with self.assertRaises(PermissionDenied):
            suppress_personal_data(self.a.ctx(), self.a.staff)

    def test_another_organization_cannot_touch_the_person(self) -> None:
        for action in (export_personal_data, suppress_personal_data):
            with self.subTest(action=action.__name__), self.assertRaises(PermissionDenied):
                action(self.a.ctx(), self.b.agent)
        self.b.agent.refresh_from_db()
        self.assertTrue(self.b.agent.is_active)

    def test_supervision_is_not_enough(self) -> None:
        supervised = make_tenant("Org S", staff_role=Role.SUPERVISOR)
        with self.assertRaises(PermissionDenied):
            export_personal_data(supervised.ctx(), supervised.agent)
