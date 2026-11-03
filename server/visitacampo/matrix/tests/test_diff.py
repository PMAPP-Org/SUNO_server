from __future__ import annotations

import uuid
from decimal import Decimal

from django.test import SimpleTestCase

from visitacampo.matrix.diff import BudgetedItem, item_totals, summarize_plan

T1, T2 = uuid.uuid4(), uuid.uuid4()
A, B, C, R = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
NAMES = {T1: "1.2 Descapote", T2: "2.1 Columnas", A: "1.1", B: "2.1", C: "2.2", R: "1.2"}


def label(pair: tuple[uuid.UUID, uuid.UUID]) -> str:
    return f"{NAMES[pair[0]]} × {NAMES[pair[1]]}"


class SummarizeTests(SimpleTestCase):
    def test_groups(self) -> None:
        current = {
            (T1, A): Decimal("10"),
            (T1, B): Decimal("5"),
            (T2, B): Decimal("1"),
            (T2, C): Decimal("3"),
        }
        new = {(T1, A): Decimal("10"), (T1, B): Decimal("6"), (T2, A): Decimal("2")}
        summary = summarize_plan(current, new, executed={(T2, C)}, label=label)
        self.assertEqual(
            (summary["added"], summary["changed"], summary["removed"], summary["kept_zero"]),
            (1, 1, 1, 1),
        )
        self.assertEqual(summary["examples"]["changed"], ["1.2 Descapote × 2.1: 5 → 6"])
        self.assertEqual(summary["examples"]["kept_zero"], ["2.1 Columnas × 2.2"])
        self.assertEqual(summary["examples"]["removed"], ["2.1 Columnas × 2.1"])


class ItemTotalsTests(SimpleTestCase):
    def test_statuses(self) -> None:
        budgeted = {
            A: BudgetedItem("1.1", "Localización", "m2", Decimal("100")),
            B: BudgetedItem("2.1", "Concreto", "m3", Decimal("10")),
            C: BudgetedItem("2.2", "Acero", "kg", Decimal("800")),
        }
        retired = {R: BudgetedItem("1.2", "Cerramiento", "ml", None)}
        totals = item_totals(
            {A: Decimal("100"), B: Decimal("12"), R: Decimal("5")}, budgeted, retired
        )
        self.assertEqual(
            totals["counts"], {"ok": 1, "over": 1, "under": 1, "retired": 1, "unassigned": 1}
        )
        rows = {row["code"]: row for row in totals["items"]}
        self.assertEqual(rows["2.1"]["status"], "over")
        self.assertEqual(rows["2.1"]["diff"], "2.0000")
        self.assertEqual(rows["2.2"]["status"], "under")
        self.assertEqual(rows["2.2"]["planned"], "0.0000")
        self.assertEqual(rows["1.2"]["status"], "retired")
        self.assertNotIn("1.1", rows)
