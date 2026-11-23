"""Restricciones de la base de mano de obra (spec 1 y 4 "Restricciones de la base de datos")."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.test import TestCase

from suno.core.models import (
    BenefitFactor,
    CostDocumentType,
    CostEntry,
    CostStatus,
    LaborImportToken,
    PayrollImport,
    PayrollImportLine,
    ProjectWorker,
    Timesheet,
    Worker,
    WorkerWage,
)
from suno.core.models.cost import ORIGIN_FIELDS
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import entry
from suno.materials.tests.factories import bare_cut, material_world

from .factories import WEEK, bare_line, bare_timesheet, labor_world, make_worker

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


@skip_without_db
class RegistryConstraintTests(TestCase):
    def setUp(self) -> None:
        self.lw = labor_world()
        self.org = self.lw.world.org

    def broken(self, build: Any) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            build()

    def test_document_is_unique_per_organization(self) -> None:
        self.broken(lambda: make_worker(self.org, "1012345678", "Otro"))
        other = labor_world("Otra constructora")
        self.assertEqual(other.pedro.document_number, "1012345678")
        # Otro tipo de documento con el mismo número es otra persona.
        make_worker(self.org, "1012345678", "Extranjero", document_type="CE")

    def test_retirement_and_activity_go_together(self) -> None:
        worker = self.lw.pedro
        worker.retired_on = date(2026, 1, 1)  # antes del ingreso
        worker.is_active = False
        self.broken(worker.save)
        worker.retired_on = None  # inactivo sin fecha
        self.broken(worker.save)
        worker.retired_on, worker.is_active = date(2026, 5, 1), True  # activo con fecha
        self.broken(worker.save)
        worker.retired_on, worker.is_active = date(2026, 5, 1), False
        worker.save()

    def test_wages_are_positive_and_unique_per_day(self) -> None:
        pedro = self.lw.pedro
        org = self.org
        self.broken(
            lambda: WorkerWage.objects.create(
                org=org, worker=pedro, valid_from=date(2026, 2, 1), daily_wage=D("0.00")
            )
        )
        self.broken(
            lambda: WorkerWage.objects.create(
                org=org, worker=pedro, valid_from=pedro.hired_on, daily_wage=D("90000.00")
            )
        )

    def test_factor_is_between_one_and_three_and_unique_per_day(self) -> None:
        for value in ("0.9999", "3.0001"):
            with self.subTest(value=value):
                self.broken(lambda value=value: BenefitFactor.objects.create(
                    org=self.org, valid_from=date(2026, 6, 1), factor=D(value)))  # fmt: skip
        self.broken(lambda: BenefitFactor.objects.create(
            org=self.org, valid_from=date(2026, 1, 1), factor=D("1.6")))  # fmt: skip
        BenefitFactor.objects.create(org=self.org, valid_from=date(2026, 6, 1), factor=D("3"))

    def test_assignment_period_order(self) -> None:
        world = self.lw.world
        self.broken(lambda: ProjectWorker.objects.create(
            org=self.org, project=self.lw.project, worker=self.lw.pedro,
            starts_on=date(2026, 3, 1), ends_on=date(2026, 2, 1),
            created_by=world.admin, updated_by=world.admin))  # fmt: skip


@skip_without_db
class TimesheetConstraintTests(TestCase):
    def setUp(self) -> None:
        self.lw = labor_world()

    def broken(self, build: Any) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            build()

    def test_period_is_ordered_and_at_most_31_days(self) -> None:
        self.broken(lambda: bare_timesheet(self.lw, start=date(2026, 3, 8), end=date(2026, 3, 2)))
        self.broken(lambda: bare_timesheet(self.lw, start=date(2026, 3, 1), end=date(2026, 4, 1)))
        bare_timesheet(self.lw, start=date(2026, 3, 1), end=date(2026, 3, 31))  # 31 días

    def test_number_is_unique_per_project(self) -> None:
        bare_timesheet(self.lw)
        self.broken(lambda: bare_timesheet(self.lw, start=date(2026, 3, 9), end=date(2026, 3, 15)))
        other = labor_world("Otra")
        self.assertEqual(bare_timesheet(other).code, "TA-001")

    def test_approval_needs_support_and_someone_else(self) -> None:
        sheet = bare_timesheet(self.lw, status="approved")
        sheet.attachment = None
        self.broken(sheet.save)
        sheet.refresh_from_db()
        sheet.decided_by = sheet.created_by
        self.broken(sheet.save)
        sheet.refresh_from_db()
        sheet.decided_by = self.lw.world.supervisor
        sheet.updated_by = self.lw.world.supervisor  # quien la editó por última vez
        self.broken(sheet.save)

    def test_rejection_and_reversal_need_their_note(self) -> None:
        rejected = bare_timesheet(self.lw, status="rejected")
        rejected.decision_note = ""
        self.broken(rejected.save)
        reversed_sheet = bare_timesheet(self.lw, number=2, status="reversed",
                                        start=date(2026, 3, 9), end=date(2026, 3, 15))  # fmt: skip
        reversed_sheet.reverse_note = ""
        self.broken(reversed_sheet.save)

    def test_line_fraction_frozen_values_and_uniqueness(self) -> None:
        sheet = bare_timesheet(self.lw)
        pedro, day = self.lw.pedro, WEEK[0]
        for fraction in ("0", "1.5001", "-1"):
            with self.subTest(fraction=fraction):
                self.broken(lambda fraction=fraction: bare_line(sheet, pedro, day, fraction))
        self.broken(lambda: bare_line(sheet, pedro, day, frozen=("80000.00", "1.52", "0.00")))
        self.broken(lambda: bare_line(sheet, pedro, day, frozen=("80000.00", "", "")))
        bare_line(sheet, pedro, day, "1.5")
        self.broken(lambda: bare_line(sheet, pedro, day, "0.5"))  # mismo destino y día
        bare_line(sheet, pedro, date(2026, 3, 3), frozen=("80000.00", "1.52", "121600.00"))


@skip_without_db
class LedgerOriginTests(TestCase):
    def test_the_timesheet_is_one_more_single_origin(self) -> None:
        lw = labor_world()
        self.assertIn("timesheet", ORIGIN_FIELDS)
        self.assertEqual(CostDocumentType(CostDocumentType.TIMESHEET).label, "Planilla de tareo")
        sheet = bare_timesheet(lw, status="approved")
        cost: CostEntry = entry(lw.world, status=CostStatus.APPROVED)
        cost.timesheet = sheet
        cost.save()
        self.assertTrue(cost.from_document)
        # Un corte de otra organización basta: el CHECK mira la fila, no la organización.
        cost.consumption_cut = bare_cut(material_world("Materiales"), status="approved")
        with self.assertRaises(IntegrityError), transaction.atomic():
            cost.save()

    def test_payroll_and_tokens(self) -> None:
        lw = labor_world()
        org = lw.world.org
        payroll = PayrollImport.objects.create(
            org=org, starts_on=date(2026, 3, 1), ends_on=date(2026, 3, 31),
            file_name="nomina.xlsx", file_size=10, file_sha256="a" * 64,
            imported_by=lw.world.admin,
        )  # fmt: skip
        PayrollImportLine.objects.create(org=org, payroll=payroll, row=2, document_type="CC",
                                         document_number="1", total_paid=D("10.00"))  # fmt: skip
        with self.assertRaises(IntegrityError), transaction.atomic():
            PayrollImportLine.objects.create(org=org, payroll=payroll, row=3, document_type="CC",
                                             document_number="1", total_paid=D("5.00"))  # fmt: skip
        with self.assertRaises(IntegrityError), transaction.atomic():
            PayrollImportLine.objects.create(org=org, payroll=payroll, row=4, document_type="CC",
                                             document_number="2", total_paid=D("0.00"))  # fmt: skip
        LaborImportToken.objects.create(org=org, nonce="n1", kind="payroll",
                                        applied_by=lw.world.admin)  # fmt: skip
        with self.assertRaises(IntegrityError), transaction.atomic():
            LaborImportToken.objects.create(org=org, nonce="n1", kind="workers",
                                            applied_by=lw.world.admin)  # fmt: skip
        self.assertEqual(Worker.objects.filter(org=org).count(), 2)
        self.assertEqual(Timesheet.objects.count(), 0)


class MaskDocumentTests(unittest.TestCase):
    """M4: un documento corto no se muestra completo."""

    def test_short_documents_hide_more(self) -> None:
        from suno.core.models.labor import mask_document

        self.assertEqual(mask_document("1012345678"), "***5678")
        self.assertEqual(mask_document("52123456"), "***3456")
        self.assertEqual(mask_document("AB12345"), "***45")  # 5 a 7: los últimos 2
        self.assertEqual(mask_document("X1234"), "***34")
        self.assertEqual(mask_document("1234"), "***")  # 3 o 4: todo
        self.assertEqual(mask_document("123"), "***")
        self.assertEqual(mask_document(""), "")
