"""Salidas, devoluciones y traslados (spec, 2.4 y 4 "Promedio móvil")."""

from __future__ import annotations

import unittest
import uuid
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import (
    AuditEvent,
    MaterialIssue,
    MaterialIssueLine,
    MaterialReturn,
    Project,
    StockMovement,
    Warehouse,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.materials import movements
from suno.materials.common import MaterialRejected
from suno.materials.destination import INDIRECT
from suno.materials.movements import (
    IssueData,
    IssueLineData,
    ReturnData,
    ReturnLineData,
    TransferData,
)

from .factories import (
    assert_consistent,
    balance_of,
    bare_cut,
    issue,
    make_warehouse,
    material_world,
    stock_in,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)


@skip_without_db
class IssueTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.contributor = self.mw.ctx(self.mw.cw.contributor)
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])

    def data(self, quantity: str, day: date = date(2026, 3, 10)) -> IssueData:
        return IssueData(
            warehouse_id=self.mw.warehouse.pk,
            issued_on=day,
            destination=INDIRECT,
            lines=(IssueLineData(self.mw.cement.pk, D(quantity)),),
        )

    def test_an_issue_leaves_at_the_average_and_fixes_its_cost(self) -> None:
        out = issue(self.mw, [(self.mw.cement, "7")])
        line = MaterialIssueLine.objects.get(issue=out)
        self.assertEqual((out.code, out.total), ("SA-001", D("227500.00")))
        self.assertEqual((line.unit_cost, line.amount), (D("32500.000000"), D("227500.00")))
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("143"), D("4647500.00")))
        self.assertTrue(AuditEvent.objects.filter(action="material_issue.created").exists())
        assert_consistent(self, self.mw)

    def test_rounding_to_cents_and_taking_everything(self) -> None:
        stock_in(self.mw, [(self.mw.sand, "10", "100000.00")])
        stock_in(self.mw, [(self.mw.sand, "20", "110000.00")])
        self.assertEqual(issue(self.mw, [(self.mw.sand, "7")]).total, D("746666.67"))
        self.assertEqual(issue(self.mw, [(self.mw.sand, "23")]).total, D("2453333.33"))
        self.assertEqual(balance_of(self.mw, self.mw.sand), (D("0"), D("0.00")))
        assert_consistent(self, self.mw)

    def test_no_negative_stock_no_future_and_no_closed_period(self) -> None:
        cases = (
            (self.data("151"), "Solo hay 150 und de Cemento gris 50 kg en Almacén principal."),
            (self.data("1", date(2026, 4, 16)), "La fecha no puede ser futura ni anterior a 2000."),
        )
        for data, error in cases:
            with self.subTest(error=error), self.assertRaises(MaterialRejected) as caught:
                movements.create_issue(self.contributor, self.project, data)
            self.assertEqual(caught.exception.errors, [error])
        bare_cut(self.mw, status="approved")  # hasta el 31/03/2026
        with self.assertRaises(MaterialRejected) as caught:
            movements.create_issue(
                self.contributor, self.project, self.data("1", date(2026, 3, 31))
            )
        self.assertEqual(
            caught.exception.errors,
            [
                "La fecha cae en un periodo ya cortado (hasta el 31/03/2026): "
                "usa una fecha posterior."
            ],
        )
        movements.create_issue(self.contributor, self.project, self.data("1", date(2026, 4, 1)))
        self.assertFalse(MaterialIssue.objects.filter(issued_on=date(2026, 3, 31)).exists())

    def test_editing_an_issue_reapplies_the_average_of_the_moment(self) -> None:
        out = issue(self.mw, [(self.mw.cement, "7")])
        stock_in(self.mw, [(self.mw.cement, "50", "35000.00")])  # 193 / 6.397.500,00
        edited = movements.update_issue(self.contributor, self.project, out.pk, self.data("10"))
        line = MaterialIssueLine.objects.get(issue=edited)
        self.assertEqual((line.unit_cost, line.amount), (D("33125.000000"), D("331250.00")))
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("190"), D("6293750.00")))
        sequences = StockMovement.objects.filter(material=self.mw.cement).order_by("sequence")
        self.assertEqual(list(sequences.values_list("sequence", flat=True)), [1, 2, 4, 5])
        assert_consistent(self, self.mw)

    def test_deleting_an_issue_gives_everything_back(self) -> None:
        out = issue(self.mw, [(self.mw.cement, "7")])
        movements.delete_issue(self.contributor, self.project, out.pk)
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("150"), D("4875000.00")))
        self.assertFalse(MaterialIssue.objects.exists())
        self.assertTrue(AuditEvent.objects.filter(action="material_issue.deleted").exists())
        assert_consistent(self, self.mw)

    def test_an_issue_with_returns_or_in_a_cut_does_not_change(self) -> None:
        returned = issue(self.mw, [(self.mw.cement, "7")])
        line = MaterialIssueLine.objects.get(issue=returned)
        movements.create_return(
            self.contributor, self.project, returned.pk,
            ReturnData(date(2026, 3, 12), (ReturnLineData(line.pk, D("1")),)),
        )  # fmt: skip
        with self.assertRaises(MaterialRejected) as caught:
            movements.update_issue(self.contributor, self.project, returned.pk, self.data("3"))
        self.assertEqual(caught.exception.errors,
                         ["La salida tiene devoluciones: no se edita ni se borra."])  # fmt: skip
        cut = issue(self.mw, [(self.mw.cement, "1")])
        MaterialIssue.objects.filter(pk=cut.pk).update(cut=bare_cut(self.mw, status="approved"))
        with self.assertRaises(MaterialRejected) as caught:
            movements.delete_issue(self.contributor, self.project, cut.pk)
        self.assertEqual(caught.exception.errors,
                         ["La salida ya está en un corte: no se edita ni se borra."])  # fmt: skip

    def test_permissions(self) -> None:
        with self.assertRaises(PermissionDenied):
            movements.create_issue(self.mw.ctx(self.mw.cw.viewer), self.project, self.data("1"))
        theirs = issue(self.mw, [(self.mw.cement, "1")], user=self.mw.world.admin)
        with self.assertRaises(PermissionDenied):  # el Colaborador, solo las suyas
            movements.update_issue(self.contributor, self.project, theirs.pk, self.data("2"))

    def test_a_client_id_makes_a_retry_safe(self) -> None:
        client_id = uuid.uuid4()
        first = issue(self.mw, [(self.mw.cement, "7")], client_id=client_id)
        again = issue(self.mw, [(self.mw.cement, "7")], client_id=client_id)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("143"), D("4647500.00")))

    def test_a_client_id_is_unique_per_project_only(self) -> None:
        client_id = uuid.uuid4()
        first = issue(self.mw, [(self.mw.cement, "1")], client_id=client_id)
        other_org = material_world("Otra constructora")
        stock_in(other_org, [(other_org.cement, "5", "1000.00")])
        elsewhere = issue(other_org, [(other_org.cement, "1")], client_id=client_id)
        world = self.mw.world
        other = Project.objects.create(org=world.org, name="Obra B", created_by=world.admin)
        site = make_warehouse(world, "Bodega de la obra B", project=other)
        stock_in(self.mw, [(self.mw.cement, "5", "1000.00")], warehouse=site, project=other)
        sibling = issue(self.mw, [(self.mw.cement, "1")], warehouse=site, project=other,
                        client_id=client_id)  # fmt: skip
        self.assertEqual(len({first.pk, elsewhere.pk, sibling.pk}), 3)
        self.assertEqual(sibling.project_id, other.pk)
        again = issue(self.mw, [(self.mw.cement, "3")], warehouse=site, project=other,
                      client_id=client_id)  # fmt: skip
        self.assertEqual(again.pk, sibling.pk)
        self.assertEqual(MaterialIssue.objects.filter(client_id=client_id).count(), 3)

    def test_editing_an_issue_into_another_warehouse(self) -> None:
        north = make_warehouse(self.mw.world, "Frente norte")
        stock_in(self.mw, [(self.mw.cement, "20", "30000.00")], warehouse=north)
        out = issue(self.mw, [(self.mw.cement, "7")])
        moved = IssueData(
            warehouse_id=north.pk,
            issued_on=date(2026, 3, 10),
            destination=INDIRECT,
            lines=(IssueLineData(self.mw.cement.pk, D("10")),),
        )
        movements.update_issue(self.contributor, self.project, out.pk, moved)
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("150"), D("4875000.00")))
        self.assertEqual(balance_of(self.mw, self.mw.cement, north), (D("10"), D("300000.00")))
        self.assertEqual(
            set(StockMovement.objects.filter(issue=out).values_list("warehouse_id", flat=True)),
            {north.pk},
        )
        assert_consistent(self, self.mw)


