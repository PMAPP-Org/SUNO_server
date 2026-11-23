"""Importar tareo desde Excel a una planilla en borrador (spec 2.5): dos pasos con nonce,
errores por fila, nada se aplica si hay errores y una línea igual se suma."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent, LaborImportToken, TimesheetLine
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import timesheet_import, timesheets
from suno.labor.common import LaborRejected

from .factories import TODAY, WEEK, labor_world, line_data, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
HEADER = ["Tipo de documento", "Número de documento", "Fecha", "Destino", "Tarea", "Fracción"]


@skip_without_db
class TimesheetImportTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx(self.lw.contributor)
        self.sheet = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        timesheets.save_lines(self.ctx, self.lw.project, self.sheet.pk,
                              [line_data(self.lw.pedro, WEEK[0], "0.5")], [WEEK[0]])  # fmt: skip

    def preview(self, rows: list[list[object]]) -> timesheet_import.TareoPreview:
        self.sheet.refresh_from_db()
        return timesheet_import.preview(
            self.ctx, self.lw.project, self.sheet, xlsx("Tareo", HEADER, rows)
        )

    def test_rows_are_added_and_equal_lines_are_summed(self) -> None:
        preview = self.preview([
            ["CC", "1012345678", "2026-03-02", "Indirecto", "", "0,5"],
            ["CC", "52123456", "03/03/2026", "2.1", "2.1", 1],
            ["cc", 52123456, "2026-03-04", "Capítulo 1", "", "0.25"],
        ])  # fmt: skip
        self.assertEqual(preview.errors, ())
        self.assertEqual(len(preview.rows), 3)
        added = timesheet_import.apply(self.ctx, self.lw.project, self.sheet.pk, preview.token)
        self.assertEqual(added, 3)
        pedro = TimesheetLine.objects.get(timesheet=self.sheet, worker=self.lw.pedro)
        self.assertEqual(pedro.fraction, D("1.0000"))  # 0,5 de antes + 0,5 importado
        self.assertEqual(TimesheetLine.objects.filter(timesheet=self.sheet).count(), 3)
        self.assertEqual(LaborImportToken.objects.get().kind, "timesheet")
        self.assertTrue(AuditEvent.objects.filter(action="timesheet.imported").exists())
        with self.assertRaises(LaborRejected):
            timesheet_import.apply(self.ctx, self.lw.project, self.sheet.pk, preview.token)

    def test_adversarial_rows_apply_nothing(self) -> None:
        preview = self.preview([
            ["CC", "999", "2026-03-02", "Indirecto", "", "1"],        # no está en el registro
            ["CC", "1012345678", "2026-03-09", "Indirecto", "", "1"],  # fuera del periodo
            ["CC", "1012345678", "30/02/2026", "Indirecto", "", "1"],  # fecha imposible
            ["CC", "1012345678", "2026-03-03", "9.9", "", "1"],        # destino que no existe
            ["CC", "1012345678", "2026-03-03", "2.1", "7.7", "1"],     # tarea que no existe
            ["CC", "1012345678", "2026-03-03", "Indirecto", "", "=A1"],  # fórmula como texto
            ["CC", "1012345678", "2026-03-03", "Indirecto", "", "1.500"],  # 1500: pasa del tope
            ["", "", "", "", "", ""],
        ])  # fmt: skip
        self.assertEqual(preview.token, "")
        text = "\n".join(preview.errors)
        for line in range(2, 9):
            with self.subTest(line=line):
                self.assertIn(f"Fila {line}:", text)
        self.assertEqual(TimesheetLine.objects.filter(timesheet=self.sheet).count(), 1)

    def test_cap_is_validated_when_applying(self) -> None:
        preview = self.preview([["CC", "1012345678", "2026-03-02", "2.1", "", "1.25"]])
        with self.assertRaises(LaborRejected) as over:
            timesheet_import.apply(self.ctx, self.lw.project, self.sheet.pk, preview.token)
        self.assertIn("suma 1,75 jornales", over.exception.errors[0])
        self.assertFalse(LaborImportToken.objects.exists())  # todo o nada, también el nonce

    def test_token_is_for_this_sheet_and_viewer_cannot_import(self) -> None:
        preview = self.preview([["CC", "1012345678", "2026-03-03", "Indirecto", "", "1"]])
        other = timesheets.create_timesheet(self.ctx, self.lw.project, date(2026, 3, 9),
                                            date(2026, 3, 15))  # fmt: skip
        with self.assertRaises(LaborRejected):
            timesheet_import.apply(self.ctx, self.lw.project, other.pk, preview.token)
        with self.assertRaises(PermissionDenied):
            timesheet_import.preview(self.lw.ctx(self.lw.viewer), self.lw.project, self.sheet,
                                     xlsx("Tareo", HEADER, []))  # fmt: skip
