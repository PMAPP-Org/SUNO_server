"""Corte de consumos (spec, 2.6 y 4 "Corte")."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest import mock, skipUnless

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import (
    BudgetItemVersion,
    ConsumptionCut,
    CostEntry,
    CostScope,
    MaterialIssue,
    MaterialIssueLine,
    MaterialReturn,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import load_catalog
from suno.costs.tests.factories import item, support_blob
from suno.materials import cuts, movements
from suno.materials.common import SELF_DECISION, MaterialRejected
from suno.materials.destination import DestinationData
from suno.materials.movements import ReturnData, ReturnLineData

from .factories import issue, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)
MARCH_END = date(2026, 3, 31)


@skip_without_db
class CutTests(TestCase):
    """Cemento a 32.500 de promedio y varilla a 4.000. Salidas: 7 und indirectas (227.500,00),
    10 und al 2.1 en la tarea 2.1 (325.000,00) y 100 kg al 2.2 (400.000,00); devolución de
    2 und del 2.1 (65.000,00). Neto del periodo: 887.500,00."""

    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.contributor = self.mw.ctx(self.mw.cw.contributor)
        self.supervisor = self.mw.ctx(self.mw.world.supervisor)
        self.admin = self.mw.ctx()
        budget = self.mw.cw.budget
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00"), (self.mw.steel, "800", "4000.00")])
        stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])
        concrete = DestinationData(
            str(CostScope.ITEM), (item(budget, "2.1").pk,), task_id=self.mw.world.task(5).pk
        )
        issue(self.mw, [(self.mw.cement, "7")], day=date(2026, 3, 10))
        self.to_concrete = issue(
            self.mw, [(self.mw.cement, "10")], day=date(2026, 3, 12), destination=concrete
        )
        steel_item = DestinationData(str(CostScope.ITEM), (item(budget, "2.2").pk,))
        issue(self.mw, [(self.mw.steel, "100")], day=date(2026, 3, 20), destination=steel_item)
        self.give_back("2", date(2026, 3, 25))

    def give_back(self, quantity: str, day: date) -> MaterialReturn:
        line = MaterialIssueLine.objects.get(issue=self.to_concrete)
        return movements.create_return(
            self.contributor, self.project, self.to_concrete.pk,
            ReturnData(day, (ReturnLineData(line.pk, D(quantity)),)),
        )  # fmt: skip

    def signed(self, end: date) -> ConsumptionCut:
        cut = cuts.create_cut(self.contributor, self.project, end)
        ConsumptionCut.objects.filter(pk=cut.pk).update(attachment=support_blob("reporte"))
        return cut

    def approved(self, end: date) -> ConsumptionCut:
        return cuts.approve_cut(self.supervisor, self.project, self.signed(end).pk)

    def test_the_proposal_groups_by_destination_and_returns_subtract(self) -> None:
        self.assertEqual(cuts.default_start(self.mw.ctx(), self.project), date(2026, 3, 10))
        catalog = load_catalog(self.admin, self.project)
        with self.assertNumQueries(5):
            proposal = cuts.build_proposal(
                self.admin, self.project, date(2026, 3, 10), MARCH_END, catalog
            )
        self.assertEqual(
            [(group.label, group.net) for group in proposal.groups],
            [("2.1 Concreto zapatas · tarea 2.1 Columnas", D("260000.00")),
             ("2.2 Acero de refuerzo", D("400000.00")),
             ("Indirecto (proyecto)", D("227500.00"))],
        )  # fmt: skip
        self.assertEqual((proposal.issued, proposal.returned, proposal.total),
                         (D("952500.00"), D("65000.00"), D("887500.00")))  # fmt: skip
        self.assertEqual((len(proposal.issue_ids), len(proposal.return_ids)), (3, 1))
        cement = proposal.groups[0].materials[0]
        self.assertEqual((cement.code, cement.issued_quantity, cement.returned_quantity),
                         ("CEM-01", D("10"), D("2")))  # fmt: skip

    def test_approval_posts_one_cost_per_destination_and_closes_the_period(self) -> None:
        cut = self.approved(MARCH_END)
        entries = CostEntry.objects.filter(consumption_cut=cut)
        self.assertEqual(entries.count(), 3)
        self.assertEqual(sum(e.amount for e in entries), D("887500.00"))
        indirect = entries.get(scope="indirect")
        self.assertEqual(
            (indirect.description, indirect.incurred_on, indirect.document_type,
             indirect.document_number, indirect.created_by, indirect.decided_by),
            ("Corte 1 de consumos · Indirecto (proyecto)", MARCH_END, "consumption_cut",
             "Corte 1", self.mw.cw.contributor, self.mw.world.supervisor),
        )  # fmt: skip
        self.assertEqual(MaterialIssue.objects.filter(cut=cut).count(), 3)
        self.assertEqual(MaterialReturn.objects.filter(cut=cut).count(), 1)
        self.assertEqual(cut.summary["fingerprint"]["total"], "887500.00")
        with self.assertRaises(MaterialRejected):
            issue(self.mw, [(self.mw.cement, "1")], day=MARCH_END)
        self.assertEqual(cuts.default_start(self.admin, self.project), date(2026, 4, 1))

    def test_a_rename_after_printing_keeps_the_printed_destination(self) -> None:
        """#88: la descripción del libro es la del reporte impreso y firmado."""
        cut = self.signed(MARCH_END)
        printed = {group["label"] for group in cut.summary["groups"]}
        BudgetItemVersion.objects.filter(item=item(self.mw.cw.budget, "2.1")).update(
            description="Concreto ciclópeo"
        )
        cuts.approve_cut(self.supervisor, self.project, cut.pk)
        descriptions = set(
            CostEntry.objects.filter(consumption_cut=cut).values_list("description", flat=True)
        )
        self.assertEqual(descriptions, {f"Corte 1 de consumos · {label}" for label in printed})
        self.assertIn(
            "Corte 1 de consumos · 2.1 Concreto zapatas · tarea 2.1 Columnas", descriptions
        )

    def test_an_older_cut_without_keys_uses_the_current_names(self) -> None:
        """#88: un corte guardado antes (`summary.groups` sin `key`) usa el nombre vigente."""
        cut = self.signed(MARCH_END)
        summary = {**cut.summary, "groups": [
            {k: v for k, v in group.items() if k != "key"} for group in cut.summary["groups"]
        ]}  # fmt: skip
        ConsumptionCut.objects.filter(pk=cut.pk).update(summary=summary)
        BudgetItemVersion.objects.filter(item=item(self.mw.cw.budget, "2.1")).update(
            description="Concreto ciclópeo"
        )
        cuts.approve_cut(self.supervisor, self.project, cut.pk)
        descriptions = set(
            CostEntry.objects.filter(consumption_cut=cut).values_list("description", flat=True)
        )
        self.assertIn(
            "Corte 1 de consumos · 2.1 Concreto ciclópeo · tarea 2.1 Columnas", descriptions
        )

    def test_what_is_approved_is_what_was_signed(self) -> None:
        cut = self.signed(MARCH_END)
        issue(self.mw, [(self.mw.cement, "1")], day=date(2026, 3, 28))
        with self.assertRaises(MaterialRejected) as caught:
            cuts.approve_cut(self.supervisor, self.project, cut.pk)
        self.assertEqual(caught.exception.errors, [cuts.CHANGED])
        again = cuts.recalculate_cut(self.contributor, self.project, cut.pk, MARCH_END)
        self.assertIsNone(again.attachment_id)  # el reporte firmado ya no corresponde
        self.assertEqual((again.total, again.issue_count), (D("920000.00"), 4))
        with self.assertRaises(MaterialRejected) as caught:
            cuts.approve_cut(self.supervisor, self.project, cut.pk)
        self.assertEqual(caught.exception.errors, ["Sube el reporte firmado antes de aprobar."])

    def test_a_negative_destination_and_voiding_only_the_last_cut(self) -> None:
        first = self.approved(MARCH_END)
        late = self.give_back("2", date(2026, 4, 5))  # salió en el corte 1
        second = self.approved(date(2026, 4, 10))
        entry = CostEntry.objects.get(consumption_cut=second)
        self.assertEqual((entry.amount, entry.incurred_on), (D("-65000.00"), date(2026, 4, 10)))
        with self.assertRaises(MaterialRejected) as caught:
            cuts.void_cut(self.admin, self.project, first.pk, "Error")
        self.assertEqual(caught.exception.errors,
                         ["Solo se anula el último corte aprobado (el 2)."])  # fmt: skip
        voided = cuts.void_cut(self.admin, self.project, second.pk, "Devolución mal fechada")
        self.assertEqual(voided.status, "voided")
        self.assertEqual(
            sum(e.amount for e in CostEntry.objects.filter(consumption_cut=second)), D("0")
        )
        late.refresh_from_db()
        self.assertIsNone(late.cut_id)
        self.assertEqual(cuts.default_start(self.admin, self.project), date(2026, 4, 1))

    def test_one_open_cut_and_its_period(self) -> None:
        cut = cuts.create_cut(self.contributor, self.project, MARCH_END)
        with self.assertRaises(MaterialRejected) as caught:
            cuts.create_cut(self.contributor, self.project, MARCH_END)
        self.assertEqual(caught.exception.errors, [cuts.OPEN_CUT])
        cuts.delete_cut(self.contributor, self.project, cut.pk)
        for end in (date(2026, 3, 9), date(2026, 4, 16)):
            with self.subTest(end=end), self.assertRaises(MaterialRejected) as caught:
                cuts.create_cut(self.contributor, self.project, end)
            self.assertEqual(
                caught.exception.errors,
                ["El corte empieza el 10/03/2026: termina entre esa fecha y hoy."],
            )
        self.assertEqual(cuts.create_cut(self.contributor, self.project, MARCH_END).number, 1)

    def test_permissions_and_self_approval(self) -> None:
        cut = self.signed(MARCH_END)
        with self.assertRaises(PermissionDenied):
            cuts.approve_cut(self.contributor, self.project, cut.pk)
        cuts.recalculate_cut(self.supervisor, self.project, cut.pk, MARCH_END)
        ConsumptionCut.objects.filter(pk=cut.pk).update(attachment=support_blob("reporte"))
        with self.assertRaises(MaterialRejected) as caught:
            cuts.approve_cut(self.supervisor, self.project, cut.pk)
        self.assertEqual(caught.exception.errors, [SELF_DECISION])
        approved = cuts.approve_cut(self.admin, self.project, cut.pk)
        with self.assertRaises(PermissionDenied):
            cuts.void_cut(self.supervisor, self.project, approved.pk, "No")

    def test_a_changed_line_with_the_same_value_is_not_approved(self) -> None:
        cut = self.signed(MARCH_END)
        signed_summary = ConsumptionCut.objects.get(pk=cut.pk).summary
        indirect = MaterialIssue.objects.get(scope="indirect")
        MaterialIssueLine.objects.filter(issue=indirect).update(material=self.mw.sand)
        with self.assertRaises(MaterialRejected) as caught:
            cuts.approve_cut(self.supervisor, self.project, cut.pk)
        self.assertEqual(caught.exception.errors, [cuts.CHANGED])
        self.assertEqual(ConsumptionCut.objects.get(pk=cut.pk).summary, signed_summary)
        self.assertEqual(len(signed_summary["fingerprint"]["lines"]), 4)