@skip_without_db
class ReturnAndTransferTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.contributor = self.mw.ctx(self.mw.cw.contributor)

    def test_a_return_enters_at_the_cost_of_its_line(self) -> None:
        stock_in(self.mw, [(self.mw.sand, "10", "100000.00")])
        stock_in(self.mw, [(self.mw.sand, "20", "110000.00")])
        out = issue(self.mw, [(self.mw.sand, "7")])
        line = MaterialIssueLine.objects.get(issue=out)

        def back(quantity: str, day: date = date(2026, 3, 12)) -> MaterialReturn:
            return movements.create_return(
                self.contributor, self.project, out.pk,
                ReturnData(day, (ReturnLineData(line.pk, D(quantity)),)),
            )  # fmt: skip

        first, second = back("3"), back("4")
        self.assertEqual((first.code, first.total), ("DV-001", D("320000.00")))
        self.assertEqual(second.total, D("426666.67"))
        self.assertEqual(balance_of(self.mw, self.mw.sand), (D("30"), D("3200000.00")))
        with self.assertRaises(MaterialRejected) as caught:
            back("1")
        self.assertEqual(
            caught.exception.errors,
            ["Arena de río: con esta devolución vuelven 8 m3 y salieron 7."],
        )
        with self.assertRaises(MaterialRejected) as caught:
            back("1", date(2026, 3, 9))
        self.assertIn("La devolución no puede ser anterior a la salida.", caught.exception.errors)
        assert_consistent(self, self.mw)

    def test_a_transfer_round_trip_keeps_the_value(self) -> None:
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])
        north = make_warehouse(self.mw.world, "Frente norte")
        main = self.mw.warehouse
        lines = (IssueLineData(self.mw.cement.pk, D("10")),)
        there = movements.create_transfer(
            self.contributor,
            self.project,
            TransferData(main.pk, north.pk, date(2026, 3, 11), lines),
        )
        self.assertEqual((there.code, there.total), ("TR-001", D("325000.00")))
        self.assertEqual(balance_of(self.mw, self.mw.cement, north), (D("10"), D("325000.00")))
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("140"), D("4550000.00")))
        movements.create_transfer(
            self.contributor,
            self.project,
            TransferData(north.pk, main.pk, date(2026, 3, 12), lines),
        )
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("150"), D("4875000.00")))
        self.assertEqual(balance_of(self.mw, self.mw.cement, north), (D("0"), D("0.00")))
        with self.assertRaises(MaterialRejected) as caught:
            movements.create_transfer(
                self.contributor, self.project,
                TransferData(main.pk, main.pk, date(2026, 3, 12), lines),
            )  # fmt: skip
        self.assertEqual(caught.exception.errors, ["Un traslado va entre dos bodegas distintas."])
        assert_consistent(self, self.mw)

    def test_partial_returns_end_exactly_at_the_line_amount(self) -> None:
        stock_in(self.mw, [(self.mw.sand, "10", "100000.00")])
        stock_in(self.mw, [(self.mw.sand, "20", "110000.00")])
        out = issue(self.mw, [(self.mw.sand, "7")])
        line = MaterialIssueLine.objects.get(issue=out)
        totals = [
            movements.create_return(
                self.contributor, self.project, out.pk,
                ReturnData(date(2026, 3, 12), (ReturnLineData(line.pk, D(quantity)),)),
            ).total
            for quantity in ("2", "2", "3")
        ]  # fmt: skip
        self.assertEqual(totals, [D("213333.33"), D("213333.33"), D("320000.01")])
        self.assertEqual(sum(totals), line.amount)
        self.assertEqual(balance_of(self.mw, self.mw.sand), (D("30"), D("3200000.00")))
        assert_consistent(self, self.mw)

    def test_no_return_into_an_inactive_warehouse(self) -> None:
        stock_in(self.mw, [(self.mw.cement, "10", "32000.00")])
        out = issue(self.mw, [(self.mw.cement, "7")])
        line = MaterialIssueLine.objects.get(issue=out)
        Warehouse.objects.filter(pk=self.mw.warehouse.pk).update(active=False)
        with self.assertRaises(MaterialRejected) as caught:
            movements.create_return(
                self.contributor, self.project, out.pk,
                ReturnData(date(2026, 3, 12), (ReturnLineData(line.pk, D("1")),)),
            )  # fmt: skip
        self.assertEqual(
            caught.exception.errors,
            ["La bodega Almacén principal está inactiva: reactívala para registrar la devolución."],
        )
