"""Consultas de la pestaña "Contratos" (spec, 2.1, 2.3, 3.3 y 4)."""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from suno.contracts import queries
from suno.contracts.queries import ListFilters
from suno.core.audit import AuditAction
from suno.core.models import (
    AmendmentStatus,
    CertificateStatus,
    ContractAmendment,
    ContractCertificate,
    ContractKind,
    ContractLine,
    ContractStatus,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import load_catalog
from suno.costs.tests.factories import item

from .factories import (
    contract_world,
    field_quantity,
    make_certificate,
    make_contract,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 9, 15)


@skip_without_db
class ContractQueryTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cw = contract_world()
        self.world = self.cw.world
        self.project = self.world.project
        self.ctx = self.world.ctx()
        self.contract = make_contract(self.cw)  # 8.224.600, anticipo 2.467.380, fin 31/08/2026
        make_certificate(
            self.cw, self.contract, quantities={"2.1": "4", "2.2": "200"},
            totals={"gross": "2781850.00", "amortization": "834555.00",
                    "retention": "139092.50"},
        )  # fmt: skip
        make_certificate(
            self.cw, self.contract, number=2, start=date(2026, 4, 1), end=date(2026, 4, 30),
            quantities={"2.1": "1"}, status=CertificateStatus.VOIDED,
            totals={"gross": "999.00"},
        )  # fmt: skip
        self.piecework = make_contract(
            self.cw, number="DT-001", kind=ContractKind.PIECEWORK, status=ContractStatus.DRAFT,
            lines=(("2.4", "40", "3000.00", ()),), charges=[], deductions=[],
            advance_pct="0", retention_pct="0",
        )  # fmt: skip

    def test_list_rows_with_balances_in_two_queries(self) -> None:
        with self.assertNumQueries(2):
            rows = queries.contract_rows(self.ctx, self.project, ListFilters())
        self.assertEqual([row.contract.number for row in rows], ["DT-001", "SC-001"])
        row = rows[1]
        self.assertEqual(row.progress.executed, D("2781850.00"))  # la anulada no cuenta
        self.assertEqual(row.executed_pct, D("33.8"))
        self.assertEqual(row.advance_balance, D("1632825.00"))
        self.assertEqual(row.retained, D("139092.50"))
        only_drafts = queries.contract_rows(self.ctx, self.project, ListFilters(status="draft"))
        self.assertEqual([r.contract.number for r in only_drafts], ["DT-001"])
        subcontracts = ListFilters.from_query({"tipo": "subcontract", "estado": "nada"})
        self.assertEqual((subcontracts.kind, subcontracts.status), ("subcontract", ""))
        self.assertEqual(subcontracts.query, "tipo=subcontract")

    def test_the_sheet_of_a_contract(self) -> None:
        field_quantity(self.cw, 5, "2.1", "6", date(2026, 3, 10))
        catalog = load_catalog(self.ctx, self.project)
        with self.assertNumQueries(8):
            sheet = queries.contract_sheet(self.ctx, self.project, self.contract, catalog)
        concrete = sheet.lines[0]
        self.assertEqual(concrete.label, "2.1 Concreto zapatas")
        self.assertEqual(concrete.tasks, "2.1 Columnas")
        self.assertEqual(sheet.lines[1].tasks, "Todas")
        self.assertEqual((concrete.contracted, concrete.paid, concrete.balance),
                         (D(10), D(4), D(6)))  # fmt: skip
        self.assertEqual(concrete.balance_value, D("2400000.00"))
        self.assertEqual(concrete.unpaid_validated, D(2))
        self.assertEqual([c.number for c in sheet.certificates], [1, 2])
        self.assertEqual(sheet.advance_balance, D("1632825.00"))
        self.assertIsNone(sheet.open_certificate)

    def test_history_gathers_the_contract_and_its_certificates(self) -> None:
        from suno.core import audit

        audit.record(self.ctx, action=AuditAction.CONTRACT_APPROVED,
                     target=f"contract:{self.contract.pk}", metadata={})  # fmt: skip
        audit.record(self.ctx, action=AuditAction.CERTIFICATE_APPROVED, target="certificate:x",
                     metadata={"contract": str(self.contract.pk)})  # fmt: skip
        audit.record(self.ctx, action=AuditAction.CERTIFICATE_APPROVED, target="certificate:y",
                     metadata={"contract": "otro"})  # fmt: skip
        events = queries.contract_history(self.ctx, self.contract)
        self.assertEqual(len(events), 2)
        self.assertEqual(queries.ACTION_LABELS[AuditAction.CONTRACT_APPROVED], "Contrato aprobado")

    def test_tab_alerts(self) -> None:
        # Vencido (fin 31/08) y validado sin pagar: 6 m3 más del 2.1.
        field_quantity(self.cw, 5, "2.1", "6", date(2026, 8, 10))
        # Un borrador de acta de hace 16 días.
        stale = make_certificate(self.cw, self.contract, number=3, start=date(2026, 5, 1),
                                 end=date(2026, 5, 31), status=CertificateStatus.DRAFT)  # fmt: skip
        ContractCertificate.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(days=16)
        )
        # Ejecutado al 100 % con anticipo por amortizar.
        full = make_contract(self.cw, number="SC-002", lines=(("1.1", "10", "1000.00", ()),),
                             charges=[], deductions=[], advance_pct="50")  # fmt: skip
        make_certificate(self.cw, full, quantities={"1.1": "10"},
                         totals={"amortization": "1000.00"})  # fmt: skip
        # Traslape: el destajo en borrador y otro contrato aprobado en el 2.4.
        make_contract(self.cw, number="SC-003", lines=(("2.4", "1", "1.00", ()),),
                      charges=[], deductions=[])  # fmt: skip
        alerts = queries.tab_alerts(self.ctx, self.project, load_catalog(self.ctx, self.project))
        texts = " | ".join(f"{alert.kind}:{alert.text}" for alert in alerts)
        self.assertIn("stale:Acta 3 de SC-001", texts)
        self.assertIn("unpaid:SC-001 · 2.1 Concreto zapatas: 2 validado sin pagar", texts)
        self.assertIn("overdue:SC-001: venció el 31/08/2026", texts)
        self.assertIn("advance:SC-002", texts)
        self.assertIn("overlap:2.4 Curado: DT-001 y SC-003", texts)

    def test_committed_by_item(self) -> None:
        settled = make_contract(self.cw, number="SC-002", status=ContractStatus.SETTLED,
                                lines=(("1.1", "10", "1000.00", ()),), charges=[],
                                deductions=[])  # fmt: skip
        make_certificate(self.cw, settled, quantities={"1.1": "3"})
        make_contract(self.cw, number="SC-003", status=ContractStatus.VOIDED,
                      lines=(("1.2", "10", "1000.00", ()),), charges=[], deductions=[])  # fmt: skip
        pending = ContractAmendment.objects.create(
            org=self.world.org, contract=self.contract, project=self.project, number=1,
            signed_on=date(2026, 5, 2), reason="Curado", created_by=self.world.admin,
            updated_by=self.world.admin, status=AmendmentStatus.DRAFT,
        )  # fmt: skip
        ContractLine.objects.create(
            org=self.world.org, contract=self.contract, order=3, item=item(self.cw.budget, "1.2"),
            quantity=D(1), current_quantity=D(1), unit_price=D("5.00"), amendment=pending,
        )  # fmt: skip
        committed = queries.committed_by_item(self.ctx, self.project)
        self.assertEqual(committed[item(self.cw.budget, "2.1").pk], D("4000000.00"))
        self.assertEqual(committed[item(self.cw.budget, "1.1").pk], D("3000.00"))
        self.assertNotIn(item(self.cw.budget, "1.2").pk, committed)  # anulado y otrosí sin aprobar
        self.assertNotIn(item(self.cw.budget, "2.4").pk, committed)  # el destajo es borrador
        self.assertEqual(
            queries.contracts_on_item(self.ctx, self.project, item(self.cw.budget, "2.1").pk),
            ["SC-001"],
        )

    def test_inbox_excludes_what_the_user_prepared(self) -> None:
        make_certificate(self.cw, self.contract, number=3, start=date(2026, 5, 1),
                         end=date(2026, 5, 31), status=CertificateStatus.DRAFT)  # fmt: skip
        mine = queries.inbox_items(self.ctx, self.project)  # el administrador preparó todo
        self.assertEqual(mine.count, 0)
        theirs = queries.inbox_items(self.world.ctx(self.world.supervisor), self.project)
        self.assertEqual(([c.number for c in theirs.contracts], len(theirs.certificates)),
                         (["DT-001"], 1))  # fmt: skip
        self.assertEqual(theirs.count, 2)
        self.assertEqual(
            queries.inbox_count(self.world.ctx(self.world.supervisor), self.project), 2
        )

    def test_certificate_document(self) -> None:
        second = make_certificate(self.cw, self.contract, number=3, start=date(2026, 5, 1),
                                  end=date(2026, 5, 31), quantities={"2.1": "2"})  # fmt: skip
        catalog = load_catalog(self.ctx, self.project)
        with self.assertNumQueries(2):
            document = queries.certificate_document(
                self.ctx, self.project, self.contract, second, catalog
            )
        concrete = document.lines[0]
        self.assertEqual((concrete.contracted, concrete.previous, concrete.this),
                         (D(10), D(4), D(2)))  # fmt: skip
        self.assertEqual((concrete.accumulated, concrete.balance), (D(6), D(4)))
        self.assertEqual(concrete.balance_value, D("1600000.00"))

    def test_suggested_numbers(self) -> None:
        self.assertEqual(queries.suggest_number(self.ctx, self.project, "subcontract"), "SC-002")
        self.assertEqual(queries.suggest_number(self.ctx, self.project, "piecework"), "DT-002")
