"""Actas en borrador (spec, 2.4, 2.5 y 3.1): propuesta, ajuste, periodo, firmado, rechazo."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from suno.contracts import certificates
from suno.contracts.certificates import CertificateData, CertificateLineData, default_period
from suno.contracts.services import ContractRejected
from suno.core.audit import AuditAction
from suno.core.models import (
    AppUser,
    AuditEvent,
    CertificateLine,
    CertificateStatus,
    ContractCertificate,
    ContractStatus,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload

from .factories import (
    contract_line,
    contract_world,
    field_quantity,
    make_certificate,
    make_contract,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)
MARCH = (date(2026, 3, 1), date(2026, 3, 31))


@skip_without_db
class CertificateDraftTests(TestCase):
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

    def create(
        self,
        user: AppUser | None = None,
        *lines: CertificateLineData,
        period: tuple[date, date] = MARCH,
        **kwargs: Any,
    ) -> ContractCertificate:
        return certificates.create_certificate(
            self.world.ctx(user or self.cw.contributor),
            self.project,
            self.contract.pk,
            CertificateData(period[0], period[1], tuple(lines)),
            **kwargs,
        )

    def test_create_proposes_from_the_field_and_computes_totals(self) -> None:
        certificate = self.create()
        self.assertEqual((certificate.number, certificate.status), (1, CertificateStatus.DRAFT))
        self.assertEqual(certificate.gross, D("2781850.00"))
        self.assertEqual(certificate.amortization, D("834555.00"))
        self.assertEqual(certificate.net, D("1736707.00"))
        self.assertEqual(len(certificate.breakdown["charges"]), 4)
        lines = {
            row.line.item.code: (row.proposed_quantity, row.quantity)
            for row in CertificateLine.objects.filter(certificate=certificate).select_related(
                "line__item"
            )
        }
        self.assertEqual(lines, {"2.1": (D(4), D(4)), "2.2": (D(200), D(200))})
        event = AuditEvent.objects.get(action=AuditAction.CERTIFICATE_CREATED)
        self.assertEqual(event.metadata["contract"], str(self.contract.pk))

    def test_an_adjusted_quantity_needs_its_justification(self) -> None:
        with self.assertRaises(ContractRejected) as refused:
            self.create(None, CertificateLineData(self.concrete.pk, D("5")))
        self.assertIn("justificación", refused.exception.errors[0])
        certificate = self.create(
            None, CertificateLineData(self.concrete.pk, D("5"), "Medición topográfica")
        )
        row = CertificateLine.objects.get(certificate=certificate, line=self.concrete)
        self.assertEqual((row.proposed_quantity, row.quantity), (D(4), D(5)))
        self.assertEqual(row.justification, "Medición topográfica")

    def test_only_one_open_and_only_on_approved_contracts(self) -> None:
        self.create()
        with self.assertRaises(ContractRejected):
            self.create(period=(date(2026, 4, 1), date(2026, 4, 10)))
        for status in (ContractStatus.DRAFT, ContractStatus.SETTLED, ContractStatus.VOIDED):
            other = make_contract(self.cw, number=f"X-{status}", status=status,
                                  lines=(("2.4", "1", "1.00", ()),))  # fmt: skip
            with self.subTest(status=status), self.assertRaises(ContractRejected):
                certificates.create_certificate(
                    self.world.ctx(), self.project, other.pk, CertificateData(*MARCH)
                )

    def test_periods_do_not_cross_nor_end_in_the_future(self) -> None:
        make_certificate(self.cw, self.contract, start=date(2026, 3, 1), end=date(2026, 3, 15))
        with self.assertRaises(ContractRejected) as crossing:
            self.create(period=(date(2026, 3, 10), date(2026, 3, 31)))
        self.assertIn("acta 1", crossing.exception.errors[0])
        with self.assertRaises(ContractRejected):
            self.create(period=(date(2026, 3, 16), date(2026, 4, 16)))
        with self.assertRaises(ContractRejected):
            self.create(period=(date(2026, 3, 20), date(2026, 3, 16)))
        self.assertEqual(self.create(period=(date(2026, 3, 16), date(2026, 3, 31))).number, 2)

    def test_default_period(self) -> None:
        ctx = self.world.ctx()
        self.assertEqual(default_period(ctx, self.contract), (date(2026, 3, 1), TODAY))
        make_certificate(self.cw, self.contract, start=date(2026, 3, 1), end=date(2026, 3, 15))
        self.assertEqual(default_period(ctx, self.contract), (date(2026, 3, 16), TODAY))

    def test_the_signed_copy_is_not_an_edit_and_editing_removes_it(self) -> None:
        certificate = self.create()
        signed = certificates.attach_signed(
            self.world.ctx(), self.project, self.contract.pk, certificate.pk,
            pdf_upload("acta-1.pdf"), "acta-1.pdf",
        )  # fmt: skip
        self.assertIsNotNone(signed.attachment_id)
        self.assertEqual(signed.updated_by, self.cw.contributor)
        with self.assertRaises(ContractRejected):
            certificates.attach_signed(
                self.world.ctx(), self.project, self.contract.pk, certificate.pk,
                SimpleUploadedFile("nota.txt", b"hola mundo"), "nota.txt",
            )  # fmt: skip
        edited = certificates.update_certificate(
            self.world.ctx(self.cw.contributor), self.project, self.contract.pk, certificate.pk,
            CertificateData(*MARCH),
        )  # fmt: skip
        self.assertIsNone(edited.attachment_id)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditAction.CERTIFICATE_UPDATED, metadata__signed_removed=True
            ).exists()
        )

    def test_rejection_and_resubmission_by_the_author(self) -> None:
        certificate = self.create()
        rejected = certificates.reject_certificate(
            self.world.ctx(self.world.supervisor), self.project, self.contract.pk,
            certificate.pk, "Falta la medición del 2.2",
        )  # fmt: skip
        self.assertEqual(rejected.status, CertificateStatus.REJECTED)
        with self.assertRaises(ContractRejected):  # el firmado va a un borrador
            certificates.attach_signed(
                self.world.ctx(self.cw.contributor), self.project, self.contract.pk,
                certificate.pk, pdf_upload(), "acta.pdf",
            )  # fmt: skip
        with self.assertRaises(PermissionDenied):
            certificates.update_certificate(
                self.world.ctx(), self.project, self.contract.pk, certificate.pk,
                CertificateData(*MARCH),
            )  # fmt: skip
        again = certificates.update_certificate(
            self.world.ctx(self.cw.contributor), self.project, self.contract.pk, certificate.pk,
            CertificateData(*MARCH),
        )  # fmt: skip
        self.assertEqual((again.status, again.decision_note), (CertificateStatus.DRAFT, ""))

    def test_deleting_the_draft_frees_its_number(self) -> None:
        certificate = self.create()
        certificates.delete_certificate(
            self.world.ctx(self.cw.contributor), self.project, self.contract.pk, certificate.pk
        )
        self.assertFalse(ContractCertificate.objects.filter(pk=certificate.pk).exists())
        self.assertEqual(self.create().number, 1)

    def test_roles(self) -> None:
        with self.assertRaises(PermissionDenied):
            self.create(self.cw.viewer)
        with self.assertRaises(PermissionDenied):  # liquidar es del Gestor
            self.create(self.world.supervisor, settlement=True)
        self.assertTrue(self.create(self.world.admin, settlement=True).is_settlement)
