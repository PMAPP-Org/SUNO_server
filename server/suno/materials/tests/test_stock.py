"""Promedio ponderado móvil (spec, 2.1 y 4 "Promedio móvil"): puro, sin base de datos."""

from __future__ import annotations

import unittest
from decimal import Decimal

from suno.materials.stock import (
    Balance,
    Shortage,
    apply,
    entry,
    exit_at_average,
    returned_value,
    unit_cost,
    withdraw,
)

D = Decimal
EMPTY = Balance(D("0"), D("0.00"))


class MovingAverageTests(unittest.TestCase):
    def test_purchases_at_different_prices_move_the_average(self) -> None:
        balance = apply(EMPTY, entry(D("10"), D("1000000.00")))
        balance = apply(balance, entry(D("20"), D("2200000.00")))
        self.assertEqual(balance, Balance(D("30"), D("3200000.00")))
        self.assertEqual(balance.average, D("106666.666667"))
        self.assertIsNone(EMPTY.average)

    def test_each_exit_is_rounded_to_cents_and_the_last_takes_the_rest(self) -> None:
        balance = Balance(D("30"), D("3200000.00"))
        first = exit_at_average(balance, D("7"))
        self.assertEqual(
            (first.quantity, first.value, first.unit_cost),
            (D("-7"), D("-746666.67"), D("106666.666667")),
        )
        balance = apply(balance, first)
        self.assertEqual(balance, Balance(D("23"), D("2453333.33")))
        last = exit_at_average(balance, D("23"))
        self.assertEqual(last.value, D("-2453333.33"))
        self.assertEqual(apply(balance, last), Balance(D("0"), D("0.00")))

    def test_an_exit_never_takes_more_value_than_the_balance_has(self) -> None:
        balance = Balance(D("3000000"), D("2000000.00"))  # unitario 0,666667 (redondeado arriba)
        move = exit_at_average(balance, D("2999999"))
        self.assertEqual(move.value, D("-2000000.00"))  # 2.000.000,33 recortado
        self.assertEqual(apply(balance, move), Balance(D("1"), D("0.00")))

    def test_negative_stock_is_refused(self) -> None:
        with self.assertRaises(Shortage) as caught:
            exit_at_average(Balance(D("3"), D("300.00")), D("3.0001"))
        self.assertEqual(caught.exception.available, D("3"))
        with self.assertRaises(Shortage):
            withdraw(Balance(D("3"), D("300.00")), D("4"), D("400.00"))

    def test_a_withdrawal_takes_its_value_without_leaving_the_balance_negative(self) -> None:
        balance = Balance(D("10"), D("505.00"))
        self.assertEqual(withdraw(balance, D("5"), D("250.00")).value, D("-250.00"))
        self.assertEqual(withdraw(balance, D("5"), D("1000.00")).value, D("-505.00"))
        self.assertEqual(withdraw(balance, D("10"), D("1000.00")).value, D("-505.00"))

    def test_a_return_enters_at_the_cost_of_its_line_without_leftover_cents(self) -> None:
        unit = D("106666.666667")
        first = returned_value(D("7"), D("746666.67"), unit, D("0"), D("0.00"), D("3"))
        self.assertEqual(first, D("320000.00"))
        rest = returned_value(D("7"), D("746666.67"), unit, D("3"), first, D("4"))
        self.assertEqual(first + rest, D("746666.67"))

    def test_unit_cost_has_six_decimals(self) -> None:
        self.assertEqual(unit_cost(D("3"), D("2.00")), D("0.666667"))
        self.assertEqual(unit_cost(D("0"), D("0.00")), D("0.000000"))
