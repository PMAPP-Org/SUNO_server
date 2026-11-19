"""Registro de trabajadores, jornales y factor (spec 1.1 y 3.1; decisiones 3 y 6 a 8)."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent, BenefitFactor, ProjectWorker, Worker, WorkerWage
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import payroll, registry
from suno.labor.common import LaborRejected
from suno.labor.registry import WorkerData

from .factories import TODAY, WEEK, bare_line, bare_timesheet, labor_world, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


def data(**fields: object) -> WorkerData:
    values: dict[str, object] = {
        "document_type": "CC",
        "document_number": "1.023.456.789",
        "full_name": "  Luis Torres ",
        "position": "Oficial",
        "hired_on": date(2026, 2, 1),
    }
    values.update(fields)
    return WorkerData(**values)  # type: ignore[arg-type]


@skip_without_db
class WorkerTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx()

    def test_create_cleans_the_document_and_starts_the_wage_at_hiring(self) -> None:
        worker = registry.create_worker(self.ctx, data(), D("85000.00"))
        self.assertEqual(worker.document_number, "1023456789")
        self.assertEqual(worker.full_name, "Luis Torres")
        wage = WorkerWage.objects.get(worker=worker)
        self.assertEqual((wage.valid_from, wage.daily_wage), (date(2026, 2, 1), D("85000.00")))
        event = AuditEvent.objects.get(action="worker.created")
        self.assertEqual(event.metadata["document"], "CC ***6789")
        self.assertNotIn("Luis", str(event.metadata))  # sin nombres en la auditoría

    def test_invalid_data_and_duplicates_are_rejected(self) -> None:
        for fields, message in (
            ({"document_number": "12"}, "El número de documento"),
            ({"document_number": "ABC-12$"}, "El número de documento"),
            ({"document_type": "XX"}, "Elige el tipo de documento"),
            ({"full_name": " "}, "Escribe el nombre"),
            ({"hired_on": date(2026, 5, 1)}, "no puede ser futura"),
            ({"document_number": "1012345678"}, "Ya hay un trabajador con CC ***5678"),
        ):
            with self.subTest(fields=fields), self.assertRaises(LaborRejected) as rejected:
                registry.create_worker(self.ctx, data(**fields), D("85000.00"))
            self.assertIn(message, rejected.exception.errors[0])
        with self.assertRaises(LaborRejected):
            registry.create_worker(self.ctx, data(), D("0"))

    def test_only_organization_managers_write_the_registry(self) -> None:
        limited = self.lw.ctx(self.lw.viewer)  # Consulta
        with self.assertRaises(PermissionDenied):
            registry.create_worker(limited, data(), D("85000.00"))
        accountant = self.lw.ctx(self.lw.accountant)  # solo Costos
        with self.assertRaises(PermissionDenied):
            registry.add_wage(accountant, self.lw.pedro.pk, date(2026, 4, 1), D("90000.00"))

    def test_retire_closes_assignments_and_respects_tareo(self) -> None:
        sheet = bare_timesheet(self.lw)
        bare_line(sheet, self.lw.pedro, WEEK[1])
        with self.assertRaises(LaborRejected) as early:
            registry.retire_worker(self.ctx, self.lw.pedro.pk, WEEK[0])
        self.assertIn("08/03/2026", early.exception.errors[0])
        worker = registry.retire_worker(self.ctx, self.lw.pedro.pk, date(2026, 3, 31))
        self.assertFalse(worker.is_active)
        assignment = ProjectWorker.objects.get(worker=worker)
        self.assertEqual(assignment.ends_on, date(2026, 3, 31))
        registry.reinstate_worker(self.ctx, worker.pk)
        worker.refresh_from_db()
        self.assertTrue(worker.is_active)
        self.assertIsNone(worker.retired_on)

    def test_delete_only_without_tareo(self) -> None:
        sheet = bare_timesheet(self.lw)
        bare_line(sheet, self.lw.pedro, WEEK[0])
        with self.assertRaises(LaborRejected):
            registry.delete_worker(self.ctx, self.lw.pedro.pk)
        registry.delete_worker(self.ctx, self.lw.maria.pk)  # sin tareo: se borra con su historial
        self.assertFalse(Worker.objects.filter(pk=self.lw.maria.pk).exists())
        self.assertFalse(WorkerWage.objects.filter(worker_id=self.lw.maria.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action="worker.deleted").exists())

    def test_a_worker_in_an_imported_payroll_is_retired_not_deleted(self) -> None:
        """I1: borrarlo daba un `ProtectedError` (500)."""
        book = xlsx("Nómina", ["Tipo de documento", "Número de documento", "Total pagado"],
                    [["CC", "52123456", "500000"]])  # fmt: skip
        preview = payroll.preview(self.ctx, book, date(2026, 3, 1), date(2026, 3, 31), "n.xlsx")
        payroll.apply(self.ctx, preview.token)
        with self.assertRaises(LaborRejected) as caught:
            registry.delete_worker(self.ctx, self.lw.maria.pk)
        self.assertEqual(
            caught.exception.errors,
            ["Aparece en la nómina del 01/03/2026 al 31/03/2026: se puede retirar, no borrar."],
        )
        self.assertTrue(Worker.objects.filter(pk=self.lw.maria.pk).exists())


@skip_without_db
class WageAndFactorTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx()

    def test_a_change_is_a_new_row_and_used_rows_are_frozen(self) -> None:
        pedro = self.lw.pedro
        new = registry.add_wage(self.ctx, pedro.pk, date(2026, 4, 1), D("90000.00"))
        self.assertEqual(WorkerWage.objects.filter(worker=pedro).count(), 2)
        with self.assertRaises(LaborRejected):  # mismo día
            registry.add_wage(self.ctx, pedro.pk, date(2026, 4, 1), D("95000.00"))
        with self.assertRaises(LaborRejected):  # antes del ingreso
            registry.add_wage(self.ctx, pedro.pk, date(2025, 12, 1), D("95000.00"))
        registry.change_wage(self.ctx, new.pk, D("91000.00"))  # nadie la usó
        first = WorkerWage.objects.get(worker=pedro, valid_from=pedro.hired_on)
        sheet = bare_timesheet(self.lw, status="approved")
        bare_line(sheet, pedro, WEEK[0], frozen=("80000.00", "1.5200", "121600.00"))
        with self.assertRaises(LaborRejected) as retroactive:  # cambiaría lo congelado
            registry.add_wage(self.ctx, pedro.pk, date(2026, 3, 1), D("95000.00"))
        self.assertIn("02/03/2026", retroactive.exception.errors[0])
        with self.assertRaises(LaborRejected) as used:
            registry.change_wage(self.ctx, first.pk, D("81000.00"))
        self.assertIn("planilla aprobada", used.exception.errors[0])
        with self.assertRaises(LaborRejected):
            registry.delete_wage(self.ctx, first.pk)
        registry.delete_wage(self.ctx, new.pk)  # la del 1 de abril no la usa nadie
        event = AuditEvent.objects.filter(action="worker.wage_changed").latest("created_at")
        self.assertEqual(event.metadata["worker"], str(pedro.pk))

    def test_a_draft_does_not_freeze_a_wage(self) -> None:
        first = WorkerWage.objects.get(worker=self.lw.pedro)
        bare_line(bare_timesheet(self.lw), self.lw.pedro, WEEK[0])
        registry.change_wage(self.ctx, first.pk, D("81000.00"))
        first.refresh_from_db()
        self.assertEqual(first.daily_wage, D("81000.00"))

    def test_factor_history(self) -> None:
        factor = registry.add_factor(self.ctx, date(2026, 4, 1), D("1.55"), "Dotación nueva")
        self.assertEqual(factor.factor, D("1.5500"))
        for value in ("0.99", "3.01", "1.55555"):
            with self.subTest(value=value), self.assertRaises(LaborRejected):
                registry.add_factor(self.ctx, date(2026, 5, 1), D(value), "")
        first = BenefitFactor.objects.get(org=self.lw.world.org, valid_from=date(2026, 1, 1))
        sheet = bare_timesheet(self.lw, status="reversed")
        bare_line(sheet, self.lw.maria, WEEK[0], frozen=("100000.00", "1.5200", "152000.00"))
        with self.assertRaises(LaborRejected):  # una anulada también la usó
            registry.change_factor(self.ctx, first.pk, D("1.6"), "")
        registry.change_factor(self.ctx, factor.pk, D("1.56"), "Corrección")
        registry.delete_factor(self.ctx, factor.pk)
        self.assertTrue(AuditEvent.objects.filter(action="benefit_factor.changed").exists())
