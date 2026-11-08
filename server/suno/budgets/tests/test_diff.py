"""Etiquetas de comparación y resumen de cambios de una versión (puro)."""

from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any

from suno.budgets.diff import (
    LABEL_LESS,
    LABEL_MORE,
    LABEL_NEW,
    LABEL_PRICE,
    LABEL_REMOVED,
    Values,
    row_labels,
    summarize_changes,
)
from suno.budgets.excel import KIND_CHAPTER, KIND_ITEM, ParsedBudget, interpret_rows

from .samples import MAPPING, V1_ROWS, V2_ROWS, V3_ROWS, sheet_rows

D = Decimal


def parsed(rows: list[list[Any]]) -> ParsedBudget:
    return interpret_rows(sheet_rows(rows), MAPPING)


def as_previous(rows: list[list[Any]]) -> dict[str, Values]:
    return {
        r.code: Values(r.kind, r.quantity, r.unit_price, r.total, r.description)
        for r in parsed(rows).rows
    }


def item(quantity: str, price: str) -> Values:
    return Values(KIND_ITEM, D(quantity), D(price), D(quantity) * D(price))


class RowLabelsTests(unittest.TestCase):
    def test_labels(self) -> None:
        self.assertEqual(row_labels(item("1", "10"), item("2", "10")), (LABEL_MORE,))
        self.assertEqual(row_labels(item("2", "10"), item("1", "10")), (LABEL_LESS,))
        self.assertEqual(row_labels(item("1", "10"), item("1", "12")), (LABEL_PRICE,))
        self.assertEqual(row_labels(item("1", "10"), item("2", "12")), (LABEL_MORE, LABEL_PRICE))
        self.assertEqual(row_labels(None, item("1", "10")), (LABEL_NEW,))
        self.assertEqual(row_labels(item("1", "10"), None), (LABEL_REMOVED,))
        self.assertEqual(row_labels(item("1", "10"), item("1", "10")), ())
        chapter = Values(KIND_CHAPTER, None, None, D("5"))
        bigger = Values(KIND_CHAPTER, None, None, D("9"))
        self.assertEqual(row_labels(chapter, bigger), ())
        self.assertEqual(row_labels(None, chapter), (LABEL_NEW,))


class SummarizeChangesTests(unittest.TestCase):
    def test_first_version_is_all_new(self) -> None:
        summary = summarize_changes({}, parsed(V1_ROWS), set())
        self.assertEqual(
            (summary["item_count"], summary["chapter_count"], summary["added"]), (5, 2, 5)
        )
        self.assertEqual(summary["direct_cost"], "9130000.00")
        self.assertIsNone(summary["previous_direct_cost"])

    def test_second_version_against_the_first(self) -> None:
        previous = as_previous(V1_ROWS)
        summary = summarize_changes(previous, parsed(V2_ROWS), set(previous))
        self.assertEqual(
            {
                key: summary[key]
                for key in (
                    "added",
                    "reappeared",
                    "removed",
                    "more_quantity",
                    "less_quantity",
                    "price_changed",
                )
            },
            {
                "added": 1,
                "reappeared": 0,
                "removed": 1,
                "more_quantity": 1,
                "less_quantity": 1,
                "price_changed": 1,
            },
        )
        self.assertEqual(summary["examples"]["added"], ["2.3 Formaleta"])
        self.assertEqual(summary["examples"]["removed"], ["1.2 Cerramiento provisional"])
        self.assertEqual(summary["previous_direct_cost"], "9130000.00")
        self.assertEqual(summary["direct_cost"], "8690000.00")

    def test_a_removed_item_that_comes_back_is_not_new(self) -> None:
        previous = as_previous(V2_ROWS)
        known = set(previous) | {"1.2"}
        summary = summarize_changes(previous, parsed(V3_ROWS), known)
        self.assertEqual((summary["added"], summary["reappeared"]), (0, 1))
        self.assertEqual(summary["examples"]["reappeared"], ["1.2 Cerramiento provisional"])

    def test_parser_figures_are_carried(self) -> None:
        rows = [*V1_ROWS, [None, "Subtotal", None, None, None, 1]]
        summary = summarize_changes({}, parsed(rows), set())
        self.assertEqual(summary["ignored_rows"], 1)
        self.assertEqual(summary["ignored_examples"], [9])
        self.assertEqual(len(summary["warnings"]), 1)


class SummaryWarningsTests(unittest.TestCase):
    def test_dot_thousands_warning_reaches_the_summary(self) -> None:
        summary = summarize_changes({}, parsed([["1.1", "A", "u", "1.234", 5]]), set())
        self.assertEqual(len(summary["warnings"]), 1)
        self.assertIn("miles", summary["warnings"][0])
