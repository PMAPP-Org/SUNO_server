"""Interpretación de las filas del Excel de presupuesto (puro)."""

from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any

from visitacampo.budgets.excel import (
    KIND_CHAPTER,
    KIND_ITEM,
    ExcelRejected,
    ParsedBudget,
    interpret_rows,
    parse_budget,
)
from visitacampo.budgets.mapping import ColumnMapping

from .samples import MAPPING, V1_ROWS, sheet_rows
from .xlsx import build_xlsx, with_cached_formula

D = Decimal


def interpret(rows: list[list[Any]]) -> ParsedBudget:
    return interpret_rows(sheet_rows(rows), MAPPING)


class KindsAndHierarchyTests(unittest.TestCase):
    def test_chapters_by_code_levels_items_and_totals(self) -> None:
        parsed = interpret(
            [
                ["1", "Preliminares"],
                ["1.1", "Localización", "m2", 100, 1500, 150000],
                ["1.2", "Cerramientos"],
                ["1.2.1", "Cerramiento en malla", "ml", 50, "20.000,50", None],
                ["2", "Estructura"],
                ["2.1", "Concreto", "m3", 2.5, 0.33, None],
            ]
        )
        by_code = {row.code: row for row in parsed.rows}
        self.assertEqual(
            [(r.code, r.kind, r.parent_code, r.sort_order) for r in parsed.rows],
            [
                ("1", KIND_CHAPTER, None, 0),
                ("1.1", KIND_ITEM, "1", 1),
                ("1.2", KIND_CHAPTER, "1", 2),
                ("1.2.1", KIND_ITEM, "1.2", 3),
                ("2", KIND_CHAPTER, None, 4),
                ("2.1", KIND_ITEM, "2", 5),
            ],
        )
        self.assertEqual(by_code["1.2.1"].unit_price, D("20000.50"))
        self.assertEqual(by_code["1.2.1"].total, D("1000025.00"))
        self.assertEqual(by_code["1.2"].total, D("1000025.00"))
        self.assertEqual(by_code["1"].total, D("1150025.00"))
        self.assertEqual(by_code["2.1"].total, D("0.83"))  # 2,5 × 0,33 = 0,825
        self.assertEqual(by_code["2.1"].quantity, D("2.5000"))
        self.assertIsNone(by_code["1"].quantity)
        self.assertEqual(parsed.direct_cost, D("1150025.83"))
        self.assertEqual(by_code["1.1"].row, 3)

    def test_non_hierarchical_codes_hang_from_the_last_chapter_above(self) -> None:
        parsed = interpret(
            [
                ["A", "Obras preliminares"],
                ["101", "Localización", "m2", 10, 100],
                ["102", "Descapote", "m2", 10, 100],
                ["B", "Estructura"],
                ["201", "Concreto", "m3", 1, 100],
            ]
        )
        self.assertEqual(
            {r.code: r.parent_code for r in parsed.rows},
            {"A": None, "101": "A", "102": "A", "B": None, "201": "B"},
        )

    def test_dotted_item_without_its_chapter_uses_the_last_chapter_above(self) -> None:
        parsed = interpret([["CAP-1", "Preliminares"], ["1.1", "Localización", "u", 1, 1]])
        self.assertEqual(parsed.rows[1].parent_code, "CAP-1")

    def test_codes_written_as_numbers_and_trailing_dots(self) -> None:
        parsed = interpret([[1, "Preliminares"], [1.1, "Localización", "u", 1, 1], ["2.", "B"]])
        self.assertEqual([r.code for r in parsed.rows], ["1", "1.1", "2"])
        self.assertEqual(parsed.rows[1].parent_code, "1")


class IgnoredRowsTests(unittest.TestCase):
    def test_rows_without_code_and_subtotals_are_ignored_and_counted(self) -> None:
        parsed = interpret(
            [
                ["1", "Preliminares"],
                ["1.1", "Localización", "m2", 100, 1500, 150000],
                [None, "Subtotal preliminares", None, None, None, 150000],
                [],
                ["", "Nota: precios con AIU incluido"],
                ["3", "SUBTOTAL CAPÍTULO 3", None, None, None, 5],
                ["TOTAL", None, None, None, None, 150000],
                ["9"],
            ]
        )
        self.assertEqual([r.code for r in parsed.rows], ["1", "1.1"])
        self.assertEqual(parsed.ignored_rows, 5)  # la fila vacía no cuenta
        self.assertEqual(parsed.ignored_examples, (4, 6, 7, 8, 9))
        self.assertIn("5 fila(s) ignorada(s)", parsed.warnings[0])


