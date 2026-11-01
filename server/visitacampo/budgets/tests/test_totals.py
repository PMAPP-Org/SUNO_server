"""Dinero, números y cargos del presupuesto (puro)."""

from __future__ import annotations

import unittest
from decimal import Decimal

from visitacampo.budgets.totals import (
    BASE_DIRECT,
    BASE_SUBTOTAL,
    ChargeError,
    ChargeSpec,
    compute_charges,
    default_charges,
    describe_base,
    item_total,
    line_base,
    money,
    parse_number,
    pct_change,
    pct_text,
    validate_charges,
)

D = Decimal


class MoneyTests(unittest.TestCase):
    def test_rounds_half_up_to_cents(self) -> None:
        self.assertEqual(money(D("0.125")), D("0.13"))
        self.assertEqual(money(D("2.675")), D("2.68"))
        self.assertEqual(money(D("-0.125")), D("-0.13"))

    def test_item_total_is_quantity_times_price_in_cents(self) -> None:
        self.assertEqual(item_total(D("2.5"), D("0.33")), D("0.83"))  # 0,825 sube

    def test_pct_change(self) -> None:
        self.assertEqual(pct_change(D("100"), D("120")), D("20.00"))
        self.assertEqual(pct_change(D("3"), D("2")), D("-33.33"))
        self.assertIsNone(pct_change(D("0"), D("5")))

    def test_pct_text(self) -> None:
        self.assertEqual(pct_text(D("10.0000")), "10")
        self.assertEqual(pct_text(D("12.5000")), "12,5")
        self.assertEqual(pct_text(D("0")), "0")


class ParseNumberTests(unittest.TestCase):
    def test_numbers_and_text(self) -> None:
        cases = {
            None: None,
            "": None,
            "  ": None,
            3: D("3"),
            0.1: D("0.1"),
            D("1.50"): D("1.50"),
            "1.234,56": D("1234.56"),
            "1,234.56": D("1234.56"),
            "1.234.567": D("1234567"),
            "12,5": D("12.5"),
            "1,234,567": D("1234567"),
            "1.5": D("1.5"),
            "$ 1.500,00": D("1500.00"),
            "-5": D("-5"),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(parse_number(value), expected)

    def test_rejects_what_is_not_a_number(self) -> None:
        for value in ("abc", "1.2.3,4,5", "#DIV/0!", True, float("nan"), float("inf"), "."):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_number(value)


class ChargesTests(unittest.TestCase):
    def test_each_base_in_order_rounding_each_line(self) -> None:
        result = compute_charges(
            D("1234.50"),
            [
                ChargeSpec(1, "Administración", D("10"), BASE_DIRECT),
                ChargeSpec(2, "Póliza", D("2"), BASE_SUBTOTAL),
                ChargeSpec(3, "Sobre la administración", D("50"), line_base(1)),
            ],
        )
        self.assertEqual(
            [line.amount for line in result.lines], [D("123.45"), D("27.16"), D("61.73")]
        )
        self.assertEqual(result.lines[1].base_amount, D("1357.95"))  # directo + línea 1
        self.assertEqual(result.total, D("1446.84"))

    def test_aiu_template(self) -> None:
        template = default_charges()
        self.assertEqual(
            [(c.order, c.name, c.pct, c.base) for c in template],
            [
                (1, "Administración", D("0"), "direct"),
                (2, "Imprevistos", D("0"), "direct"),
                (3, "Utilidad", D("0"), "direct"),
                (4, "IVA sobre utilidad", D("0"), "line:3"),
            ],
        )
        pcts = [D("10"), D("5"), D("5"), D("19")]
        charges = [
            ChargeSpec(c.order, c.name, p, c.base) for c, p in zip(template, pcts, strict=True)
        ]
        result = compute_charges(D("9130000.00"), charges)
        self.assertEqual(
            [line.amount for line in result.lines],
            [D("913000.00"), D("456500.00"), D("456500.00"), D("86735.00")],
        )
        self.assertEqual(result.total, D("11042735.00"))

    def test_no_charges_total_is_direct_cost(self) -> None:
        self.assertEqual(compute_charges(D("10.00"), []).total, D("10.00"))

    def test_invalid_charges(self) -> None:
        bad = {
            "base posterior": [ChargeSpec(1, "A", D("1"), line_base(1))],
            "base desconocida": [ChargeSpec(1, "A", D("1"), "otra")],
            "orden con hueco": [ChargeSpec(2, "A", D("1"), BASE_DIRECT)],
            "negativo": [ChargeSpec(1, "A", D("-1"), BASE_DIRECT)],
            "cinco decimales": [ChargeSpec(1, "A", D("1.00001"), BASE_DIRECT)],
            "demasiado alto": [ChargeSpec(1, "A", D("1000"), BASE_DIRECT)],
            "sin nombre": [ChargeSpec(1, "  ", D("1"), BASE_DIRECT)],
            "demasiadas": [ChargeSpec(i, "A", D("1"), BASE_DIRECT) for i in range(1, 22)],
        }
        for case, charges in bad.items():
            with self.subTest(case=case), self.assertRaises(ChargeError):
                validate_charges(charges)

    def test_describe_base(self) -> None:
        names = {3: "Utilidad"}
        self.assertEqual(describe_base("direct", names), "costo directo")
        self.assertEqual(describe_base("subtotal", names), "subtotal")
        self.assertEqual(describe_base("line:3", names), "línea 3 (Utilidad)")
        self.assertEqual(describe_base("line:2", names), "línea 2")
