"""PDF del acta y Excel de contratos (spec, 2.8 y 4): contenido, `safe_cell`, auditoría."""

from __future__ import annotations

import io
import unittest
import zipfile
from datetime import date

from django.test import TestCase

from suno.contracts.pdf import certificate_pdf
from suno.contracts.queries import certificate_document
from suno.core.audit import AuditAction
from suno.core.models import AuditEvent
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import load_catalog

from .factories import contract_world, make_certificate, make_contract

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def sheets(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as book:
        return [
            book.read(name).decode()
            for name in sorted(book.namelist())
            if name.startswith("xl/worksheets/sheet")
        ]


@skip_without_db
class ContractDocumentTests(TestCase):
    def setUp(self) -> None:
        self.cw = contract_world()
        self.world = self.cw.world
        self.contract = make_contract(self.cw)
        self.contract.contractor = '=HYPERLINK("x")'
        self.contract.save(update_fields=["contractor"])
        self.certificate = make_certificate(
            self.cw, self.contract, quantities={"2.1": "4", "2.2": "200"},
            totals={"gross": "2781850.00", "net": "1736707.00"},
        )  # fmt: skip
        self.base = f"/plataforma/proyectos/{self.world.project.pk}/contratos/"
        self.client.force_login(self.cw.contributor)

    def test_the_pdf_to_sign(self) -> None:
        ctx = self.world.ctx()
        document = certificate_document(
            ctx, self.world.project, self.contract, self.certificate,
            load_catalog(ctx, self.world.project),
        )  # fmt: skip
        content = certificate_pdf(document, generated_on=date(2026, 4, 1))
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertGreater(len(content), 1500)
        url = f"{self.base}{self.contract.pk}/actas/{self.certificate.pk}/acta.pdf"
        response = self.client.get(url)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("acta-SC-001-1", response["Content-Disposition"])
        event = AuditEvent.objects.get(action=AuditAction.CERTIFICATE_EXPORTED)
        self.assertEqual(event.metadata["contract"], str(self.contract.pk))

    def test_the_list_in_excel(self) -> None:
        response = self.client.get(self.base + "contratos.xlsx?tipo=subcontract")
        self.assertEqual(response["Content-Type"], XLSX)
        (sheet,) = sheets(response.content)
        self.assertIn(">SC-001<", sheet)
        self.assertIn('>\'=HYPERLINK("x")<', sheet)
        self.assertIn("<v>8224600", sheet)
        event = AuditEvent.objects.get(action=AuditAction.CONTRACT_EXPORTED)
        self.assertEqual(event.metadata["contracts"], 1)

    def test_one_contract_in_excel(self) -> None:
        response = self.client.get(f"{self.base}{self.contract.pk}/contrato.xlsx")
        self.assertEqual(response["Content-Type"], XLSX)
        contract, lines, certificates, amendments = sheets(response.content)
        self.assertIn(">Estructura en concreto<", contract)
        self.assertIn(">2.1 Concreto zapatas<", lines)
        self.assertIn("<v>2781850", certificates)
        self.assertIn(">numero<", amendments)

    def test_who_downloads(self) -> None:
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.base + "contratos.xlsx").status_code, 404)
