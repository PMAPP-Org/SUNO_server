"""Cálculo de contratos y actas (spec, 3.1 y 3.2). Puro: sin BD."""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.test import SimpleTestCase

from suno.budgets.tests.samples import AIU
from suno.budgets.totals import (
    BASE_DIRECT,
    BASE_SUBTOTAL,
    ChargeError,
    ChargeSpec,
    compute_charges,
    line_base,
)
from suno.contracts.calc import (
    BASE_GROSS,
    Balances,
    LineAmount,
    LineInput,
    certificate_totals,
    charge_template,
    compute_deductions,
    contract_value,
    ledger_split,
    validate_deductions,
    withholding_template,
)

from .factories import WITHHOLDINGS

D = Decimal
L1, L2, L3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
VALUE = D("8224600.00")
ADVANCE = D("2467380.00")


def lines(q1: str, q2: str) -> list[LineInput]:
    return [LineInput(L1, 1, D(q1), D("400000.00")), LineInput(L2, 2, D(q2), D("3500.00"))]


class ContractValueTests(SimpleTestCase):
    def test_value_is_direct_plus_charges(self) -> None:
        value = contract_value(lines("10", "800"), AIU)
        self.assertEqual(value.direct_cost, D("6800000.00"))
        self.assertEqual(
            [line.amount for line in value.lines],
            [D("680000.00"), D("340000.00"), D("340000.00"), D("64600.00")],
        )
        self.assertEqual(value.total, VALUE)

    def test_without_charges_the_value_is_the_direct_cost(self) -> None:
        self.assertEqual(contract_value(lines("10", "800"), []).total, D("6800000.00"))


class CertificateTotalsTests(SimpleTestCase):
    def balances(self, **values: str) -> Balances:
        return Balances(
            current_value=VALUE,
            advance_amount=ADVANCE,
            **{key: D(value) for key, value in values.items()},
        )

    def test_aiu_with_advance_retention_and_withholdings(self) -> None:
        totals = certificate_totals(
            lines("4", "200"),
            AIU,
            WITHHOLDINGS,
            retention_pct=D("5"),
            balances=self.balances(),
        )
        self.assertEqual(totals.direct, D("2300000.00"))
        self.assertEqual(totals.charges_total, D("481850.00"))
        self.assertEqual(totals.gross, D("2781850.00"))
        self.assertEqual(totals.amortization, D("834555.00"))
        self.assertEqual(totals.retention, D("139092.50"))
        self.assertEqual([d.amount for d in totals.deductions],
                         [D("46000.00"), D("22218.00"), D("3277.50")])  # fmt: skip
        self.assertEqual(totals.deductions_total, D("71495.50"))
        self.assertEqual(totals.retention_returned, D("0.00"))
        self.assertEqual(totals.net, D("1736707.00"))
        stored = totals.stored()
        self.assertEqual(stored["gross"], D("2781850.00"))
        self.assertEqual(stored["net"], D("1736707.00"))
        breakdown = totals.breakdown()
        self.assertEqual(breakdown["charges"][3]["name"], "IVA sobre utilidad")
        self.assertEqual(breakdown["deductions"][2]["amount"], "3277.50")

    def test_full_vat_and_no_charges(self) -> None:
        iva = [ChargeSpec(1, "IVA", D("19"), BASE_DIRECT)]
        balances = Balances(VALUE, D("0.00"))
        vat = certificate_totals(
            lines("4", "200"), iva, [], retention_pct=D("0"), balances=balances
        )
        self.assertEqual(vat.gross, D("2737000.00"))
        bare = certificate_totals(
            lines("4", "200"), [], [], retention_pct=D("0"), balances=balances
        )
        self.assertEqual((bare.gross, bare.net), (D("2300000.00"), D("2300000.00")))

    def test_amortization_never_passes_the_balance(self) -> None:
        totals = certificate_totals(
            lines("4", "200"),
            AIU,
            [],
            retention_pct=D("5"),
            balances=self.balances(amortized="2367380.00"),
        )
        self.assertEqual(totals.amortization, D("100000.00"))
        spent = certificate_totals(
            lines("4", "200"), AIU, [], retention_pct=D("0"),
            balances=self.balances(amortized="2467380.00"),
        )  # fmt: skip
        self.assertEqual(spent.amortization, D("0.00"))

    def test_settlement_amortizes_the_rest_and_returns_the_retention(self) -> None:
        totals = certificate_totals(
            lines("0", "0"),
            AIU,
            WITHHOLDINGS,
            retention_pct=D("5"),
            balances=self.balances(amortized="834555.00", retained="139092.50"),
            settlement=True,
        )
        self.assertEqual(totals.gross, D("0.00"))
        self.assertEqual(totals.amortization, D("1632825.00"))
        self.assertEqual(totals.retention, D("0.00"))
        self.assertEqual(totals.retention_returned, D("139092.50"))
        self.assertEqual(totals.net, D("-1493732.50"))

    def test_huge_values_keep_every_digit(self) -> None:
        big = [LineInput(L1, 1, D("99999999.9999"), D("9999999.99"))]
        totals = certificate_totals(
            big, [], [], retention_pct=D("5"),
            balances=Balances(D("999999998999000.00"), D("299999999699700.00")),
        )  # fmt: skip
        self.assertEqual(totals.direct, D("999999998999000.00"))
        self.assertEqual(totals.amortization, D("299999999699700.00"))
        self.assertEqual(totals.retention, D("49999999949950.00"))


