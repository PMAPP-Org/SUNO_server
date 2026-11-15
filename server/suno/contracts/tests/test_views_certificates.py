"""Actas en la web (spec, 2.4–2.7): propuesta, "Recalcular", ficha, firmado, bandeja, anular."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from unittest import mock

from django.test import TestCase, override_settings

from suno.core.models import (
    CertificateStatus,
    ContractAmendment,
    ContractCertificate,
    ContractStatus,
    CostEntry,
    Project,
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

TODAY = date(2026, 4, 15)


@skip_without_db
class CertificatePagesTests(TestCase):
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
        self.contract = make_contract(self.cw)
        self.concrete = contract_line(self.contract, "2.1")
        field_quantity(self.cw, 5, "2.1", "4", date(2026, 3, 10))
        field_quantity(self.cw, 3, "2.2", "200", date(2026, 3, 12))
        self.base = f"/plataforma/proyectos/{self.world.project.pk}/contratos/{self.contract.pk}/"
        self.new = self.base + "actas/nueva/"
        self.client.force_login(self.cw.contributor)

    def save(self, **extra: str) -> ContractCertificate:
        self.client.post(
            self.new, {"accion": "guardar", "desde": "2026-03-01", "hasta": "2026-03-31", **extra}
        )
        return ContractCertificate.objects.get()

    def test_new_certificate_proposes_and_recalculates(self) -> None:
        page = self.client.get(self.new + "?desde=2026-03-01&hasta=2026-03-31")
        self.assertContains(page, "2.781.850,00")  # bruto con la propuesta
        self.assertContains(page, "1.736.707,00")  # neto
        self.assertContains(page, 'value="2026-03-01"')
        default = self.client.get(self.new)
        self.assertContains(default, 'value="2026-03-01"')  # inicio del contrato
        self.assertContains(default, 'value="2026-04-15"')  # hoy
        recalculated = self.client.post(
            self.new,
            {"accion": "calcular", "desde": "2026-03-01", "hasta": "2026-03-31",
             f"cantidad_{self.concrete.pk}": "5"},
        )  # fmt: skip
        self.assertContains(recalculated, "escribe la justificación")
        self.assertFalse(ContractCertificate.objects.exists())

    def test_save_with_justification(self) -> None:
        certificate = self.save(
            **{f"cantidad_{self.concrete.pk}": "5", f"justificacion_{self.concrete.pk}": "Medido"}
        )
        self.assertEqual(certificate.status, CertificateStatus.DRAFT)
        detail = self.client.get(f"{self.base}actas/{certificate.pk}/")
        self.assertContains(detail, "Acta 1")
        self.assertContains(detail, "Medido")
        self.assertContains(detail, 'name="signed"')

    def test_signed_copy_inbox_approval_and_ledger(self) -> None:
        certificate = self.save()
        url = f"{self.base}actas/{certificate.pk}/"
        self.client.post(url + "firmada/", {"signed": pdf_upload("acta-1.pdf")})
        certificate.refresh_from_db()
        self.assertEqual(certificate.attachment_name, "acta-1.pdf")
        self.assertEqual(self.client.get(url + "soporte/").status_code, 302)
        self.client.force_login(self.world.supervisor)
        inbox = self.client.get(
            f"/plataforma/proyectos/{self.world.project.pk}/costos/por-aprobar/"
        )
        self.assertContains(inbox, "Contratos, otrosíes y actas")
        self.assertContains(inbox, "Acta 1 · SC-001")
        self.assertContains(inbox, "Por aprobar (1)")
        response = self.client.post(url + "aprobar/", {"volver": "bandeja"})
        self.assertRedirects(
            response, f"/plataforma/proyectos/{self.world.project.pk}/costos/por-aprobar/"
        )
        certificate.refresh_from_db()
        self.assertEqual(certificate.status, CertificateStatus.APPROVED)
        self.assertEqual(CostEntry.objects.filter(certificate=certificate).count(), 2)
        self.assertContains(self.client.get(url), "Movimientos en el libro")

    def test_reject_and_void(self) -> None:
        certificate = self.save()
        url = f"{self.base}actas/{certificate.pk}/"
        self.client.force_login(self.world.supervisor)
        self.client.post(url + "rechazar/", {"motivo": "Revisar el 2.2"})
        certificate.refresh_from_db()
        self.assertEqual(certificate.status, CertificateStatus.REJECTED)
        self.client.force_login(self.cw.contributor)
        self.client.post(url + "editar/", {"accion": "guardar", "desde": "2026-03-01",
                                           "hasta": "2026-03-31"})  # fmt: skip
        self.client.post(url + "firmada/", {"signed": pdf_upload()})
        self.client.force_login(self.world.supervisor)
        self.client.post(url + "aprobar/")
        self.assertEqual(self.client.post(url + "anular/", {"motivo": "x"}).status_code, 403)
        self.client.force_login(self.world.admin)
        self.client.post(url + "anular/", {"motivo": "Error de medición"})
        certificate.refresh_from_db()
        self.assertEqual(certificate.status, CertificateStatus.VOIDED)

    def test_settlement_is_for_the_manager_and_viewer_reads(self) -> None:
        self.client.force_login(self.world.supervisor)
        self.assertEqual(self.client.get(self.new + "?liquidacion=1").status_code, 403)
        self.client.force_login(self.world.admin)
        self.assertContains(self.client.get(self.new + "?liquidacion=1"), "Acta de liquidación")
        certificate = self.save()
        self.client.force_login(self.cw.viewer)
        self.assertEqual(self.client.get(f"{self.base}actas/{certificate.pk}/").status_code, 200)
        self.assertEqual(self.client.get(self.new).status_code, 403)

    def test_actas_tab_links(self) -> None:
        certificate = self.save()
        tab = self.client.get(self.base + "?pestana=actas")
        self.assertContains(tab, f"actas/{certificate.pk}/")
        self.assertNotContains(tab, "Nueva acta")  # ya hay una abierta

    # Revisión de código.

    def test_no_period_left_shows_a_message(self) -> None:  # M-2
        make_certificate(self.cw, self.contract, start=date(2026, 3, 1), end=TODAY)
        page = self.client.get(self.new)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "No hay periodo para una nueva acta")
        self.assertNotContains(page, 'name="accion"')

    def test_foreign_ids_are_404(self) -> None:  # M-7
        certificate = self.save()
        other = make_contract(self.cw, number="SC-002", lines=(("2.4", "1", "1.00", ()),))
        amendment = ContractAmendment.objects.create(
            org=self.world.org, contract=self.contract, project=self.world.project, number=1,
            signed_on=date(2026, 4, 2), reason="Curado", created_by=self.cw.contributor,
            updated_by=self.cw.contributor,
        )  # fmt: skip
        project = f"/plataforma/proyectos/{self.world.project.pk}/contratos/"
        foreign = f"{project}{other.pk}/"
        self.client.force_login(self.world.admin)
        for url in (
            f"{foreign}actas/{certificate.pk}/",
            f"{foreign}actas/{certificate.pk}/soporte/",
            f"{foreign}actas/{certificate.pk}/acta.pdf",
            f"{foreign}otrosies/{amendment.pk}/editar/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        upload = self.client.post(f"{foreign}actas/{certificate.pk}/firmada/",
                                  {"signed": pdf_upload()})  # fmt: skip
        self.assertEqual(upload.status_code, 404)
        certificate.refresh_from_db()
        self.assertIsNone(certificate.attachment_id)
        elsewhere = Project.objects.create(
            org=self.world.org, name="Otra obra", created_by=self.world.admin
        )
        moved = f"/plataforma/proyectos/{elsewhere.pk}/contratos/{self.contract.pk}/"
        for url in (
            moved,
            f"{moved}actas/{certificate.pk}/",
            f"{moved}actas/{certificate.pk}/soporte/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_no_settle_link_while_an_amendment_is_open(self) -> None:  # I-2
        self.client.force_login(self.world.admin)
        tab = self.base + "?pestana=actas"
        self.assertContains(self.client.get(tab), "?liquidacion=1")
        ContractAmendment.objects.create(
            org=self.world.org, contract=self.contract, project=self.world.project, number=1,
            signed_on=date(2026, 4, 2), reason="Curado", created_by=self.cw.contributor,
            updated_by=self.cw.contributor,
        )  # fmt: skip
        self.assertNotContains(self.client.get(tab), "?liquidacion=1")

    def test_settlement_with_zero_quantities_and_a_negative_net(self) -> None:
        make_certificate(
            self.cw, self.contract, quantities={"2.1": "4", "2.2": "200"},
            totals={"gross": "2781850.00", "amortization": "834555.00", "retention": "139092.50"},
        )  # fmt: skip
        self.client.force_login(self.world.admin)
        self.client.post(self.new, {"accion": "guardar", "liquidacion": "1",
                                    "desde": "2026-04-01", "hasta": "2026-04-15"})  # fmt: skip
        settlement = ContractCertificate.objects.get(is_settlement=True)
        self.assertEqual((settlement.gross, str(settlement.net)), (0, "-1493732.50"))
        url = f"{self.base}actas/{settlement.pk}/"
        self.client.post(url + "firmada/", {"signed": pdf_upload()})
        self.client.force_login(self.world.supervisor)
        self.client.post(url + "aprobar/")
        settlement.refresh_from_db()
        self.contract.refresh_from_db()
        self.assertEqual(settlement.status, CertificateStatus.APPROVED)
        self.assertEqual(self.contract.status, ContractStatus.SETTLED)
        self.assertFalse(CostEntry.objects.filter(certificate=settlement).exists())
        self.assertContains(self.client.get(url), "1.493.732,50")
