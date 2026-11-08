"""Flujos web del catálogo y la ficha técnica de equipos."""

from __future__ import annotations

import unittest
from datetime import date

from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.access import AccessScope, Section, SectionRole
from visitacampo.core.access_services import revoke_section_grant, set_section_grant
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import Equipment, EquipmentCollection, EquipmentUsage, Project, Role
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.equipment.services import (
    assign_operator,
    create_equipment,
    create_equipment_type,
    link_project,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class EquipmentWebTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Equipos Web")
        self.admin = make_user(self.org, username="gestor-web", role=Role.ADMIN)
        self.viewer = make_user(self.org, username="consulta-web", role=Role.VIEWER)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.equipment_type = create_equipment_type(
            self.ctx,
            code="RET",
            name="Retroexcavadora",
        )
        self.equipment = create_equipment(
            self.ctx,
            equipment_type_id=self.equipment_type.pk,
            code="EQ-WEB-01",
            name="Retroexcavadora principal",
            manufacturer="CAT",
        )
        self.project = Project.objects.create(
            org=self.org,
            name="Corredor Norte",
            created_by=self.admin,
        )
        link_project(
            self.ctx,
            equipment_id=self.equipment.pk,
            project_id=self.project.pk,
            started_on=date(2026, 11, 1),
        )

    def prepare_operator(self):
        operator = make_user(
            self.org,
            username="operador-uso",
            role=Role.FIELD_AGENT,
        )
        set_section_grant(
            self.ctx,
            user_id=operator.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.EQUIPMENT_OPERATOR,
            scope=AccessScope.PROJECTS,
            project_ids=[self.project.pk],
        )
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=operator.pk,
            project_id=self.project.pk,
            valid_from=date(2026, 11, 1),
        )
        return operator

    def test_manager_sees_catalog_and_full_technical_record(self) -> None:
        self.client.force_login(self.admin)

        catalog = self.client.get("/plataforma/equipos/")
        detail = self.client.get(f"/plataforma/equipos/{self.equipment.pk}/")

        self.assertEqual(catalog.status_code, 200)
        self.assertContains(catalog, "Retroexcavadora principal")
        self.assertContains(catalog, "Corredor Norte")
        self.assertContains(catalog, "Registrar equipo")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Ficha técnica")
        self.assertContains(detail, "Editar ficha")
        self.assertContains(detail, "Identificación móvil")

    def test_manager_creates_and_opens_collection_page(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(
            "/plataforma/equipos/colecciones/nueva/",
            {
                "name": "Maquinaria amarilla",
                "description": "Equipos para movimiento de tierra.",
                "equipment_types": [str(self.equipment_type.pk)],
            },
        )

        collection = EquipmentCollection.objects.get(org=self.org, name="Maquinaria amarilla")
        self.assertRedirects(
            response,
            f"/plataforma/equipos/colecciones/{collection.pk}/",
        )
        catalog = self.client.get("/plataforma/equipos/")
        self.assertContains(catalog, "Colecciones")
        self.assertContains(catalog, "Maquinaria amarilla")
        detail = self.client.get(f"/plataforma/equipos/colecciones/{collection.pk}/")
        self.assertContains(detail, "Retroexcavadora principal")
        self.assertContains(detail, "Retroexcavadora")
        self.assertContains(detail, "Editar colección")

    def test_catalog_filters_by_query_status_type_and_project(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(
            "/plataforma/equipos/",
            {
                "q": "EQ-WEB",
                "status": "available",
                "type": str(self.equipment_type.pk),
                "project": str(self.project.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Retroexcavadora principal")

    def test_manager_registers_equipment_from_web_form(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(
            "/plataforma/equipos/nuevo/",
            {
                "equipment_type": str(self.equipment_type.pk),
                "code": "EQ-WEB-02",
                "name": "Cargador frontal",
                "ownership": "own",
                "status": "available",
                "specifications": "{}",
            },
        )

        created = Equipment.objects.get(code="EQ-WEB-02", org=self.org)
        self.assertRedirects(
            response,
            f"/plataforma/equipos/{created.pk}/",
        )

    def test_viewer_can_read_but_cannot_mutate(self) -> None:
        set_section_grant(
            self.ctx,
            user_id=self.viewer.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.VIEWER,
            scope=AccessScope.ORGANIZATION,
        )
        self.client.force_login(self.viewer)

        detail = self.client.get(f"/plataforma/equipos/{self.equipment.pk}/")

        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "Editar ficha")
        self.assertEqual(self.client.get("/plataforma/equipos/nuevo/").status_code, 403)
        self.assertEqual(
            self.client.get("/plataforma/equipos/colecciones/nueva/").status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                f"/plataforma/equipos/{self.equipment.pk}/documentos/nuevo/"
            ).status_code,
            403,
        )

    def test_revoked_section_disappears_and_url_is_forbidden(self) -> None:
        set_section_grant(
            self.ctx,
            user_id=self.viewer.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.VIEWER,
            scope=AccessScope.ORGANIZATION,
        )
        revoke_section_grant(
            self.ctx,
            user_id=self.viewer.pk,
            section=Section.EQUIPMENT,
        )
        self.client.force_login(self.viewer)

        home = self.client.get("/plataforma/")
        catalog = self.client.get("/plataforma/equipos/")

        self.assertNotContains(home, 'href="/plataforma/equipos/"')
        self.assertEqual(catalog.status_code, 403)

    def test_other_organization_equipment_is_not_visible(self) -> None:
        other_org = make_org("Equipos Externos")
        other_admin = make_user(other_org, username="gestor-externo", role=Role.ADMIN)
        other_ctx = resolve_request_context(Principal(user_id=other_admin.pk, org_id=other_org.pk))
        other_type = create_equipment_type(other_ctx, code="GRU", name="Grúa")
        other_equipment = create_equipment(
            other_ctx,
            equipment_type_id=other_type.pk,
            code="EXT-01",
            name="Grúa externa",
        )
        self.client.force_login(self.admin)

        response = self.client.get(f"/plataforma/equipos/{other_equipment.pk}/")

        self.assertEqual(response.status_code, 404)

    def test_manager_adds_document_from_the_detail_flow(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/plataforma/equipos/{self.equipment.pk}/documentos/nuevo/",
            {
                "document_type": "insurance",
                "name": "Póliza todo riesgo",
                "number": "POL-001",
                "issued_on": "2026-11-01",
                "expires_on": "2027-11-01",
            },
            follow=True,
        )

        self.assertContains(response, "Información agregada")
        documents = self.client.get(f"/plataforma/equipos/{self.equipment.pk}/?tab=documents")
        self.assertContains(documents, "Póliza todo riesgo")

    def test_manager_registers_and_lists_equipment_usage(self) -> None:
        operator = self.prepare_operator()
        self.client.force_login(self.admin)

        response = self.client.post(
            "/plataforma/equipos/usos/nuevo/",
            {
                "usage_date": "2026-11-05",
                "project": str(self.project.pk),
                "equipment": str(self.equipment.pk),
                "start_time": "07:00",
                "end_time": "12:30",
                "operator": str(operator.memberships.get(org=self.org).pk),
                "activity": "Excavación de material común",
                "location": "Frente norte",
                "status": "completed",
                "meter_unit": "hours",
                "start_meter": "120.00",
                "end_meter": "125.50",
                "notes": "Sin novedades",
            },
        )

        self.assertRedirects(response, "/plataforma/equipos/usos/")
        self.assertTrue(
            EquipmentUsage.objects.filter(
                equipment=self.equipment,
                operator=operator,
            ).exists()
        )
        usage_list = self.client.get("/plataforma/equipos/usos/")
        self.assertContains(usage_list, "Excavación de material común")
        self.assertContains(usage_list, "07:00–12:30")

    def test_viewer_cannot_create_equipment_usage(self) -> None:
        set_section_grant(
            self.ctx,
            user_id=self.viewer.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.VIEWER,
            scope=AccessScope.ORGANIZATION,
        )
        self.client.force_login(self.viewer)

        self.assertEqual(
            self.client.get("/plataforma/equipos/usos/nuevo/").status_code,
            403,
        )
