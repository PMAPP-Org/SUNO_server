"""Planillas de tareo (spec 1.2, 2.2, 2.3 y 3.2): periodo, líneas, asignación, tope diario,
resumen guardado y edición."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent, Timesheet, TimesheetLine, WorkerWage
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload
from suno.labor import timesheets
from suno.labor.common import LaborRejected

from .factories import (
    TODAY,
    WEEK,
    bare_line,
    bare_timesheet,
    labor_world,
    line_data,
    make_worker,
    other_project,
    to_item,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MON, TUE = WEEK[0], date(2026, 3, 3)


class PeriodTests(unittest.TestCase):
    def test_default_period_is_last_week(self) -> None:
        self.assertEqual(
            timesheets.default_period(date(2026, 4, 15)), (date(2026, 4, 6), date(2026, 4, 12))
        )
        self.assertEqual(
            timesheets.default_period(date(2026, 4, 13)), (date(2026, 4, 6), date(2026, 4, 12))
        )


@skip_without_db
class CreateTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx(self.lw.contributor)

    def test_number_period_rules_and_overlaps(self) -> None:
        first = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        self.assertEqual((first.code, first.status), ("TA-001", "draft"))
        for start, end, message in (
            (date(2026, 3, 8), date(2026, 3, 2), "termina antes"),
            (date(2026, 3, 9), date(2026, 4, 9), "31 días"),
            (date(2026, 4, 13), date(2026, 4, 19), "futuro"),
            (date(2026, 3, 5), date(2026, 3, 11), "Se cruza con la planilla TA-001"),
        ):
            with self.subTest(start=start), self.assertRaises(LaborRejected) as rejected:
                timesheets.create_timesheet(self.ctx, self.lw.project, start, end)
            self.assertIn(message, rejected.exception.errors[0])
        # Una anulada no bloquea: para corregir se hace otra con el mismo periodo.
        Timesheet.objects.filter(pk=first.pk).delete()
        bare_timesheet(self.lw, status="reversed")
        again = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        self.assertEqual(again.code, "TA-002")
        self.assertTrue(AuditEvent.objects.filter(action="timesheet.created").exists())

    def test_viewer_cannot_create(self) -> None:
        with self.assertRaises(PermissionDenied):
            timesheets.create_timesheet(self.lw.ctx(self.lw.viewer), self.lw.project, *WEEK)


@skip_without_db
class SaveLinesTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx(self.lw.contributor)
        self.sheet = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)

    def save(self, lines: list[Any], days: list[date] | None = None, ctx: Any = None) -> Timesheet:
        return timesheets.save_lines(
            ctx or self.ctx, self.lw.project, self.sheet.pk, lines, days or [MON, TUE]
        )

    def test_save_writes_lines_and_the_summary(self) -> None:
        sheet = self.save([
            line_data(self.lw.pedro, MON),
            line_data(self.lw.maria, MON, "0.5", to_item(self.lw)),
            line_data(self.lw.maria, MON, "0.25", to_item(self.lw)),  # misma línea: se suma
        ])  # fmt: skip
        self.assertEqual(TimesheetLine.objects.filter(timesheet=sheet).count(), 2)
        maria = TimesheetLine.objects.get(timesheet=sheet, worker=self.lw.maria)
        self.assertEqual((maria.fraction, maria.scope), (D("0.7500"), "item"))
        self.assertIsNone(maria.amount)  # en borrador no se congela
        self.assertEqual(sheet.total, D("235600.00"))  # 121.600 + 0,75 × 100.000 × 1,52
        self.assertEqual(sheet.days, D("1.7500"))
        self.assertEqual(sheet.line_count, 2)
        self.assertEqual(sheet.summary["fingerprint"]["total"], "235600.00")
        self.assertEqual(len(sheet.summary["groups"]), 2)
        event = AuditEvent.objects.filter(action="timesheet.updated").latest("created_at")
        self.assertEqual(event.metadata["lines"], 2)

    def test_saving_some_days_keeps_the_others(self) -> None:
        self.save([line_data(self.lw.pedro, MON), line_data(self.lw.pedro, TUE)])
        self.save([line_data(self.lw.pedro, TUE, "0.5")], days=[TUE])
        days = dict(TimesheetLine.objects.values_list("worked_on", "fraction"))
        self.assertEqual(days, {MON: D("1.0000"), TUE: D("0.5000")})
        self.save([], days=[TUE])  # vaciar un día
        self.assertEqual(TimesheetLine.objects.count(), 1)

    def test_line_rules(self) -> None:
        luis = make_worker(self.lw.world.org, "70111222", "Luis Torres")  # sin asignación
        for lines, days, message in (
            ([line_data(luis, MON)], None, "no está asignado al proyecto"),
            ([line_data(self.lw.pedro, MON, "1.6")], None, "la fracción"),
            ([line_data(self.lw.pedro, MON, "1"), line_data(self.lw.pedro, MON, "0.75")], None,
             "la misma línea suma 1,75"),
            ([line_data(self.lw.pedro, date(2026, 3, 9))], [date(2026, 3, 9)], "fuera del periodo"),
            ([line_data(self.lw.pedro, TUE)], [MON], "el día no está"),
        ):  # fmt: skip
            with self.subTest(message=message), self.assertRaises(LaborRejected) as rejected:
                self.save(lines, days)
            self.assertIn(message, "\n".join(rejected.exception.errors))

    def test_daily_cap_counts_other_projects_and_names_them(self) -> None:
        other = other_project(self.lw)
        elsewhere = bare_timesheet(self.lw, project=other)
        bare_line(elsewhere, self.lw.pedro, MON, "1")
        with self.assertRaises(LaborRejected) as rejected:
            self.save([line_data(self.lw.pedro, MON, "0.75")])
        text = rejected.exception.errors[0]
        self.assertIn("Pedro Pérez · CC ***5678 suma 1,75 jornales el 02/03/2026", text)
        self.assertIn("TA-001 (Obra 2)", text)
        self.save([line_data(self.lw.pedro, MON, "0.5")])  # 1,5 exacto pasa
        # Una planilla anulada no cuenta.
        reversed_sheet = bare_timesheet(self.lw, number=2, project=other, status="reversed",
                                        start=date(2026, 3, 9), end=date(2026, 3, 15))  # fmt: skip
        bare_line(reversed_sheet, self.lw.pedro, date(2026, 3, 9), "1.5")
        self.assertEqual(
            timesheets.day_totals(self.lw.ctx(), {self.lw.pedro.pk}, {date(2026, 3, 9)}), {}
        )

    def test_editing_a_rejected_sheet_returns_it_to_draft_and_drops_the_signature(self) -> None:
        self.save([line_data(self.lw.pedro, MON)])
        timesheets.attach_signed(self.ctx, self.lw.project, self.sheet.pk, pdf_upload(), "f.pdf")
        Timesheet.objects.filter(pk=self.sheet.pk).update(
            status="rejected",
            decided_by=self.lw.world.supervisor,
            decided_at=self.sheet.created_at,
            decision_note="Falta María",
        )
        with self.assertRaises(PermissionDenied):  # un rechazado lo edita solo su autor
            self.save([line_data(self.lw.maria, MON)], ctx=self.lw.ctx(self.lw.world.supervisor))
        sheet = self.save([line_data(self.lw.pedro, MON), line_data(self.lw.maria, MON)])
        self.assertEqual(sheet.status, "draft")
        self.assertIsNone(sheet.attachment_id)
        self.assertIsNone(sheet.decided_by_id)
        event = AuditEvent.objects.filter(action="timesheet.updated").latest("created_at")
        self.assertTrue(event.metadata["resubmitted"])
        self.assertTrue(event.metadata["signed_removed"])

    def test_signature_is_not_an_edit_and_recalculate_is(self) -> None:
        self.save([line_data(self.lw.pedro, MON)])
        sheet = timesheets.attach_signed(
            self.lw.ctx(), self.lw.project, self.sheet.pk, pdf_upload(), "firmada.pdf"
        )
        self.assertEqual(sheet.updated_by, self.lw.contributor)  # subirla no es editar
        WorkerWage.objects.create(org=self.lw.world.org, worker=self.lw.pedro,
                                  valid_from=MON, daily_wage=D("90000.00"))  # fmt: skip
        sheet = timesheets.recalculate(self.lw.ctx(), self.lw.project, self.sheet.pk)
        self.assertEqual(sheet.total, D("136800.00"))
        self.assertIsNone(sheet.attachment_id)
        self.assertEqual(sheet.updated_by, self.lw.world.admin)

    def test_delete_only_open_sheets_by_their_author_or_a_reviewer(self) -> None:
        with self.assertRaises(PermissionDenied):
            timesheets.delete_timesheet(self.lw.ctx(self.lw.viewer), self.lw.project, self.sheet.pk)
        timesheets.delete_timesheet(self.ctx, self.lw.project, self.sheet.pk)
        self.assertFalse(Timesheet.objects.filter(pk=self.sheet.pk).exists())
        approved = bare_timesheet(self.lw, status="approved")
        with self.assertRaises(LaborRejected):
            timesheets.delete_timesheet(self.lw.ctx(), self.lw.project, approved.pk)
