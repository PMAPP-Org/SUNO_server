"""Costos y Control con el tareo (spec 1.4, 2.1 y 3.5): origen "Tareo" en el libro sin nombres
ni salarios, enlace solo para quien lee Mano de obra, sin reverso desde el libro, y la mano de
obra en el costo real del control."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from suno.control.loader import load_control_input
from suno.control.periods import MONTH
from suno.core.models import CostEntry
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs import services as cost_services
from suno.costs.services import CostRejected
from suno.costs.tests.factories import entry, pdf_upload
from suno.labor import approval, timesheets

from .factories import TODAY, WEEK, labor_world, line_data, to_item

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


@skip_without_db
class LedgerAndControlTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        author = self.lw.ctx(self.lw.contributor)
        sheet = timesheets.create_timesheet(author, self.lw.project, *WEEK)
        timesheets.save_lines(author, self.lw.project, sheet.pk, [
            line_data(self.lw.pedro, WEEK[0]),
            line_data(self.lw.maria, WEEK[0], "0.5", to_item(self.lw)),
        ], [WEEK[0]])  # fmt: skip
        timesheets.attach_signed(author, self.lw.project, sheet.pk, pdf_upload(), "f.pdf")
        self.sheet = approval.approve_timesheet(self.lw.ctx(self.lw.world.supervisor),
                                                self.lw.project, sheet.pk)  # fmt: skip
        entry(self.lw.world, amount="5000.00", status="approved")  # un movimiento manual
        self.costs = f"/plataforma/proyectos/{self.lw.project.pk}/costos/"

    def test_ledger_shows_the_origin_without_names_or_wages(self) -> None:
        self.client.force_login(self.lw.accountant)  # Costos (Consulta), sin Mano de obra
        page = self.client.get(self.costs)
        self.assertContains(page, "Tareo TA-001")
        self.assertNotContains(page, "/tareo/")  # sin enlace a la planilla
        for private in ("Pedro", "María", "80.000", "***5678"):
            with self.subTest(private=private):
                self.assertNotContains(page, private)
        only = self.client.get(self.costs + "?origen=tareo")
        self.assertContains(only, "Tareo TA-001")
        self.assertNotContains(only, "Concreto premezclado")  # el manual queda fuera
        self.client.force_login(self.lw.world.admin)
        self.assertContains(self.client.get(self.costs),
                            f"/tareo/{self.sheet.pk}/")  # fmt: skip

    def test_detail_offers_no_reversal_and_the_service_refuses(self) -> None:
        cost = CostEntry.objects.filter(timesheet=self.sheet).first()
        assert cost is not None
        self.client.force_login(self.lw.world.admin)
        detail = self.client.get(f"{self.costs}{cost.pk}/")
        self.assertContains(detail, "se anula desde la pestaña Tareo")
        self.assertNotContains(detail, "Reversar")
        with self.assertRaises(CostRejected) as refused:
            cost_services.reverse(self.lw.ctx(), self.lw.project, cost.pk, "No")
        self.assertIn("planilla de tareo", refused.exception.errors[0])

    def test_labor_is_actual_cost_in_control(self) -> None:
        data = load_control_input(self.lw.ctx(), self.lw.project, cut=date(2026, 3, 31),
                                  kind=MONTH, costs=True)  # fmt: skip
        assert data.costs is not None
        # Lo directo va por día al ítem (CPI directo del ítem).
        direct = [(line.amount, line.scope) for line in data.costs.lines if line.day == WEEK[1]]
        self.assertEqual(direct, [(D("76000.00"), "item")])
        # Lo indirecto se agrega en una línea al corte (CPI de administración): la mano de obra
        # indirecta (121.600) más el movimiento manual indirecto (5.000).
        indirect = [line.amount for line in data.costs.lines if line.scope == "indirect"]
        self.assertEqual(indirect, [D("126600.00")])
