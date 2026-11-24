"""Aprobación, rechazo y anulación (spec 2.3 y 2.4): lo aprobado es lo firmado, sin
autoaprobación, valores congelados y costos por destino en el libro."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from suno.core.models import (
    AuditEvent,
    BenefitFactor,
    BudgetItemVersion,
    CostEntry,
    Timesheet,
    TimesheetLine,
    WorkerWage,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import item, pdf_upload
from suno.labor import approval, timesheets
from suno.labor.common import SELF_DECISION, LaborRejected

from .factories import (
    TODAY,
    WEEK,
    bare_line,
    bare_timesheet,
    labor_world,
    line_data,
    other_project,
    to_item,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MON = WEEK[0]


@skip_without_db
class ApprovalTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.author = self.lw.ctx(self.lw.contributor)
        self.approver = self.lw.ctx(self.lw.world.supervisor)
        self.sheet = timesheets.create_timesheet(self.author, self.lw.project, *WEEK)
        timesheets.save_lines(self.author, self.lw.project, self.sheet.pk, [
            line_data(self.lw.pedro, MON),
            line_data(self.lw.maria, MON, "0.5", to_item(self.lw)),
        ], [MON])  # fmt: skip

    def sign(self) -> None:
        timesheets.attach_signed(self.author, self.lw.project, self.sheet.pk, pdf_upload(), "f.pdf")

    def test_no_row_is_locked_for_someone_without_permission(self) -> None:
        """M5: la política se revisa antes de los `FOR UPDATE`."""
        self.sign()
        viewer = self.lw.ctx(self.lw.viewer)
        actions = {
            "approve": lambda: approval.approve_timesheet(viewer, self.lw.project, self.sheet.pk),
            "reject": lambda: approval.reject_timesheet(viewer, self.lw.project, self.sheet.pk,
                                                        "No"),
            "reverse": lambda: approval.reverse_timesheet(self.approver, self.lw.project,
                                                          self.sheet.pk, "No"),
        }  # fmt: skip
        for name, action in actions.items():
            with self.subTest(name), CaptureQueriesContext(connection) as queries:
                with self.assertRaises(PermissionDenied):
                    action()
            locks = [q["sql"] for q in queries.captured_queries if "FOR " in q["sql"]]
            self.assertEqual(locks, [], name)

    def test_approval_freezes_lines_and_posts_one_cost_per_destination(self) -> None:
        self.sign()
        sheet = approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertEqual(sheet.status, "approved")
        pedro = TimesheetLine.objects.get(timesheet=sheet, worker=self.lw.pedro)
        self.assertEqual(
            (pedro.daily_wage, pedro.factor, pedro.amount),
            (D("80000.00"), D("1.5200"), D("121600.00")),
        )
        entries = CostEntry.objects.filter(timesheet=sheet).order_by("amount")
        self.assertEqual([e.amount for e in entries], [D("76000.00"), D("121600.00")])
        indirect = entries[1]
        self.assertEqual(indirect.description, "Tareo TA-001 · Indirecto (proyecto)")
        self.assertEqual(
            (indirect.kind, indirect.document_type, indirect.document_number),
            ("labor", "timesheet", "TA-001"),
        )
        self.assertEqual(indirect.incurred_on, WEEK[1])
        self.assertEqual(indirect.third_party, "")  # sin nombres en el libro
        self.assertEqual(indirect.created_by, self.lw.contributor)
        self.assertEqual(indirect.decided_by, self.lw.world.supervisor)
        self.assertEqual(indirect.attachment_id, sheet.attachment_id)
        self.assertEqual(entries[0].scope, "item")
        self.assertEqual(sum(e.amount for e in entries), sheet.total)  # cuadra al peso
        event = AuditEvent.objects.get(action="timesheet.approved")
        self.assertEqual(event.metadata["entries"], 2)

    def test_a_rename_after_printing_keeps_the_printed_destination(self) -> None:
        """#88: la descripción del libro es la de la planilla impresa y firmada."""
        self.sign()
        printed = {group["label"] for group in Timesheet.objects.get(pk=self.sheet.pk)
                   .summary["groups"]}  # fmt: skip
        BudgetItemVersion.objects.filter(item=item(self.lw.cw.budget, "2.1")).update(
            description="Concreto ciclópeo"
        )
        sheet = approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        descriptions = set(
            CostEntry.objects.filter(timesheet=sheet).values_list("description", flat=True)
        )
        self.assertEqual(descriptions, {f"Tareo TA-001 · {label}" for label in printed})
        self.assertIn("Tareo TA-001 · 2.1 Concreto zapatas", descriptions)

    def test_no_self_approval_and_signature_required(self) -> None:
        with self.assertRaises(LaborRejected) as unsigned:
            approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertIn("firmada", unsigned.exception.errors[0])
        self.sign()
        with self.assertRaises(PermissionDenied):  # el Colaborador no aprueba
            approval.approve_timesheet(self.author, self.lw.project, self.sheet.pk)
        timesheets.recalculate(self.approver, self.lw.project, self.sheet.pk)  # el revisor edita
        timesheets.attach_signed(self.approver, self.lw.project, self.sheet.pk, pdf_upload(), "f")
        with self.assertRaises(LaborRejected) as own:
            approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertEqual(own.exception.errors, [SELF_DECISION])
        approval.approve_timesheet(self.lw.ctx(), self.lw.project, self.sheet.pk)

    def test_a_wage_change_after_printing_blocks_approval(self) -> None:
        self.sign()
        WorkerWage.objects.create(org=self.lw.world.org, worker=self.lw.pedro, valid_from=MON,
                                  daily_wage=D("90000.00"))  # fmt: skip
        with self.assertRaises(LaborRejected) as changed:
            approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertEqual(
            changed.exception.errors,
            ["La planilla cambió desde que se imprimió; revísala y vuelve a cargar el soporte "
             "firmado."],
        )  # fmt: skip
        timesheets.recalculate(self.author, self.lw.project, self.sheet.pk)
        self.sign()
        sheet = approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertEqual(sheet.total, D("212800.00"))  # 136.800 + 76.000

    def test_missing_factor_blocks_approval(self) -> None:
        BenefitFactor.objects.filter(org=self.lw.world.org).delete()
        timesheets.recalculate(self.author, self.lw.project, self.sheet.pk)
        self.sign()
        with self.assertRaises(LaborRejected) as missing:
            approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertIn("No hay factor prestacional vigente el 02/03/2026.", missing.exception.errors)

    def test_cap_is_checked_again_at_approval(self) -> None:
        """Un borrador escrito por fuera del servicio (como si llegara de otra vía) que rompe el
        tope impide aprobar."""
        elsewhere = bare_timesheet(self.lw, project=other_project(self.lw))
        bare_line(elsewhere, self.lw.pedro, MON, "1")
        self.sign()
        with self.assertRaises(LaborRejected) as over:
            approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertIn("suma 2 jornales el 02/03/2026", over.exception.errors[0])

    def test_reject_needs_a_note_and_editing_returns_to_draft(self) -> None:
        with self.assertRaises(LaborRejected):
            approval.reject_timesheet(self.approver, self.lw.project, self.sheet.pk, "  ")
        sheet = approval.reject_timesheet(self.approver, self.lw.project, self.sheet.pk, "Falta X")
        self.assertEqual((sheet.status, sheet.decision_note), ("rejected", "Falta X"))
        sheet = timesheets.recalculate(self.author, self.lw.project, self.sheet.pk)
        self.assertEqual(sheet.status, "draft")

    def test_reverse_writes_todays_reversals_and_keeps_frozen_values(self) -> None:
        self.sign()
        approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        with self.assertRaises(PermissionDenied):  # el Revisor no anula
            approval.reverse_timesheet(self.approver, self.lw.project, self.sheet.pk, "Error")
        sheet = approval.reverse_timesheet(self.lw.ctx(), self.lw.project, self.sheet.pk, "Error")
        self.assertEqual(sheet.status, "reversed")
        reversals = CostEntry.objects.filter(timesheet=sheet, reverses__isnull=False)
        self.assertEqual(reversals.count(), 2)
        self.assertTrue(all(r.incurred_on == TODAY and r.amount < 0 for r in reversals))
        self.assertEqual(
            TimesheetLine.objects.get(timesheet=sheet, worker=self.lw.pedro).amount,
            D("121600.00"),
        )
        with self.assertRaises(LaborRejected):
            approval.reverse_timesheet(self.lw.ctx(), self.lw.project, self.sheet.pk, "Otra vez")
        # Para corregir: otra planilla con el mismo periodo.
        again = timesheets.create_timesheet(self.author, self.lw.project, *WEEK)
        self.assertEqual(again.code, "TA-002")
        self.assertEqual(Timesheet.objects.filter(project=self.lw.project).count(), 2)
