"""Importes con signo en la página del control (costos, pieza 5). Sin BD."""

from __future__ import annotations

from decimal import Decimal

from django.template import Context, Template
from django.test import SimpleTestCase

from suno.core.templatetags.money_tags import money, qty, signed

D = Decimal


class MoneyFilterTests(SimpleTestCase):
    def test_sign_goes_before_the_symbol(self) -> None:
        self.assertEqual(money(D("-3150000")), "−$3.150.000,00")
        self.assertEqual(money(D("900000")), "$900.000,00")
        self.assertEqual(money(D("0")), "$0,00")
        self.assertEqual(money(D("-0.001")), "$0,00")
        self.assertEqual(money(None), "")

    def test_signed_amount_without_symbol(self) -> None:
        self.assertEqual(signed(D("-20000")), "−20.000,00")
        self.assertEqual(signed(D("1350000")), "1.350.000,00")
        self.assertEqual(signed(None), "")

    def test_usable_from_a_template(self) -> None:
        template = Template("{% load money_tags %}{{ v|money }} {{ v|signed }}")
        self.assertEqual(template.render(Context({"v": D("-5")})), "−$5,00 −5,00")

    def test_quantities(self) -> None:
        self.assertEqual(qty(D("1234.5000")), "1.234,5")
        self.assertEqual(qty(D("10.0000")), "10")
        self.assertEqual(qty(D("0.0000")), "0")
        self.assertEqual(qty(None), "")
