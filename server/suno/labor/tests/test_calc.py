"""Cálculo del tareo (spec 2.1 y 2.2, 4 "Cálculo"). Puro: `SimpleTestCase`."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from suno.labor.calc import (
    DAY_CAP,
    History,
    LineCost,
    fraction_ok,
    line_amount,
    over_cap,
    price,
    problems,
    summarize,
)

D = Decimal
PEDRO, MARIA = uuid.uuid4(), uuid.uuid4()
MON, TUE = date(2026, 3, 2), date(2026, 3, 3)


class HistoryTests(SimpleTestCase):
    def test_the_value_in_force_is_the_latest_start_not_after_the_day(self) -> None:
        wages = History.of([(date(2026, 3, 3), D("90000.00")), (date(2026, 1, 5), D("80000.00"))])
        self.assertIsNone(wages.on(date(2026, 1, 4)))
        self.assertEqual(wages.on(date(2026, 1, 5)), D("80000.00"))
        self.assertEqual(wages.on(MON), D("80000.00"))
        self.assertEqual(wages.on(TUE), D("90000.00"))  # el día exacto del cambio
        self.assertEqual(wages.on(date(2027, 1, 1)), D("90000.00"))
        self.assertIsNone(History().on(MON))


class AmountTests(SimpleTestCase):
    def test_line_amount_rounds_half_up_to_cents(self) -> None:
        self.assertEqual(line_amount(D("1"), D("80000.00"), D("1.5200")), D("121600.00"))
        self.assertEqual(line_amount(D("0.5"), D("100000.00"), D("1.52")), D("76000.00"))
        # 0,3333 × 75.000,00 × 1,5213 = 38.028,69675: 38.028,70 (se redondea cada línea)
        self.assertEqual(line_amount(D("0.3333"), D("75000.00"), D("1.5213")), D("38028.70"))
        # 0,0001 × 1,00 × 1 = 0,0001: cero centavos (no se aprueba, decisión 15)
        self.assertEqual(line_amount(D("0.0001"), D("1.00"), D("1")), D("0.00"))
        self.assertEqual(line_amount(D("0.125"), D("80000.05"), D("1")), D("10000.01"))

    def test_fraction_rules(self) -> None:
        for value, ok in (("1", True), ("1.5", True), ("0.0001", True), ("0", False),
                          ("1.5001", False), ("-0.5", False), ("0.00001", False)):  # fmt: skip
            with self.subTest(value=value):
                self.assertEqual(fraction_ok(D(value)), ok)
        self.assertFalse(fraction_ok(None))
        self.assertFalse(fraction_ok(D("NaN")))


class SummaryTests(SimpleTestCase):
    def priced(self) -> list[LineCost]:
        wages = {
            PEDRO: History.of([(date(2026, 1, 5), D("80000.00")), (TUE, D("90000.00"))]),
            MARIA: History.of([(date(2026, 1, 5), D("100000.00"))]),
        }
        factors = History.of([(date(2026, 1, 1), D("1.5200"))])
        return price(
            [
                (PEDRO, MON, "a", D("1")),
                (PEDRO, TUE, "a", D("0.5")),
                (PEDRO, TUE, "b", D("0.5")),
                (MARIA, MON, "b", D("0.3333")),
            ],
            wages,
            factors,
        )

    def test_destination_cost_is_the_sum_of_rounded_lines(self) -> None:
        summary = summarize(self.priced())
        # Pedro: 121.600,00 + 68.400,00 (jornal nuevo desde el martes) + 68.400,00
        # María: 0,3333 × 100.000 × 1,52 = 50.661,60
        self.assertEqual(summary.groups["a"].amount, D("190000.00"))
        self.assertEqual(summary.groups["b"].amount, D("119061.60"))
        self.assertEqual(summary.total, D("309061.60"))
        self.assertEqual(summary.days, D("2.3333"))
        self.assertEqual(summary.workers[str(PEDRO)].days, D("2.0000"))
        self.assertEqual(summary.lines, 4)
        self.assertTrue(summary.complete)
        self.assertEqual(
            sum(group.amount for group in summary.groups.values()), summary.total
        )  # cuadra al peso

    def test_fingerprint_changes_with_a_wage_and_snapshot_has_labels(self) -> None:
        before = summarize(self.priced()).fingerprint()
        wages = {
            PEDRO: History.of([(date(2026, 1, 5), D("80000.00"))]),
            MARIA: History.of([(date(2026, 1, 5), D("100000.00"))]),
        }
        after = summarize(
            price(
                [
                    (PEDRO, TUE, "a", D("0.5")),
                    (PEDRO, MON, "a", D("1")),
                    (PEDRO, TUE, "b", D("0.5")),
                    (MARIA, MON, "b", D("0.3333")),
                ],
                wages,
                History.of([(date(2026, 1, 1), D("1.5200"))]),
            )  # fmt: skip
        ).fingerprint()
        self.assertNotEqual(before, after)
        snapshot = summarize(self.priced()).snapshot({"a": "2.1 Concreto", "b": "Indirecto"})
        self.assertEqual(snapshot["fingerprint"], before)
        self.assertEqual([g["label"] for g in snapshot["groups"]], ["2.1 Concreto", "Indirecto"])
        self.assertEqual(snapshot["groups"][0]["total"], "190000.00")

    def test_missing_wage_or_factor_and_zero_cost_are_problems(self) -> None:
        costs = price(
            [(PEDRO, date(2026, 1, 2), "a", D("1")), (MARIA, MON, "a", D("0.0001"))],
            {PEDRO: History.of([(date(2026, 1, 5), D("80000.00"))]),
             MARIA: History.of([(date(2025, 1, 1), D("1.00"))])},
            History.of([(date(2026, 1, 1), D("1"))]),
        )  # fmt: skip
        summary = summarize(costs)
        self.assertFalse(summary.complete)
        self.assertEqual(
            problems(costs, {PEDRO: "Pedro Pérez · CC ***5678", MARIA: "María Gómez · CC ***3456"}),
            [
                "Pedro Pérez · CC ***5678 no tiene jornal vigente el 02/01/2026.",
                "La línea de María Gómez · CC ***3456 del 02/03/2026 cuesta $0: revisa la "
                "fracción.",
            ],
        )
        wages = {PEDRO: History.of([(date(2026, 1, 5), D("80000.00"))])}
        no_factor = price([(PEDRO, MON, "a", D("1"))], wages, History())
        self.assertEqual(
            problems(no_factor, {PEDRO: "Pedro"}),
            ["No hay factor prestacional vigente el 02/03/2026."],
        )


class CapTests(SimpleTestCase):
    def test_over_cap_lists_worker_days_above_one_and_a_half(self) -> None:
        self.assertEqual(DAY_CAP, D("1.5"))
        totals = {(PEDRO, MON): D("1.5"), (PEDRO, TUE): D("1.5001"), (MARIA, MON): D("2")}
        found = over_cap(totals)
        self.assertEqual(len(found), 2)
        self.assertIn((PEDRO, TUE, D("1.5001")), found)
        self.assertIn((MARIA, MON, D("2")), found)
