"""Excel de mano de obra (M10 de la revisión): la misma convención que los exportes de costos y
materiales: montos y cantidades como número (`float`), vacío si no hay valor, fechas en ISO,
encabezados en minúsculas sin tildes y texto de usuario por `safe_cell`."""

from __future__ import annotations

import io
import unittest
from datetime import date
from unittest import mock

from django.test import TestCase
from openpyxl import load_workbook

from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import load_catalog
from suno.labor import export, payroll

from .factories import TODAY, WEEK, bare_line, bare_timesheet, labor_world, make_worker, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


def _rows(content: bytes) -> list[tuple[object, ...]]:
    return list(load_workbook(io.BytesIO(content)).active.iter_rows(values_only=True))


@skip_without_db
class ExportTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx()

    def test_amounts_are_numbers_like_the_other_exports(self) -> None:
        sheet = bare_timesheet(self.lw, status="approved")
        bare_line(sheet, self.lw.pedro, WEEK[0], frozen=("80000.00", "1.5200", "121600.00"))
        rows = _rows(
            export.timesheet_workbook(self.ctx, sheet, load_catalog(self.ctx, self.lw.project))
        )
        self.assertEqual(rows[0], tuple(export.TIMESHEET_HEADER))
        self.assertEqual(rows[1][2:], ("2026-03-02", "Indirecto (proyecto)", 1.0, 80000.0, 1.52,
                                       121600.0))  # fmt: skip
        make_worker(self.lw.world.org, "70111222", "Luis Torres")
        book = xlsx("Nómina", ["Tipo de documento", "Número de documento", "Total pagado"],
                    [["CC", "1012345678", "130000"], ["CC", "70111222", "1000"],
                     ["CE", "99999999", "50"]])  # fmt: skip
        imported = payroll.apply(
            self.ctx,
            payroll.preview(self.ctx, book, date(2026, 3, 1), date(2026, 3, 31), "n.xlsx").token,
        )
        rows = _rows(export.reconciliation_workbook(payroll.reconcile(self.ctx, imported)))
        by_name = {str(row[0]).split(" · ")[0]: row for row in rows[1:]}
        self.assertEqual(by_name["Pedro Pérez"][2:7], (1.0, 121600.0, 130000.0, 8400.0, 6.9))
        self.assertEqual(by_name["Luis Torres"][2:8], (0.0, 0.0, 1000.0, 1000.0, "",
                                                       "Pagado sin tareo"))  # fmt: skip
        unknown = by_name["Fuera del registro: CE ***9999 (fila 4)"]
        self.assertEqual(unknown[4], 50.0)
