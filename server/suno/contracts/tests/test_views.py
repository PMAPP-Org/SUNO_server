"""Pestaña "Contratos" (spec, 2.1–2.3 y 4): acceso, lista, formulario, ficha y otrosíes."""

from __future__ import annotations

import unittest
from datetime import date
from typing import Any
from unittest import mock

from django.test import TestCase

from suno.authn.tests.factories import make_user
from suno.core.models import (
    AmendmentStatus,
    Contract,
    ContractAmendment,
    ContractStatus,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import contract_line, contract_world, make_certificate, make_contract

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TODAY = date(2026, 5, 15)
AIU_ROWS = [
    ("Administración", "10", "direct"),
    ("Imprevistos", "5", "direct"),
    ("Utilidad", "5", "direct"),
    ("IVA sobre utilidad", "19", "line:3"),
]


def contract_post(**overrides: Any) -> dict[str, Any]:
    """El POST del formulario de contrato: encabezado, dos líneas y los cargos AIU."""
    data: dict[str, Any] = {
        "accion": "guardar",
        "kind": "subcontract",
        "number": "SC-001",
        "purpose": "Estructura en concreto",
        "contractor": "Estructuras del Norte SAS",
        "starts_on": "2026-03-01",
        "ends_on": "2026-08-31",
        "advance_pct": "30",
        "retention_pct": "5",
        "lineas-TOTAL_FORMS": "2",
        "lineas-INITIAL_FORMS": "0",
        "lineas-0-item": "2.1 Concreto zapatas",
        "lineas-0-tasks": "2.1",
        "lineas-0-unit": "m3",
        "lineas-0-quantity": "10",
        "lineas-0-unit_price": "400000",
        "lineas-1-item": "2.2 Acero de refuerzo",
        "lineas-1-tasks": "",
        "lineas-1-unit": "kg",
        "lineas-1-quantity": "800",
        "lineas-1-unit_price": "3500",
        "cargos-TOTAL_FORMS": str(len(AIU_ROWS)),
        "cargos-INITIAL_FORMS": "0",
        "descuentos-TOTAL_FORMS": "0",
        "descuentos-INITIAL_FORMS": "0",
    }
    for n, (name, pct, base) in enumerate(AIU_ROWS):
        data.update({f"cargos-{n}-name": name, f"cargos-{n}-pct": pct, f"cargos-{n}-base": base})
    data.update(overrides)
    return data


@skip_without_db
class ContractPagesTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cw = contract_world()
        self.world = self.cw.world
        self.project_url = f"/plataforma/proyectos/{self.world.project.pk}/"
        self.base = f"{self.project_url}contratos/"
        self.client.force_login(self.world.admin)

    def test_who_sees_the_tab(self) -> None:
        self.assertContains(self.client.get(self.project_url), ">Contratos</a>")
        self.client.force_login(self.world.supervisor)  # Revisor de Costos: lee
        self.assertEqual(self.client.get(self.base).status_code, 200)
        plain = make_user(self.world.org, username="sin-costos", role=Role.SUPERVISOR)
        self.client.force_login(plain)  # sin la sección Costos
        self.assertNotContains(self.client.get(self.project_url), ">Contratos</a>")
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.base).status_code, 404)
        other = contract_world("Otra constructora")
        self.client.force_login(other.world.admin)
        self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_list_filters_and_alerts(self) -> None:
        make_contract(self.cw, ends_on=date(2026, 4, 30))
        make_contract(self.cw, number="DT-001", kind="piecework", status=ContractStatus.DRAFT,
                      lines=(("2.4", "40", "3000.00", ()),), charges=[], deductions=[])  # fmt: skip
        response = self.client.get(self.base)
        self.assertContains(response, 'aria-current="page">Contratos</a>')
        self.assertContains(response, "SC-001")
        self.assertContains(response, "DT-001")
        self.assertContains(response, "8.224.600,00")
        self.assertContains(response, "venció el 30/04/2026")
        drafts = self.client.get(self.base + "?estado=draft")
        self.assertNotContains(drafts, ">SC-001</a></td>")  # la fila; el aviso sí se ve
        self.assertContains(drafts, '<option value="draft" selected>')

    def test_viewer_reads_but_does_not_create(self) -> None:
        self.client.force_login(self.cw.viewer)
        self.assertEqual(self.client.get(self.base).status_code, 200)
        self.assertEqual(self.client.get(self.base + "nuevo/").status_code, 403)
        self.assertEqual(self.client.post(self.base + "nuevo/", contract_post()).status_code, 403)

    def test_new_form_starts_from_the_templates(self) -> None:
        page = self.client.get(self.base + "nuevo/")
        self.assertContains(page, 'value="SC-001"')
        self.assertContains(page, 'value="Administración"')
        self.assertContains(page, 'value="Retención en la fuente"')
        self.assertContains(page, '<datalist id="ctr-items">')
        self.assertContains(page, '<option value="2.1 Concreto zapatas">')
        piecework = self.client.get(self.base + "nuevo/?tipo=piecework")
        self.assertContains(piecework, 'value="DT-001"')
        self.assertContains(piecework, 'name="cargos-TOTAL_FORMS" value="0"')
        self.assertContains(piecework, 'name="descuentos-TOTAL_FORMS" value="0"')

    def test_create_a_contract(self) -> None:
        response = self.client.post(self.base + "nuevo/", contract_post())
        contract = Contract.objects.get()
        self.assertRedirects(response, f"{self.base}{contract.pk}/")
        self.assertEqual(str(contract.current_value), "8224600.00")
        self.assertEqual(
            list(contract_line(contract, "2.1").tasks.values_list("outline_number", flat=True)),
            ["2.1"],
        )
        detail = self.client.get(f"{self.base}{contract.pk}/")
        self.assertContains(detail, "Estructuras del Norte SAS")
        self.assertContains(detail, "8.224.600,00")

    def test_add_rows_does_not_save(self) -> None:
        response = self.client.post(self.base + "nuevo/", contract_post(accion="lineas"))
        self.assertContains(response, 'name="lineas-TOTAL_FORMS" value="5"')
        self.assertFalse(Contract.objects.exists())

    def test_errors_of_the_rows(self) -> None:
        response = self.client.post(
            self.base + "nuevo/",
            contract_post(**{"lineas-0-tasks": "9.9", "lineas-1-item": "Nada que ver"}),
        )
        self.assertContains(response, "no hay tareas vigentes con número 9.9")
        self.assertContains(response, "«Nada que ver» no es un ítem")
        self.assertFalse(Contract.objects.exists())

    def test_an_overlap_asks_for_confirmation(self) -> None:
        make_contract(self.cw, number="SC-009", lines=(("2.2", "5", "1.00", ()),))
        response = self.client.post(self.base + "nuevo/", contract_post())
        self.assertContains(response, "el contrato SC-009 ya lo cubre")
        self.assertContains(response, 'name="confirm_overlap"')
        self.assertEqual(Contract.objects.count(), 1)
        self.client.post(self.base + "nuevo/", contract_post(confirm_overlap="on"))
        self.assertEqual(Contract.objects.count(), 2)

    def test_detail_tabs(self) -> None:
        contract = make_contract(self.cw)
        make_certificate(self.cw, contract, quantities={"2.1": "4"})
        url = f"{self.base}{contract.pk}/"
        self.assertContains(self.client.get(url), "Anticipo por amortizar")
        lines = self.client.get(url + "?pestana=lineas")
        self.assertContains(lines, "2.1 Concreto zapatas")
        self.assertContains(lines, "2.1 Columnas")
        self.assertContains(self.client.get(url + "?pestana=actas"), "Acta 1")
        self.assertContains(self.client.get(url + "?pestana=otrosies"), "Nuevo otrosí")
        self.assertEqual(self.client.get(url + "?pestana=otra").status_code, 200)

    def test_approve_reject_and_void_from_the_sheet(self) -> None:
        self.client.post(self.base + "nuevo/", contract_post())
        contract = Contract.objects.get()
        url = f"{self.base}{contract.pk}/"
        own = self.client.post(url + "aprobar/", follow=True)
        self.assertContains(own, "otra persona")
        self.client.force_login(self.world.supervisor)
        self.client.post(url + "rechazar/", {"motivo": "Falta la póliza"})
        contract.refresh_from_db()
        self.assertEqual(contract.rejection_note, "Falta la póliza")
        self.client.force_login(self.world.admin)
        self.client.post(url + "editar/", contract_post())
        self.client.force_login(self.world.supervisor)
        self.client.post(url + "aprobar/")
        contract.refresh_from_db()
        self.assertEqual(contract.status, ContractStatus.APPROVED)
        self.assertEqual(self.client.post(url + "anular/", {"motivo": "x"}).status_code, 403)
        self.client.force_login(self.world.admin)
        self.client.post(url + "anular/", {"motivo": "El contratista desistió"})
        contract.refresh_from_db()
        self.assertEqual(contract.status, ContractStatus.VOIDED)

    def test_contributor_edits_only_its_drafts(self) -> None:
        other = make_contract(self.cw, status=ContractStatus.DRAFT)
        self.client.force_login(self.cw.contributor)
        self.assertEqual(self.client.get(f"{self.base}{other.pk}/editar/").status_code, 403)
        self.client.post(self.base + "nuevo/", contract_post(number="SC-002",
                                                             confirm_overlap="on"))  # fmt: skip
        own = Contract.objects.get(number="SC-002")
        self.assertEqual(self.client.get(f"{self.base}{own.pk}/editar/").status_code, 200)

    def test_amendment_create_and_approve(self) -> None:
        contract = make_contract(self.cw)
        concrete = contract_line(contract, "2.1")
        url = f"{self.base}{contract.pk}/otrosies/nuevo/"
        self.assertContains(self.client.get(url), f'name="aumento_{concrete.pk}"')
        response = self.client.post(
            url,
            {
                "accion": "guardar",
                "signed_on": "2026-05-02",
                "reason": "Mayor cantidad de concreto",
                "new_ends_on": "2026-10-31",
                f"aumento_{concrete.pk}": "2",
                "lineas-TOTAL_FORMS": "1",
                "lineas-INITIAL_FORMS": "0",
                "lineas-0-item": "2.4 Curado",
                "lineas-0-tasks": "",
                "lineas-0-unit": "m2",
                "lineas-0-quantity": "40",
                "lineas-0-unit_price": "3000",
            },
        )
        amendment = ContractAmendment.objects.get()
        self.assertRedirects(response, f"{self.base}{contract.pk}/?pestana=otrosies")
        self.client.force_login(self.world.supervisor)
        self.client.post(f"{self.base}{contract.pk}/otrosies/{amendment.pk}/aprobar/")
        amendment.refresh_from_db()
        contract.refresh_from_db()
        self.assertEqual(amendment.status, AmendmentStatus.APPROVED)
        self.assertEqual(str(contract.current_value), "9337340.00")
