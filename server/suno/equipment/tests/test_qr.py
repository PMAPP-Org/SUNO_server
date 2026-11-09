"""Identificación QR de equipos: payload, imagen y resolución con alcance."""

from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from uuid import UUID, uuid4

from django.test import SimpleTestCase, TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import Principal, resolve_request_context
from suno.core.models import Project, Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.qr import (
    PAYLOAD_PREFIX,
    equipment_identifier,
    equipment_payload,
    parse_identifier,
    render_png,
    render_svg,
)
from suno.equipment.services import (
    assign_operator,
    create_equipment,
    create_equipment_type,
    link_project,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


class QrPayloadTests(SimpleTestCase):
    def test_identifier_is_prefix_plus_uuid(self) -> None:
        pk = uuid4()
        self.assertEqual(equipment_identifier(pk), f"{PAYLOAD_PREFIX}{pk}")

    def test_payload_carries_registration_data_but_no_free_text(self) -> None:
        equipment = SimpleNamespace(
            pk=uuid4(),
            code="EQ-001",
            name="Excavadora\nprincipal",
            equipment_type=SimpleNamespace(name="Excavadora"),
            manufacturer="CAT",
            model="320",
            serial_number="",
            license_plate="ABC123",
            manufacture_year=2020,
            owner_name="Contratista SAS",
            notes="no debe salir",
            specifications={"secreto": 1},
            get_ownership_display=lambda: "Alquilado",
        )
        payload = equipment_payload(equipment)  # type: ignore[arg-type]
        lines = payload.split("\n")
        self.assertEqual(lines[0], f"suno:eq:{equipment.pk}")
        self.assertIn("Código: EQ-001", lines)
        self.assertIn("Nombre: Excavadora principal", lines)  # sin saltos de línea inyectados
        self.assertIn("Tipo: Excavadora", lines)
        self.assertIn("Placa: ABC123", lines)
        self.assertIn("Año: 2020", lines)
        self.assertIn("Propiedad: Alquilado", lines)
        self.assertIn("Propietario: Contratista SAS", lines)
        self.assertFalse(any(line.startswith("Serie") for line in lines))  # vacío se omite
        self.assertNotIn("no debe salir", payload)
        self.assertNotIn("secreto", payload)
        self.assertEqual(parse_identifier(payload), equipment.pk)

    def test_only_the_first_identifier_counts(self) -> None:
        real, fake = uuid4(), uuid4()
        text = f"suno:eq:{real}\nNombre: suno:eq:{fake}"
        self.assertEqual(parse_identifier(text), real)

    def test_parse_accepts_payload_bare_uuid_and_detail_url(self) -> None:
        pk = uuid4()
        self.assertEqual(parse_identifier(equipment_identifier(pk)), pk)
        self.assertEqual(parse_identifier(f"  SUNO:EQ:{pk}  "), pk)
        self.assertEqual(parse_identifier(str(pk)), pk)
        self.assertEqual(parse_identifier(f"https://x.test/plataforma/equipos/{pk}/"), pk)

    def test_parse_rejects_everything_else(self) -> None:
        for raw in ("", "EQ-01", "suno:eq:no-es-uuid", "https://x.test/otra/ruta/", "a" * 600):
            with self.subTest(raw=raw[:20]):
                self.assertIsNone(parse_identifier(raw))

    def test_svg_and_png_are_valid_images_with_platform_colors(self) -> None:
        payload = equipment_identifier(uuid4())
        svg = render_svg(payload, title="QR de EQ-01")
        self.assertTrue(svg.startswith(b"<svg"))
        self.assertIn(b"#171717", svg)
        self.assertNotIn(b"<script", svg)
        self.assertTrue(render_png(payload).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_same_payload_gives_same_image(self) -> None:
        payload = equipment_identifier(UUID(int=7))
        self.assertEqual(render_svg(payload), render_svg(payload))


@skip_without_db
class QrWebTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("QR Web")
        self.admin = make_user(self.org, username="gestor-qr", role=Role.ADMIN)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        equipment_type = create_equipment_type(self.ctx, code="VOL", name="Volqueta")
        self.equipment = create_equipment(
            self.ctx,
            equipment_type_id=equipment_type.pk,
            code="VQ-01",
            name="Volqueta 1",
            license_plate="ABC123",
        )
        self.project = Project.objects.create(org=self.org, name="Obra A", created_by=self.admin)
        link_project(
            self.ctx,
            equipment_id=self.equipment.pk,
            project_id=self.project.pk,
            started_on=date(2026, 11, 1),
        )
        self.base = "/plataforma/equipos/"

    def grant_operator(self, username: str):
        operator = make_user(self.org, username=username, role=Role.FIELD_AGENT)
        set_section_grant(
            self.ctx,
            user_id=operator.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.EQUIPMENT_OPERATOR,
            scope=AccessScope.PROJECTS,
            project_ids=[self.project.pk],
        )
        return operator

    def test_label_page_embeds_qr_and_downloads_work(self) -> None:
        self.client.force_login(self.admin)
        page = self.client.get(f"{self.base}{self.equipment.pk}/qr/")
        self.assertContains(page, "data:image/svg+xml;base64,")
        self.assertContains(page, f'alt="Código QR de {self.equipment.code}"')
        self.assertContains(page, "VQ-01")
        svg = self.client.get(f"{self.base}{self.equipment.pk}/qr.svg?download=1")
        self.assertEqual(svg["Content-Type"], "image/svg+xml")
        self.assertIn("qr-vq-01.svg", svg["Content-Disposition"])
        png = self.client.get(f"{self.base}{self.equipment.pk}/qr.png")
        self.assertEqual(png["Content-Type"], "image/png")
        self.assertNotIn("Content-Disposition", png)
        self.assertEqual(self.client.get(f"{self.base}{self.equipment.pk}/qr.gif").status_code, 404)

    def test_identify_resolves_scan_code_and_plate(self) -> None:
        self.client.force_login(self.admin)
        detail = f"{self.base}{self.equipment.pk}/"
        for raw in (
            equipment_payload(self.equipment),
            equipment_identifier(self.equipment.pk),
            "vq-01",
            "abc123",
        ):
            with self.subTest(raw=raw):
                response = self.client.get(f"{self.base}identificar/", {"q": raw})
                self.assertRedirects(response, detail)

    def test_identify_unknown_code_does_not_leak_existence(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(
            f"{self.base}identificar/", {"q": equipment_identifier(uuid4())}, follow=True
        )
        self.assertContains(response, "no corresponde a un equipo al que tengas acceso")

    def test_partial_text_falls_back_to_catalog_search(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(f"{self.base}identificar/", {"q": "VQ"})
        self.assertRedirects(response, f"{self.base}?q=VQ", fetch_redirect_response=False)

    def test_other_org_and_unassigned_operator_cannot_resolve_or_render(self) -> None:
        other_org = make_org("Otra org")
        outsider = make_user(other_org, username="externo", role=Role.ADMIN)
        operator = self.grant_operator("operador-qr")  # sin asignación vigente
        for user in (outsider, operator):
            self.client.force_login(user)
            pk = self.equipment.pk
            self.assertEqual(self.client.get(f"{self.base}{pk}/qr/").status_code, 404)
            self.assertEqual(self.client.get(f"{self.base}{pk}/qr.svg").status_code, 404)
            response = self.client.get(
                f"{self.base}identificar/", {"q": equipment_identifier(self.equipment.pk)}
            )
            self.assertRedirects(response, self.base, fetch_redirect_response=False)
            labels = self.client.get(f"{self.base}etiquetas/", {"ids": str(pk)})
            self.assertNotContains(labels, "VQ-01")

    def test_assigned_operator_can_open_own_label(self) -> None:
        operator = self.grant_operator("operador-ok")
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=operator.pk,
            project_id=self.project.pk,
            valid_from=date(2026, 11, 1),
        )
        self.client.force_login(operator)
        self.assertContains(self.client.get(f"{self.base}{self.equipment.pk}/qr/"), "VQ-01")

    def test_batch_labels_ignore_garbage_ids(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(
            f"{self.base}etiquetas/",
            {"ids": [str(self.equipment.pk), "basura", str(uuid4())]},
        )
        self.assertContains(response, "VQ-01")
        self.assertEqual(response.content.decode().count('class="eq-label"'), 1)

    def test_anonymous_is_redirected_to_login(self) -> None:
        self.assertEqual(self.client.get(f"{self.base}{self.equipment.pk}/qr/").status_code, 302)


@skip_without_db
class PeekPanelTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Peek Web")
        self.admin = make_user(self.org, username="gestor-peek", role=Role.ADMIN)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        equipment_type = create_equipment_type(self.ctx, code="GEN", name="Generador")
        self.equipment = create_equipment(
            self.ctx, equipment_type_id=equipment_type.pk, code="GN-01", name="Generador 1"
        )
        self.client.force_login(self.admin)

    def test_catalog_links_open_in_the_floating_panel(self) -> None:
        page = self.client.get("/plataforma/equipos/")
        self.assertContains(page, "peek.js")
        self.assertContains(page, "peek-frame.js")
        self.assertContains(
            page, f'data-peek class="eq-name" href="/plataforma/equipos/{self.equipment.pk}/"'
        )
        self.assertContains(page, 'data-peek class="eq-button eq-button-primary"')

    def test_equipment_pages_can_be_framed_by_same_origin_only(self) -> None:
        response = self.client.get(f"/plataforma/equipos/{self.equipment.pk}/")
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")
        other = self.client.get("/plataforma/")
        self.assertEqual(other["X-Frame-Options"], "DENY")
