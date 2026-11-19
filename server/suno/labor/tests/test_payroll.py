"""Nómina importada y conciliación (spec 1.3 y 2.6; decisiones 21 y 22 del plan)."""

from __future__ import annotations

import hashlib
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent, CostEntry, PayrollImport, PayrollImportLine
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import payroll
from suno.labor.common import LaborRejected

from .factories import (
    TODAY,
    WEEK,
    bare_line,
    bare_timesheet,
    labor_world,
    make_worker,
    other_project,
    xlsx,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
HEADER = ["Tipo de documento", "Número de documento", "Nombre", "Total pagado"]
MARCH = (date(2026, 3, 1), date(2026, 3, 31))


@skip_without_db
class PayrollTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx()
        self.luis = make_worker(self.lw.world.org, "70111222", "Luis Torres")  # pagado sin tareo
        # Tareo aprobado: Pedro 1 + 1 en el proyecto y 2 en la Obra 2; María 0,5.
        here = bare_timesheet(self.lw, status="approved")
        bare_line(here, self.lw.pedro, WEEK[0], frozen=("80000.00", "1.5200", "121600.00"))
        bare_line(here, self.lw.pedro, date(2026, 3, 3), frozen=("80000.00", "1.5200", "121600.00"))
        bare_line(here, self.lw.maria, WEEK[0], "0.5", frozen=("100000.00", "1.5200", "76000.00"))
        self.other = other_project(self.lw)
        there = bare_timesheet(self.lw, project=self.other, status="approved")
        for day in (date(2026, 3, 4), date(2026, 3, 5)):
            bare_line(there, self.lw.pedro, day, frozen=("80000.00", "1.5200", "121600.00"))
        # Una anulada y un borrador no cuentan.
        gone = bare_timesheet(self.lw, number=2, status="reversed",
                              start=date(2026, 3, 9), end=date(2026, 3, 15))  # fmt: skip
        bare_line(
            gone, self.lw.maria, date(2026, 3, 9), frozen=("100000.00", "1.5200", "152000.00")
        )
        draft = bare_timesheet(self.lw, number=3, start=date(2026, 3, 16), end=date(2026, 3, 22))
        bare_line(draft, self.lw.maria, date(2026, 3, 16))

    def imported(self) -> PayrollImport:
        book = xlsx("Nómina", HEADER, [
            ["CC", "1.012.345.678", "Pedro", "500.000"],
            ["CC", "70111222", "Luis", 300000],
            ["CE", "123456", "Desconocido", "50000"],
        ])  # fmt: skip
        preview = payroll.preview(self.ctx, book, *MARCH, "nomina-marzo.xlsx")
        self.assertEqual(preview.errors, ())
        return payroll.apply(self.ctx, preview.token)

    def test_import_keeps_lines_and_the_file_fingerprint_not_the_file(self) -> None:
        book = xlsx("Nómina", HEADER, [["CC", "1012345678", "Pedro", "500000"]])
        preview = payroll.preview(self.ctx, book, *MARCH, "nomina.xlsx")
        imported = payroll.apply(self.ctx, preview.token)
        self.assertEqual(imported.file_sha256, hashlib.sha256(book).hexdigest())
        self.assertEqual((imported.file_size, imported.line_count), (len(book), 1))
        self.assertEqual(imported.total, D("500000.00"))
        line = PayrollImportLine.objects.get(payroll=imported)
        self.assertEqual(line.worker, self.lw.pedro)
        self.assertEqual(CostEntry.objects.count(), 0)  # no toca el libro
        event = AuditEvent.objects.get(action="payroll.imported")
        self.assertNotIn("Pedro", str(event.metadata))

    def test_reconciliation_of_the_organization(self) -> None:
        result = payroll.reconcile(self.ctx, self.imported())
        rows = {row.label.split(" · ")[0]: row for row in result.rows}
        pedro = rows["Pedro Pérez"]
        self.assertEqual((pedro.days, pedro.estimated, pedro.paid), (D("4"), D("486400.00"),
                                                                     D("500000.00")))  # fmt: skip
        self.assertEqual(pedro.difference, D("13600.00"))
        self.assertEqual(pedro.pct, D("2.8"))
        self.assertEqual(rows["Luis Torres"].flag, "Pagado sin tareo")
        self.assertEqual(rows["María Gómez"].flag, "Tareado sin pago")
        self.assertEqual(
            rows["María Gómez"].estimated, D("76000.00")
        )  # sin la anulada ni el borrador
        self.assertEqual([u.document for u in result.unknown], ["CE ***3456"])
        self.assertEqual(result.paid, D("800000.00"))  # sin la línea desconocida

    def test_reconciliation_of_a_project_prorates_what_was_paid(self) -> None:
        result = payroll.reconcile(self.ctx, self.imported(), project=self.lw.project)
        rows = {row.label.split(" · ")[0]: row for row in result.rows}
        # Pedro: 2 de sus 4 jornales en este proyecto → la mitad de 500.000.
        self.assertEqual(rows["Pedro Pérez"].paid, D("250000.00"))
        self.assertEqual(rows["Pedro Pérez"].estimated, D("243200.00"))
        self.assertNotIn("Luis Torres", rows)  # pagado sin tareo: no se prorratea
        self.assertEqual(result.excluded, 1)

    def test_bad_files_and_permissions(self) -> None:
        book = xlsx("Nómina", HEADER, [
            ["CC", "1012345678", "Pedro", "0"],
            ["CC", "1012345678", "Pedro", "10"],
            ["XX", "1", "Nadie", "10"],
        ])  # fmt: skip
        preview = payroll.preview(self.ctx, book, *MARCH, "mala.xlsx")
        self.assertEqual(preview.token, "")
        self.assertEqual(len(preview.errors), 3)
        with self.assertRaises(LaborRejected):
            payroll.preview(self.ctx, book, date(2026, 3, 31), date(2026, 3, 1), "x.xlsx")
        with self.assertRaises(PermissionDenied):
            payroll.preview(self.lw.ctx(self.lw.viewer), book, *MARCH, "x.xlsx")
        with self.assertRaises(PermissionDenied):
            payroll.reconcile(self.lw.ctx(self.lw.accountant), self.imported())