class DeductionTests(SimpleTestCase):
    def test_bases(self) -> None:
        charges = compute_charges(D("1000.00"), AIU)
        result = compute_deductions(
            D("1000.00"),
            charges,
            [
                ChargeSpec(1, "Directo", D("10"), BASE_DIRECT),
                ChargeSpec(2, "Bruto", D("10"), BASE_GROSS),
                ChargeSpec(3, "Del IVA", D("100"), line_base(4)),
            ],
        )
        self.assertEqual([line.base_amount for line in result],
                         [D("1000.00"), charges.total, D("9.50")])  # fmt: skip
        self.assertEqual([line.amount for line in result], [D("100.00"), D("120.95"), D("9.50")])

    def test_invalid_bases_and_percentages(self) -> None:
        for wrong in (
            [ChargeSpec(1, "Subtotal", D("1"), BASE_SUBTOTAL)],
            [ChargeSpec(1, "Cargo 5", D("1"), line_base(5))],
            [ChargeSpec(2, "Desordenado", D("1"), BASE_DIRECT)],
            [ChargeSpec(1, " ", D("1"), BASE_DIRECT)],
            [ChargeSpec(1, "Más de 100", D("100.0001"), BASE_DIRECT)],
        ):
            with self.subTest(wrong=wrong), self.assertRaises(ChargeError):
                validate_deductions(wrong, charge_count=4)


class LedgerSplitTests(SimpleTestCase):
    def test_charges_in_proportion_to_the_direct_cost(self) -> None:
        amounts = [
            LineAmount(L1, 1, D("4"), D("1600000.00")),
            LineAmount(L2, 2, D("200"), D("700000.00")),
            LineAmount(L3, 3, D("0"), D("0.00")),
        ]
        self.assertEqual(
            ledger_split(amounts, D("481850.00")),
            {L1: D("1935200.00"), L2: D("846650.00")},
        )

    def test_the_rounding_goes_to_the_biggest_line_and_adds_up(self) -> None:
        amounts = [
            LineAmount(L1, 1, D("1"), D("100.00")),
            LineAmount(L2, 2, D("1"), D("100.00")),
            LineAmount(L3, 3, D("1"), D("100.00")),
        ]
        split = ledger_split(amounts, D("100.00"))
        self.assertEqual(split, {L1: D("133.34"), L2: D("133.33"), L3: D("133.33")})
        self.assertEqual(sum(split.values()), D("400.00"))

    def test_nothing_to_post(self) -> None:
        self.assertEqual(ledger_split([LineAmount(L1, 1, D("0"), D("0.00"))], D("0.00")), {})


class TemplateTests(SimpleTestCase):
    def test_charge_templates(self) -> None:
        aiu = charge_template("aiu")
        self.assertEqual(
            [c.name for c in aiu],
            ["Administración", "Imprevistos", "Utilidad", "IVA sobre utilidad"],
        )
        self.assertEqual([c.pct for c in aiu], [D("0"), D("0"), D("0"), D("19")])
        self.assertEqual(aiu[3].base, line_base(3))
        self.assertEqual(charge_template("iva"), [ChargeSpec(1, "IVA", D("19"), BASE_DIRECT)])
        self.assertEqual(charge_template("ninguno"), [])
        self.assertEqual(charge_template("otra"), [])

    def test_withholdings_find_the_vat_line(self) -> None:
        self.assertEqual(withholding_template(charge_template("aiu"))[2].base, line_base(4))
        self.assertEqual(withholding_template(charge_template("iva"))[2].base, line_base(1))
        self.assertEqual(withholding_template([])[2].base, BASE_GROSS)
        self.assertEqual({d.pct for d in withholding_template([])}, {D("0")})
