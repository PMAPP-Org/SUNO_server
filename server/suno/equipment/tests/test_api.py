"""`GET /v1/equipment` y `POST /v1/equipment/{id}/meter-readings` (móvil, fase 1)."""

from __future__ import annotations

import unittest
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.test import TestCase

from suno.authn.tests.factories import login, make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import Principal, resolve_request_context
from suno.core.models import EquipmentFieldType, EquipmentMeterReading, Project, Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.datasheet import create_department, define_field, set_field_value
from suno.equipment.services import (
    add_document,
    assign_operator,
    create_equipment,
    create_equipment_type,
    link_project,
    update_equipment,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

LIST_URL = "/v1/equipment"


@skip_without_db
class FieldEquipmentApiTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Campo")
        self.admin = make_user(self.org, username="gestor", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador", role=Role.FIELD_AGENT)
        self.outsider = make_user(self.org, username="externo", role=Role.VIEWER)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.project = Project.objects.create(
            org=self.org, name="Proyecto Norte", created_by=self.admin
        )
        etype = create_equipment_type(self.ctx, code="VOL", name="Volqueta")
        self.department = create_department(self.ctx, cost_center_code="CC-1", name="Maquinaria")
        self.equipment = create_equipment(
            self.ctx,
            equipment_type_id=etype.pk,
            code="EQ-1",
            name="Volqueta 1",
            license_plate="EXG871",
            department=self.department,
            primary_meter_unit="hours",
            secondary_meter_unit="kilometers",
        )
        self.other = create_equipment(
            self.ctx, equipment_type_id=etype.pk, code="EQ-2", name="Volqueta 2"
        )
        link_project(
            self.ctx,
            equipment_id=self.equipment.pk,
            project_id=self.project.pk,
            started_on=date.today() - timedelta(days=5),
        )
        set_section_grant(
            self.ctx,
            user_id=self.operator.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.EQUIPMENT_OPERATOR,
            scope=AccessScope.PROJECTS,
            project_ids=[self.project.pk],
        )
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=self.project.pk,
            valid_from=date.today() - timedelta(days=1),
        )

    def auth(self, username: str) -> dict[str, str]:
        token = login(self.client, username=username).json()["tokens"]
        return {"HTTP_AUTHORIZATION": f"Bearer {token['access_token']}"}

    def get(self, username: str, **params: str) -> dict[str, Any]:
        response = self.client.get(LIST_URL, params, **self.auth(username))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def post_reading(self, username: str, equipment_id: Any, **body: Any):
        payload = {
            "id": str(uuid.uuid4()),
            "meter_unit": "hours",
            "value": "339",
            "read_at": "2026-11-01T08:00:00Z",
            **body,
        }
        return self.client.post(
            f"{LIST_URL}/{equipment_id}/meter-readings",
            payload,
            content_type="application/json",
            **self.auth(username),
        )

    # --- lista ----------------------------------------------------------------

    def test_requires_authentication(self) -> None:
        self.assertEqual(self.client.get(LIST_URL).status_code, 401)

    def test_manager_sees_the_whole_catalog_with_datasheet(self) -> None:
        body = self.get("gestor")
        self.assertEqual([i["code"] for i in body["items"]], ["EQ-1", "EQ-2"])
        item = body["items"][0]
        self.assertEqual(item["qr_identifier"], f"suno:eq:{self.equipment.pk}")
        self.assertEqual(item["department"], {"code": "CC-1", "name": "Maquinaria"})
        self.assertEqual(item["current_project_id"], str(self.project.pk))
        self.assertEqual(item["meter_units"], ["hours", "kilometers"])
        self.assertEqual(item["equipment_type"]["code"], "VOL")

    def test_operator_only_sees_the_assigned_equipment(self) -> None:
        body = self.get("operador")
        self.assertEqual([i["code"] for i in body["items"]], ["EQ-1"])

    def test_user_without_equipment_section_gets_an_empty_list(self) -> None:
        body = self.get("externo")
        self.assertEqual((body["changed"], body["items"]), (True, []))

    def test_retired_equipment_is_not_delivered(self) -> None:
        update_equipment(self.ctx, self.other.pk, status="retired")
        self.assertEqual([i["code"] for i in self.get("gestor")["items"]], ["EQ-1"])

    def test_unchanged_version_answers_without_items(self) -> None:
        version = self.get("gestor")["version"]
        again = self.get("gestor", version=version)
        self.assertEqual((again["changed"], again["items"]), (False, []))

    def test_version_changes_when_the_datasheet_changes(self) -> None:
        version = self.get("gestor")["version"]
        update_equipment(self.ctx, self.equipment.pk, name="Volqueta 1 bis")
        self.assertNotEqual(self.get("gestor")["version"], version)

    def test_alerts_meters_and_fields_are_delivered(self) -> None:
        soat = define_field(self.ctx, name="SOAT", data_type=EquipmentFieldType.DATE, alert_days=30)
        set_field_value(
            self.ctx,
            equipment_id=self.equipment.pk,
            definition_id=soat.pk,
            value=date.today() + timedelta(days=10),
        )
        add_document(
            self.ctx,
            equipment_id=self.equipment.pk,
            document_type="insurance",
            name="Póliza",
            expires_on=date.today() - timedelta(days=1),
        )
        self.assertEqual(self.post_reading("operador", self.equipment.pk).status_code, 200)
        item = self.get("gestor")["items"][0]
        self.assertEqual([a["name"] for a in item["alerts"]], ["Póliza", "SOAT"])
        self.assertEqual(item["alerts"][0]["days_left"], -1)
        self.assertEqual(item["fields"][0]["name"], "SOAT")
        self.assertEqual(item["meters"][0]["value"], 339.0)
        self.assertTrue(item["meters"][0]["is_primary"])
        self.assertEqual(item["documents"][0]["name"], "Póliza")

    # --- lecturas -------------------------------------------------------------

    def test_operator_records_a_reading_and_retry_is_idempotent(self) -> None:
        reading_id = str(uuid.uuid4())
        first = self.post_reading("operador", self.equipment.pk, id=reading_id)
        again = self.post_reading("operador", self.equipment.pk, id=reading_id)
        self.assertEqual((first.status_code, again.status_code), (200, 200), again.content)
        self.assertEqual(first.json(), again.json())
        self.assertEqual(EquipmentMeterReading.objects.filter(equipment=self.equipment).count(), 1)
        self.assertEqual(EquipmentMeterReading.objects.get().value, Decimal("339.00"))

    def test_reading_is_rejected_when_it_goes_backwards(self) -> None:
        self.post_reading("operador", self.equipment.pk, value="400")
        response = self.post_reading(
            "operador", self.equipment.pk, value="100", read_at="2026-11-02T08:00:00Z"
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("no retroceden", response.json()["message"])

    def test_reading_of_unassigned_equipment_is_not_found(self) -> None:
        self.assertEqual(self.post_reading("operador", self.other.pk).status_code, 404)

    def test_user_without_section_cannot_record_readings(self) -> None:
        self.assertEqual(self.post_reading("externo", self.equipment.pk).status_code, 404)

    def test_reading_with_negative_value_is_unprocessable(self) -> None:
        self.assertEqual(
            self.post_reading("operador", self.equipment.pk, value="-1").status_code, 422
        )
