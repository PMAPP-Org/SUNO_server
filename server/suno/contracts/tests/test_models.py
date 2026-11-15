"""Restricciones de la base de contratos, otrosíes y actas (spec, 1)."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from suno.core.models import (
    CertificateLine,
    CertificateStatus,
    Contract,
    ContractAmendment,
    ContractCertificate,
    ContractStatus,
    CostDocumentType,
    CostEntry,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import contract_world, make_certificate, make_contract

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class ContractConstraintTests(TestCase):
    def setUp(self) -> None:
        self.cw = contract_world()
        self.world = self.cw.world

    def broken(self, **fields: object) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_contract(self.cw, **fields)  # type: ignore[arg-type]

    def test_number_is_unique_per_project_ignoring_case(self) -> None:
        make_contract(self.cw, number="SC-001")
        self.broken(number="sc-001")
        self.assertEqual(make_contract(self.cw, number="SC-002").number, "SC-002")

    def test_dates_and_percentages(self) -> None:
        self.broken(number="X-1", starts_on=date(2026, 5, 1), ends_on=date(2026, 4, 30))
        self.broken(number="X-2", advance_pct="100.0001")
        self.broken(number="X-3", retention_pct="-1")

    def test_an_approved_contract_was_approved_by_someone_else(self) -> None:
        draft = make_contract(self.cw, status=ContractStatus.DRAFT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contract.objects.filter(pk=draft.pk).update(
                status=ContractStatus.APPROVED,
                approved_by=self.world.admin,
                approved_at=timezone.now(),
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contract.objects.filter(pk=draft.pk).update(status=ContractStatus.APPROVED)

    def test_a_voided_contract_has_its_reason_and_closing(self) -> None:
        contract = make_contract(self.cw)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contract.objects.filter(pk=contract.pk).update(status=ContractStatus.VOIDED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contract.objects.filter(pk=contract.pk).update(
                status=ContractStatus.VOIDED,
                closed_by=self.world.admin,
                closed_at=timezone.now(),
                close_note="",
            )

    def test_a_rejection_keeps_the_draft_with_its_reason(self) -> None:
        draft = make_contract(self.cw, status=ContractStatus.DRAFT)
        Contract.objects.filter(pk=draft.pk).update(
            rejected_by=self.world.supervisor, rejected_at=timezone.now(), rejection_note="Falta"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contract.objects.filter(pk=draft.pk).update(rejection_note="")


@skip_without_db
class CertificateConstraintTests(TestCase):
    def setUp(self) -> None:
        self.cw = contract_world()
        self.world = self.cw.world
        self.contract = make_contract(self.cw)

    def test_number_is_unique_and_only_one_is_open(self) -> None:
        make_certificate(self.cw, self.contract, number=1, status=CertificateStatus.DRAFT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_certificate(self.cw, self.contract, number=2, status=CertificateStatus.DRAFT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_certificate(self.cw, self.contract, number=1, start=date(2026, 4, 1),
                             end=date(2026, 4, 30))  # fmt: skip
        make_certificate(
            self.cw, self.contract, number=2, start=date(2026, 4, 1), end=date(2026, 4, 30)
        )

    def test_period_order(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_certificate(self.cw, self.contract, start=date(2026, 4, 2), end=date(2026, 4, 1))

    def test_approval_needs_the_signed_copy_and_someone_else(self) -> None:
        draft = make_certificate(self.cw, self.contract, status=CertificateStatus.DRAFT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContractCertificate.objects.filter(pk=draft.pk).update(
                status=CertificateStatus.APPROVED,
                decided_by=self.world.supervisor,
                decided_at=timezone.now(),
            )  # sin soporte
        approved = make_certificate(
            self.cw, self.contract, number=2, start=date(2026, 4, 1), end=date(2026, 4, 30)
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContractCertificate.objects.filter(pk=approved.pk).update(decided_by=self.world.admin)

    def test_voided_needs_its_reason(self) -> None:
        approved = make_certificate(self.cw, self.contract)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContractCertificate.objects.filter(pk=approved.pk).update(
                status=CertificateStatus.VOIDED
            )

    def test_a_changed_quantity_needs_its_justification(self) -> None:
        certificate = make_certificate(self.cw, self.contract, quantities={"2.1": "2"})
        line = CertificateLine.objects.get(certificate=certificate, line__item__code="2.1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CertificateLine.objects.filter(pk=line.pk).update(quantity=Decimal("3"))
        CertificateLine.objects.filter(pk=line.pk).update(
            quantity=Decimal("3"), justification="Medición en obra"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            CertificateLine.objects.filter(pk=line.pk).update(
                quantity=Decimal("-1"), proposed_quantity=Decimal("-1")
            )

    def test_amendments_are_numbered_and_one_is_open(self) -> None:
        def amendment(number: int) -> ContractAmendment:
            return ContractAmendment.objects.create(
                org=self.world.org,
                contract=self.contract,
                project=self.world.project,
                number=number,
                signed_on=date(2026, 4, 10),
                reason="Mayor cantidad",
                created_by=self.world.admin,
                updated_by=self.world.admin,
            )

        amendment(1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            amendment(2)

    def test_the_ledger_knows_its_certificate(self) -> None:
        field = CostEntry._meta.get_field("certificate")
        self.assertTrue(field.null)
        self.assertIn(CostDocumentType.CERTIFICATE, CostDocumentType.values)
