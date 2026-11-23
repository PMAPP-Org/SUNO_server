"""Importar trabajadores desde Excel (spec 3.1; decisión 8 del plan), con un Excel adversarial
(spec 4 "Importaciones")."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent, LaborImportToken, Worker, WorkerWage
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import registry_import
from suno.labor.common import LaborRejected

from .factories import TODAY, bare_line, bare_timesheet, labor_world, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
HEADER = ["Tipo de documento", "Número de documento", "Nombre", "Cargo", "Fecha de ingreso",
          "Jornal", "Jornal desde"]  # fmt: skip


@skip_without_db
class WorkerImportTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx()

    def test_preview_classifies_and_apply_writes_everything_once(self) -> None:
        book = xlsx("Trabajadores", HEADER, [
            ["CC", "1.023.456.789", "Luis Torres", "Oficial", "2026-02-01", 85000, ""],
            ["CC", 1012345678, "Pedro Pérez Ruiz", "Maestro", "05/01/2026", "95.000", "01/04/2026"],
            ["CC", "52123456", "María Gómez", "Ayudante", "2026-01-05", "100000", ""],
            ["", "", "", "", "", "", ""],
        ])  # fmt: skip
        preview = registry_import.preview(self.ctx, book)
        self.assertEqual(preview.errors, ())
        self.assertEqual(
            [(row.line, row.action) for row in preview.rows],
            [(2, "nuevo"), (3, "actualiza"), (4, "sin cambios")],
        )
        created, updated = registry_import.apply(self.ctx, preview.token)
        self.assertEqual((created, updated), (1, 1))
        luis = Worker.objects.get(document_number="1023456789")
        self.assertEqual(WorkerWage.objects.get(worker=luis).valid_from, date(2026, 2, 1))
        pedro = Worker.objects.get(pk=self.lw.pedro.pk)
        self.assertEqual((pedro.full_name, pedro.position), ("Pedro Pérez Ruiz", "Maestro"))
        self.assertEqual(pedro.hired_on, date(2026, 1, 5))  # el ingreso de uno existente no cambia
        new_wage = WorkerWage.objects.get(worker=pedro, valid_from=date(2026, 4, 1))
        self.assertEqual(new_wage.daily_wage, D("95000.00"))
        self.assertEqual(LaborImportToken.objects.get().kind, "workers")
        event = AuditEvent.objects.get(action="workers.imported")
        self.assertEqual(event.metadata, {"created": 1, "updated": 1})
        # M7: cada cambio de jornal queda en la auditoría, uno a uno (spec 3.6).
        wage_event = AuditEvent.objects.get(action="worker.wage_changed")
        self.assertEqual(wage_event.target, f"worker:{pedro.pk}")
        self.assertEqual(wage_event.metadata, {
            "worker": str(pedro.pk), "document": "CC ***5678", "change": "imported",
            "valid_from": "2026-04-01", "wage": "95000.00"})  # fmt: skip
        with self.assertRaises(LaborRejected) as again:
            registry_import.apply(self.ctx, preview.token)
        self.assertIn("ya se cargó", again.exception.errors[0])

    def test_a_new_wage_without_its_date_starts_today(self) -> None:
        row = ["CC", "1012345678", "Pedro Pérez", "Oficial", "2026-01-05", "90000"]
        book = xlsx("Trabajadores", HEADER[:6], [row])
        registry_import.apply(self.ctx, registry_import.preview(self.ctx, book).token)
        self.assertTrue(WorkerWage.objects.filter(worker=self.lw.pedro, valid_from=TODAY).exists())

    def test_adversarial_file_lists_every_bad_row_and_applies_nothing(self) -> None:
        book = xlsx("Trabajadores", HEADER, [
            ["CC", "=1+1", "Fórmula", "", "2026-02-01", 85000, ""],        # texto con =
            ["CC", "70999888", "Fecha rara", "", "31/02/2026", 85000, ""],
            ["CC", "70111222", "Repetido", "", "2026-02-01", 85000, ""],
            ["CC", "70111222", "Repetido", "", "2026-02-01", 85000, ""],
            ["XX", "80111222", "Tipo malo", "", "2026-02-01", 85000, ""],
            ["CC", "90111222", "Sin jornal", "", "2026-02-01", "", ""],
            ["CC", "91111222", "Jornal texto", "", "2026-02-01", "mucho", ""],
            ["CC", "92111222", "Futuro", "", "2026-12-01", 85000, ""],
        ])  # fmt: skip
        preview = registry_import.preview(self.ctx, book)
        self.assertEqual(preview.token, "")
        text = "\n".join(preview.errors)
        for line in (2, 3, 5, 6, 7, 8, 9):
            with self.subTest(line=line):
                self.assertIn(f"Fila {line}:", text)
        self.assertNotIn("Fila 4:", text)  # la primera de las repetidas está bien
        self.assertIn("se repite (fila 4)", text)
        self.assertEqual(Worker.objects.count(), 2)

    def test_missing_columns_wrong_sheet_and_permissions(self) -> None:
        preview = registry_import.preview(self.ctx, xlsx("Trabajadores", ["Nombre"], [["Ana"]]))
        self.assertIn("Falta la columna «Tipo de documento».", preview.errors)
        with self.assertRaises(PermissionDenied):
            registry_import.preview(self.lw.ctx(self.lw.viewer), xlsx("Trabajadores", HEADER, []))
        token = registry_import.preview(self.ctx, xlsx("Trabajadores", HEADER, [
            ["CC", "70111222", "Ana", "", "2026-02-01", 85000, ""]])).token  # fmt: skip
        with self.assertRaises(LaborRejected):  # la vista previa es de quien la generó
            registry_import.apply(self.lw.ctx(self.lw.world.supervisor), token)
        with self.assertRaises(LaborRejected):
            registry_import.apply(self.ctx, token + "x")

    def test_a_new_wage_cannot_start_on_frozen_days(self) -> None:
        """Decisión 7: la importación tampoco cambia el jornal de días ya aprobados (C2)."""
        sheet = bare_timesheet(self.lw, status="approved")
        bare_line(
            sheet, self.lw.pedro, date(2026, 3, 3), frozen=("80000.00", "1.5200", "121600.00")
        )
        early = xlsx(
            "Trabajadores",
            HEADER,
            [["CC", "1012345678", "Pedro Pérez", "Oficial", "2026-01-05", 95000, "2026-03-01"]],
        )
        preview = registry_import.preview(self.ctx, early)
        self.assertEqual(preview.token, "")
        self.assertIn(
            "Fila 2: Hay planillas aprobadas hasta el 03/03/2026", " ".join(preview.errors)
        )
        later = xlsx(
            "Trabajadores",
            HEADER,
            [["CC", "1012345678", "Pedro Pérez", "Oficial", "2026-01-05", 95000, "2026-03-04"]],
        )
        token = registry_import.preview(self.ctx, later).token
        # Entre la vista previa y aplicar se aprueba otra planilla con días posteriores.
        other = bare_timesheet(
            self.lw, number=2, status="reversed", start=date(2026, 3, 9), end=date(2026, 3, 15)
        )
        bare_line(
            other, self.lw.pedro, date(2026, 3, 10), frozen=("80000.00", "1.5200", "121600.00")
        )
        with self.assertRaises(LaborRejected) as caught:
            registry_import.apply(self.ctx, token)
        self.assertIn("10/03/2026", caught.exception.errors[0])
        self.assertFalse(
            WorkerWage.objects.filter(worker=self.lw.pedro, valid_from=date(2026, 3, 4)).exists()
        )
        # Un jornal igual al vigente no agrega fila: no choca con lo congelado.
        same = xlsx(
            "Trabajadores",
            HEADER,
            [["CC", "1012345678", "Pedro Pérez R", "Oficial", "2026-01-05", 80000, "2026-03-01"]],
        )
        self.assertEqual(registry_import.preview(self.ctx, same).errors, ())
