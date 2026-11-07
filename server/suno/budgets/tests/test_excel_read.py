"""Lectura segura del `.xlsx` de presupuesto: archivo, hojas, límites (puro)."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from suno.budgets import excel
from suno.budgets.excel import (
    MAX_BYTES,
    MAX_ROWS,
    ExcelRejected,
    check_file_name,
    preview_sheet,
    read_rows,
)
from suno.budgets.mapping import MAX_COLUMNS

from .xlsx import (
    HEADER,
    build_xlsx,
    rewrite_entry,
    with_bad_crc,
    with_cached_formula,
    with_entry_bytes,
    with_extra_entry,
)

ROWS: list[list[Any]] = [["1", "Preliminares"], ["1.1", "Localización", "m2", 100, 1500, 150000]]


class FileNameTests(unittest.TestCase):
    def test_only_xlsx(self) -> None:
        check_file_name("Presupuesto Obra.XLSX")
        for name, words in (
            ("p.xlsm", "macros"),
            ("p.xls", "97-2003"),
            ("p.csv", "Solo se admiten"),
        ):
            with self.subTest(name=name), self.assertRaises(ExcelRejected) as caught:
                check_file_name(name)
            self.assertIn(words, str(caught.exception))

    def test_limits_are_the_ones_in_the_spec(self) -> None:
        self.assertEqual(MAX_BYTES, 20 * 1024 * 1024)
        self.assertEqual(MAX_ROWS, 20_000)


class ArchiveTests(unittest.TestCase):
    def test_not_a_zip(self) -> None:
        with self.assertRaises(ExcelRejected) as caught:
            read_rows(b"hola", "Presupuesto")
        self.assertIn("no es un archivo .xlsx válido", str(caught.exception))

    def test_macros_are_rejected_even_with_xlsx_name(self) -> None:
        data = with_extra_entry(build_xlsx(ROWS), "xl/vbaProject.bin", b"\x00")
        with self.assertRaises(ExcelRejected) as caught:
            read_rows(data, "Presupuesto")
        self.assertIn("macros", str(caught.exception))

    def test_size_limit(self) -> None:
        with self.assertRaises(ExcelRejected) as caught:
            read_rows(build_xlsx(ROWS), "Presupuesto", max_bytes=100)
        self.assertIn("supera", str(caught.exception))

    def test_expanded_size_limit(self) -> None:
        with mock.patch.object(excel, "MAX_EXPANDED_BYTES", 100):
            with self.assertRaises(ExcelRejected) as caught:
                read_rows(build_xlsx(ROWS), "Presupuesto")
        self.assertIn("demasiado grande", str(caught.exception))


class PreviewTests(unittest.TestCase):
    def test_first_rows_of_the_chosen_sheet(self) -> None:
        data = build_xlsx(
            [[str(i), f"Ítem {i}", "u", 1, 1] for i in range(40)], extra_sheets=["APU"]
        )
        preview = preview_sheet(data)
        self.assertEqual(preview.sheet_names, ("Presupuesto", "APU"))
        self.assertEqual(preview.sheet, "Presupuesto")
        self.assertEqual(len(preview.rows), 30)
        self.assertEqual(list(preview.rows[0][:6]), HEADER)
        self.assertEqual(preview_sheet(data, "APU").sheet, "APU")
        self.assertEqual(preview_sheet(data, "No existe").sheet, "Presupuesto")


class ReadRowsTests(unittest.TestCase):
    def test_rows_are_padded_to_the_column_limit(self) -> None:
        rows = read_rows(build_xlsx(ROWS), "Presupuesto")
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(rows[0]), MAX_COLUMNS)
        self.assertEqual(rows[2][:6], ("1.1", "Localización", "m2", 100, 1500, 150000))

    def test_missing_sheet(self) -> None:
        with self.assertRaises(ExcelRejected) as caught:
            read_rows(build_xlsx(ROWS), "Hoja1")
        self.assertIn("no tiene la hoja «Hoja1»", str(caught.exception))

    def test_data_beyond_the_row_limit_is_rejected(self) -> None:
        data = build_xlsx([[str(i), "x", "u", 1, 1] for i in range(8)])
        with self.assertRaises(ExcelRejected) as caught:
            read_rows(data, "Presupuesto", max_rows=5)
        self.assertIn("después de la fila 5", str(caught.exception))

    def test_merged_cells_keep_the_value_in_the_first_cell(self) -> None:
        data = build_xlsx(
            [["1", "Preliminares y obras provisionales"]],
            title_rows=[["PRESUPUESTO"]],
            merges=["A1:F1", "B3:D3"],
        )
        rows = read_rows(data, "Presupuesto")
        self.assertEqual(rows[0][0], "PRESUPUESTO")
        self.assertEqual(rows[2][1], "Preliminares y obras provisionales")
        self.assertIsNone(rows[2][2])

    def test_formulas_are_read_as_their_calculated_value(self) -> None:
        data = with_cached_formula(build_xlsx(ROWS), "F3", "D3*E3", "150000")
        self.assertEqual(read_rows(data, "Presupuesto")[2][5], 150000)


SHEET = "xl/worksheets/sheet1.xml"


def corrupt_files() -> dict[str, bytes]:
    data = build_xlsx(ROWS)
    return {
        "truncated": rewrite_entry(data, SHEET, lambda xml: xml[: len(xml) // 2]),
        "garbage": with_entry_bytes(data, SHEET, b"esto no es xml <<<"),
        "bad crc": with_bad_crc(data),
    }


class CorruptSheetTests(unittest.TestCase):
    def test_corrupt_sheets_are_rejected_not_raised(self) -> None:
        for name, data in corrupt_files().items():
            with self.subTest(case=name, call="read_rows"), self.assertRaises(ExcelRejected):
                read_rows(data, "Presupuesto")
            with self.subTest(case=name, call="preview_sheet"), self.assertRaises(ExcelRejected):
                preview_sheet(data)


class MemoryCapsTests(unittest.TestCase):
    def test_a_single_entry_over_the_cap_is_rejected(self) -> None:
        with mock.patch.object(excel, "MAX_ENTRY_BYTES", 100):
            with self.assertRaises(ExcelRejected) as caught:
                read_rows(build_xlsx(ROWS), "Presupuesto")
        self.assertIn("demasiado grande", str(caught.exception))

    def test_reading_stops_after_a_long_gap_past_the_row_limit(self) -> None:
        rows: list[list[Any]] = [["1.1", "A", "u", 1, 1]]
        rows += [[] for _ in range(excel.EMPTY_GAP + 20)]
        rows.append(["9.9", "Lejos", "u", 1, 1])
        data = build_xlsx(rows)
        self.assertEqual(len(read_rows(data, "Presupuesto", max_rows=5)), 5)
        # Datos pegados al límite siguen rechazándose.
        with self.assertRaises(ExcelRejected):
            read_rows(build_xlsx([["x"]] * 8), "Presupuesto", max_rows=5)