class RejectedFilesTests(unittest.TestCase):
    def rejected(self, rows: list[list[Any]]) -> str:
        with self.assertRaises(ExcelRejected) as caught:
            interpret(rows)
        return str(caught.exception)

    def test_repeated_code_is_named(self) -> None:
        message = self.rejected(
            [["1.1", "A", "u", 1, 1], ["1.2", "B", "u", 1, 1], ["1.1", "C", "u", 1, 1]]
        )
        self.assertIn("El código 1.1 está repetido (filas 2 y 4)", message)

    def test_non_numeric_quantity_names_the_row(self) -> None:
        message = self.rejected([["1.1", "A", "u", "diez", 1]])
        self.assertIn("Fila 2 (código 1.1): la cantidad «diez» no es un número", message)

    def test_negative_price_names_the_row(self) -> None:
        message = self.rejected([["1.1", "A", "u", 1, -5]])
        self.assertIn("Fila 2 (código 1.1): el valor unitario tiene un valor negativo", message)

    def test_quantity_without_price(self) -> None:
        message = self.rejected([["1.1", "A", "u", 3, None]])
        self.assertIn("le falta el valor unitario", message)

    def test_code_too_long(self) -> None:
        self.assertIn("más de 50 caracteres", self.rejected([["X" * 51, "A", "u", 1, 1]]))

    def test_value_too_large(self) -> None:
        self.assertIn("demasiado grande", self.rejected([["1.1", "A", "u", 1, 10**17]]))

    def test_no_items(self) -> None:
        self.assertIn("No se encontró ningún ítem", self.rejected([["1", "Preliminares"]]))


class WarningsTests(unittest.TestCase):
    def test_excel_total_that_does_not_match_is_warned(self) -> None:
        parsed = interpret([["1.1", "A", "u", 10, 15, 151], ["1.2", "B", "u", 2, 3, 6]])
        self.assertEqual(parsed.total_mismatches, 1)
        self.assertIn("1.1 (fila 2: 151.00 en el Excel, 150.00 calculado)", parsed.warnings[0])
        self.assertEqual(parsed.rows[0].total, D("150.00"))  # manda el calculado

    def test_without_total_column_nothing_is_compared(self) -> None:
        no_total = ColumnMapping(
            sheet=MAPPING.sheet, first_row=2, columns={**MAPPING.columns, "total": None}
        )
        parsed = interpret_rows(sheet_rows([["1.1", "A", "u", 10, 15, 151]]), no_total)
        self.assertEqual(parsed.total_mismatches, 0)


class ParseBudgetTests(unittest.TestCase):
    def test_real_file_with_titles_merged_cells_and_formulas(self) -> None:
        data = build_xlsx(
            [
                ["1", "Preliminares y obras provisionales"],
                ["1.1", "Localización y replanteo", "m2", 100, 1, 1],
            ],
            title_rows=[["PRESUPUESTO DE OBRA"]],
            merges=["A1:F1", "B3:D3"],
        )
        data = with_cached_formula(data, "E4", "1000+500", "1500")
        data = with_cached_formula(data, "F4", "D4*E4", "150000")
        mapping = ColumnMapping(sheet="Presupuesto", first_row=3, columns=MAPPING.columns)
        parsed = parse_budget(data, mapping)
        self.assertEqual([r.code for r in parsed.rows], ["1", "1.1"])
        self.assertEqual(parsed.rows[0].description, "Preliminares y obras provisionales")
        self.assertEqual(parsed.rows[1].unit_price, D("1500.00"))
        self.assertEqual(parsed.rows[1].total, D("150000.00"))
        self.assertEqual(parsed.total_mismatches, 0)

    def test_sample_v1(self) -> None:
        parsed = interpret(V1_ROWS)
        self.assertEqual(parsed.direct_cost, D("9130000.00"))
        totals = {r.code: r.total for r in parsed.rows if r.kind == KIND_CHAPTER}
        self.assertEqual(totals, {"1": D("1150000.00"), "2": D("7980000.00")})


class NumberReadingTests(unittest.TestCase):
    def test_huge_product_is_rejected_with_a_message(self) -> None:
        with self.assertRaises(ExcelRejected) as caught:
            interpret([["1.1", "A", "u", 10**13, 10**14]])
        self.assertIn("el total es demasiado grande", str(caught.exception))

    def test_huge_excel_total_only_warns(self) -> None:
        parsed = interpret([["1.1", "A", "u", 1, 1, 10**40]])
        self.assertEqual(parsed.total_mismatches, 1)

    def test_dot_with_three_digits_is_read_as_thousands_and_warned(self) -> None:
        parsed = interpret(
            [
                ["1.1", "A", "u", "1.234", "2,5"],
                ["1.2", "B", "u", 3, "1.5"],
                ["1.3", "C", "u", 1, 1],
            ]
        )
        self.assertEqual(parsed.rows[0].quantity, D("1234"))
        self.assertEqual(parsed.rows[1].unit_price, D("1.50"))
        self.assertEqual(len(parsed.warnings), 1)
        self.assertIn("1 celda(s) de texto con un punto seguido de 3 dígitos", parsed.warnings[0])
        self.assertIn("fila 2 (1.234)", parsed.warnings[0])
