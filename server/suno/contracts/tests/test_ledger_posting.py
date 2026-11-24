"""Aprobación de actas y paso al libro (spec, 3.2, 2.6 y 2.7): movimientos, topes, anulación,
liquidación y el control."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from suno.budgets.totals import BASE_DIRECT, ChargeSpec
from suno.contracts import certificates, services
from suno.contracts.certificates import CertificateData, CertificateLineData
from suno.contracts.queries import certificate_document
from suno.contracts.services import AmendmentData, ContractRejected, IncreaseData
from suno.control.earned_value import compute
from suno.control.loader import load_control_input
from suno.control.periods import MONTH
from suno.core.audit import AuditAction
from suno.core.models import (
    AppUser,
    AuditEvent,
    BudgetItemVersion,
    CertificateStatus,
    ContractCertificate,
    ContractKind,
    ContractStatus,
    CostDocumentType,
    CostEntry,
    CostKind,
    CostScope,
    CostStatus,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs import services as cost_services
from suno.costs.catalog import load_catalog
from suno.costs.services import CostRejected
from suno.costs.tests.factories import item, pdf_upload

from .factories import contract_line, contract_world, field_quantity, make_contract

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 5, 15)
MARCH = (date(2026, 3, 1), date(2026, 3, 31))
APRIL = (date(2026, 4, 1), date(2026, 4, 30))


@skip_without_db
class CertificateApprovalTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.cw = contract_world()
        self.world = self.cw.world
        self.project = self.world.project
        self.contract = make_contract(self.cw)
        self.concrete = contract_line(self.contract, "2.1")
        field_quantity(self.cw, 5, "2.1", "4", date(2026, 3, 10))
        field_quantity(self.cw, 3, "2.2", "200", date(2026, 3, 12))

    def prepared(
        self,
        period: tuple[date, date] = MARCH,
        *lines: CertificateLineData,
        user: AppUser | None = None,
        settlement: bool = False,
    ) -> ContractCertificate:
        """Un acta preparada (por el Colaborador, salvo `user`) y con el firmado subido."""
        ctx = self.world.ctx(user or self.cw.contributor)
        certificate = certificates.create_certificate(
            ctx, self.project, self.contract.pk, CertificateData(*period, tuple(lines)),
            settlement=settlement,
        )  # fmt: skip
        return certificates.attach_signed(
            ctx, self.project, self.contract.pk, certificate.pk, pdf_upload("acta.pdf"), "acta.pdf"
        )

    def approve(
        self, certificate: ContractCertificate, user: AppUser | None = None
    ) -> ContractCertificate:
        return certificates.approve_certificate(
            self.world.ctx(user or self.world.supervisor), self.project, self.contract.pk,
            certificate.pk,
        )  # fmt: skip

    def test_approval_posts_one_entry_per_line_that_adds_up_to_the_gross(self) -> None:
        certificate = self.approve(self.prepared())
        self.assertEqual(certificate.status, CertificateStatus.APPROVED)
        entries = {
            entry.items.get().code: entry
            for entry in CostEntry.objects.filter(certificate=certificate)
        }
        self.assertEqual({code: e.amount for code, e in entries.items()},
                         {"2.1": D("1935200.00"), "2.2": D("846650.00")})  # fmt: skip
        self.assertEqual(sum(e.amount for e in entries.values()), certificate.gross)
        concrete = entries["2.1"]
        self.assertEqual(concrete.kind, CostKind.SUBCONTRACT)
        self.assertEqual(concrete.incurred_on, date(2026, 3, 31))
        self.assertEqual((concrete.scope, concrete.status), (CostScope.ITEM, CostStatus.APPROVED))
        self.assertEqual(concrete.chapter_id, item(self.cw.budget, "2").pk)
        self.assertEqual(concrete.task, self.world.task(5))
        self.assertIsNone(entries["2.2"].task_id)  # cubre todas las tareas
        self.assertEqual(concrete.document_type, CostDocumentType.CERTIFICATE)
        self.assertEqual(concrete.document_number, "SC-001 · Acta 1")
        self.assertEqual(concrete.description, "Acta 1 · Contrato SC-001 · 2.1 Concreto zapatas")
        self.assertEqual(concrete.third_party, "Estructuras del Norte SAS")
        self.assertEqual((concrete.created_by, concrete.decided_by),
                         (self.cw.contributor, self.world.supervisor))  # fmt: skip
        self.assertEqual(concrete.attachment_id, certificate.attachment_id)
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.CERTIFICATE_APPROVED).exists())

    def test_a_rename_after_printing_keeps_the_printed_label_in_the_ledger(self) -> None:
        """#88: la descripción del libro es la del acta impresa y firmada, no la vigente."""
        certificate = self.prepared()
        before = certificate_document(
            self.world.ctx(), self.project, self.contract, certificate,
            load_catalog(self.world.ctx(), self.project),
        )  # fmt: skip
        BudgetItemVersion.objects.filter(item=item(self.cw.budget, "2.1")).update(
            description="Concreto ciclópeo"
        )
        self.assertIn("2.1 Concreto ciclópeo", load_catalog(self.world.ctx(), self.project)
                      .label_of(item(self.cw.budget, "2.1").pk))  # fmt: skip
        after = certificate_document(
            self.world.ctx(), self.project, self.contract, certificate,
            load_catalog(self.world.ctx(), self.project),
        )  # fmt: skip
        printed = [line.label for line in before.lines]
        self.assertEqual([line.label for line in after.lines], printed)
        self.approve(certificate)
        descriptions = sorted(
            CostEntry.objects.filter(certificate=certificate).values_list("description", flat=True)
        )
        self.assertEqual(
            descriptions, sorted(f"Acta 1 · Contrato SC-001 · {label}" for label in printed)
        )
        self.assertIn("Acta 1 · Contrato SC-001 · 2.1 Concreto zapatas", descriptions)

    def test_self_approval_missing_copy_and_roles(self) -> None:
        own = certificates.create_certificate(
            self.world.ctx(), self.project, self.contract.pk, CertificateData(*MARCH)
        )
        with self.assertRaises(ContractRejected):  # sin el firmado
            self.approve(own)
        certificates.attach_signed(self.world.ctx(), self.project, self.contract.pk, own.pk,
                                   pdf_upload(), "acta.pdf")  # fmt: skip
        with self.assertRaises(ContractRejected):  # lo preparó el administrador
            self.approve(own, self.world.admin)
        with self.assertRaises(PermissionDenied):
            self.approve(own, self.cw.contributor)
        self.assertEqual(self.approve(own).status, CertificateStatus.APPROVED)

    def test_the_cap_is_the_current_quantity(self) -> None:
        certificate = self.prepared(
            MARCH, CertificateLineData(self.concrete.pk, D("11"), "Sobreejecución medida")
        )
        with self.assertRaises(ContractRejected) as refused:
            self.approve(certificate)
        self.assertIn("otrosí", refused.exception.errors[0])
        amendment = services.create_amendment(
            self.world.ctx(), self.project, self.contract.pk,
            AmendmentData(date(2026, 5, 2), "Mayor cantidad",
                          increases=(IncreaseData(self.concrete.pk, D("2")),)),
        )  # fmt: skip
        # El otrosí cambia el valor vigente (y la amortización): el acta se rehace después.
        certificates.delete_certificate(
            self.world.ctx(self.cw.contributor), self.project, self.contract.pk, certificate.pk
        )
        services.approve_amendment(self.world.ctx(self.world.supervisor), self.project,
                                   self.contract.pk, amendment.pk)  # fmt: skip
        again = self.prepared(
            MARCH, CertificateLineData(self.concrete.pk, D("11"), "Sobreejecución medida")
        )
        self.assertEqual(self.approve(again).status, CertificateStatus.APPROVED)

    def test_piecework_goes_to_labor_and_the_rounding_adds_up(self) -> None:
        piecework = make_contract(
            self.cw,
            number="DT-001",
            kind=ContractKind.PIECEWORK,
            lines=(("1.1", "1", "100.00", ()), ("1.2", "1", "100.00", ()),
                   ("2.4", "1", "100.00", ())),
            charges=[ChargeSpec(1, "Cargo", D("33.3333"), BASE_DIRECT)],
            deductions=[],
            advance_pct="0",
            retention_pct="0",
        )  # fmt: skip
        ctx = self.world.ctx(self.cw.contributor)
        lines = tuple(
            CertificateLineData(line.pk, D("1"), "Medido en obra")
            for line in piecework.lines.order_by("order")
        )
        draft = certificates.create_certificate(
            ctx, self.project, piecework.pk, CertificateData(*MARCH, lines)
        )
        certificates.attach_signed(ctx, self.project, piecework.pk, draft.pk, pdf_upload(),
                                   "acta.pdf")  # fmt: skip
        approved = certificates.approve_certificate(
            self.world.ctx(self.world.supervisor), self.project, piecework.pk, draft.pk
        )
        entries = {e.items.get().code: e for e in CostEntry.objects.filter(certificate=approved)}
        self.assertEqual({code: e.amount for code, e in entries.items()},
                         {"1.1": D("133.34"), "1.2": D("133.33"), "2.4": D("133.33")})  # fmt: skip
        self.assertEqual({e.kind for e in entries.values()}, {CostKind.LABOR})

    def test_only_the_last_one_is_voided_with_reversals_dated_today(self) -> None:
        first = self.approve(self.prepared())
        second = self.approve(
            self.prepared(APRIL, CertificateLineData(self.concrete.pk, D("2"), "Abril"))
        )
        with self.assertRaises(ContractRejected):
            certificates.void_certificate(self.world.ctx(), self.project, self.contract.pk,
                                          first.pk, "Error")  # fmt: skip
        with self.assertRaises(PermissionDenied):  # el Revisor no anula
            certificates.void_certificate(self.world.ctx(self.world.supervisor), self.project,
                                          self.contract.pk, second.pk, "Error")  # fmt: skip
        voided = certificates.void_certificate(
            self.world.ctx(), self.project, self.contract.pk, second.pk, "Error de medición"
        )
        self.assertEqual((voided.status, voided.void_note), ("voided", "Error de medición"))
        reversals = CostEntry.objects.filter(certificate=second, reverses__isnull=False)
        self.assertEqual(reversals.count(), 1)
        self.assertEqual({r.incurred_on for r in reversals}, {TODAY})
        self.assertEqual(sum(e.amount for e in CostEntry.objects.filter(certificate=second)), 0)
        certificates.void_certificate(self.world.ctx(), self.project, self.contract.pk, first.pk,
                                      "Se rehace")  # fmt: skip
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.CERTIFICATE_VOIDED).exists())

    def test_settlement_amortizes_returns_and_closes_the_contract(self) -> None:
        self.approve(self.prepared())
        settlement = self.prepared(
            (date(2026, 4, 1), date(2026, 4, 15)), user=self.world.admin, settlement=True
        )
        self.assertEqual(settlement.amortization, D("1632825.00"))
        self.assertEqual(settlement.retention_returned, D("139092.50"))
        self.assertEqual(settlement.net, D("-1493732.50"))
        approved = self.approve(settlement)
        self.assertFalse(CostEntry.objects.filter(certificate=approved).exists())  # bruto 0
        self.contract.refresh_from_db()
        self.assertEqual((self.contract.status, self.contract.closed_by),
                         (ContractStatus.SETTLED, self.world.supervisor))  # fmt: skip
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.CONTRACT_SETTLED).exists())
        with self.assertRaises(ContractRejected):
            self.prepared(APRIL)
        certificates.void_certificate(self.world.ctx(), self.project, self.contract.pk,
                                      approved.pk, "Faltaba una cantidad")  # fmt: skip
        self.contract.refresh_from_db()
        self.assertEqual((self.contract.status, self.contract.closed_by_id),
                         (ContractStatus.APPROVED, None))  # fmt: skip

    def test_the_ledger_refuses_to_reverse_a_certificate_entry(self) -> None:
        certificate = self.approve(self.prepared())
        entry = CostEntry.objects.filter(certificate=certificate).first()
        assert entry is not None
        with self.assertRaises(CostRejected) as refused:
            cost_services.reverse(self.world.ctx(), self.project, entry.pk, "x")
        self.assertIn("acta", refused.exception.errors[0])

    def test_the_control_counts_the_certificate(self) -> None:
        certificate = self.approve(self.prepared())
        ctx = self.world.ctx()
        result = compute(load_control_input(ctx, self.project, cut=TODAY, kind=MONTH, costs=True))
        assert result.cost is not None
        self.assertEqual(result.cost.ac_direct, certificate.gross)

    # Revisión de código, I-1: lo aprobado es lo firmado.

    def test_the_previous_one_is_not_voided_while_a_draft_is_open(self) -> None:
        first = self.approve(self.prepared())
        self.prepared(APRIL, CertificateLineData(self.concrete.pk, D("2"), "Abril"))
        with self.assertRaises(ContractRejected) as refused:
            certificates.void_certificate(self.world.ctx(), self.project, self.contract.pk,
                                          first.pk, "Error")  # fmt: skip
        self.assertIn("acta abierta", refused.exception.errors[0])
        first.refresh_from_db()
        self.assertEqual(first.status, CertificateStatus.APPROVED)

    def test_an_amendment_is_not_approved_while_a_certificate_is_open(self) -> None:
        self.prepared()
        amendment = services.create_amendment(
            self.world.ctx(), self.project, self.contract.pk,
            AmendmentData(date(2026, 5, 2), "Mayor cantidad",
                          increases=(IncreaseData(self.concrete.pk, D("2")),)),
        )  # fmt: skip
        with self.assertRaises(ContractRejected) as refused:
            services.approve_amendment(self.world.ctx(self.world.supervisor), self.project,
                                       self.contract.pk, amendment.pk)  # fmt: skip
        self.assertIn("acta abierta", refused.exception.errors[0])

    def test_changed_values_are_refused_at_approval(self) -> None:
        certificate = self.prepared()
        ContractCertificate.objects.filter(pk=certificate.pk).update(net=D("1.00"))
        with self.assertRaises(ContractRejected) as refused:
            self.approve(certificate)
        self.assertIn("Los valores del acta cambiaron", refused.exception.errors[0])
        self.assertFalse(CostEntry.objects.filter(certificate=certificate).exists())
        line = certificate.lines.get(line=self.concrete)
        type(line).objects.filter(pk=line.pk).update(amount=D("1.00"))
        ContractCertificate.objects.filter(pk=certificate.pk).update(net=D("1736707.00"))
        with self.assertRaises(ContractRejected):
            self.approve(certificate)

    # Revisión de código, I-2: no se liquida con un otrosí abierto.

    def open_amendment(self) -> None:
        services.create_amendment(
            self.world.ctx(), self.project, self.contract.pk,
            AmendmentData(date(2026, 5, 2), "Mayor cantidad",
                          increases=(IncreaseData(self.concrete.pk, D("2")),)),
        )  # fmt: skip

    def test_no_settlement_is_created_while_an_amendment_is_open(self) -> None:
        self.approve(self.prepared())
        self.open_amendment()
        with self.assertRaises(ContractRejected) as refused:
            self.prepared((date(2026, 4, 1), date(2026, 4, 15)), user=self.world.admin,
                          settlement=True)  # fmt: skip
        self.assertIn("otrosí abierto", refused.exception.errors[0])

    def test_no_settlement_is_approved_while_an_amendment_is_open(self) -> None:
        self.approve(self.prepared())
        settlement = self.prepared(
            (date(2026, 4, 1), date(2026, 4, 15)), user=self.world.admin, settlement=True
        )
        self.open_amendment()
        with self.assertRaises(ContractRejected) as refused:
            self.approve(settlement)
        self.assertIn("otrosí abierto", refused.exception.errors[0])
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.status, ContractStatus.APPROVED)
