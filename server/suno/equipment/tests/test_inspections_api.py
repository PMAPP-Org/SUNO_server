"""`POST /v1/equipment/{id}/inspections` y la lista de chequeo dentro de `GET /v1/equipment`."""

from __future__ import annotations

import unittest
import uuid
from datetime import date, timedelta
from typing import Any

from django.test import TestCase

from suno.authn.tests.factories import login, make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import Principal, resolve_request_context
from suno.core.models import EquipmentFault, EquipmentInspection, Project, Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.inspections import define_checklist_item, review_inspection
from suno.equipment.services import (
    assign_operator,
    create_equipment,
    create_equipment_type,
    link_project,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

LIST_URL = "/v1/equipment"


@skip_without_db
class InspectionApiTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Preop API")
        self.admin = make_user(self.org, username="gestor", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador", role=Role.FIELD_AGENT)
        self.reviewer = make_user(self.org, username="revisor", role=Role.SUPERVISOR)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        project = Project.objects.create(org=self.org, name="Norte", created_by=self.admin)
        etype = create_equipment_type(self.ctx, code="VOL", name="Volqueta")
        self.equipment = create_equipment(
            self.ctx,
            equipment_type_id=etype.pk,
            code="EQ-1",
            name="Volqueta 1",
            primary_meter_unit="hours",
        )
        link_project(
            self.ctx,
            equipment_id=self.equipment.pk,
            project_id=project.pk,
            started_on=date.today() - timedelta(days=3),
        )
        for user, role, scope, ids in (
            (self.operator, SectionRole.EQUIPMENT_OPERATOR, AccessScope.PROJECTS, [project.pk]),
            (self.reviewer, SectionRole.REVIEWER, AccessScope.ORGANIZATION, []),
        ):
            set_section_grant(
                self.ctx,
                user_id=user.pk,
                section=Section.EQUIPMENT,
                role=role,
                scope=scope,
                project_ids=ids,
            )
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=project.pk,
            valid_from=date.today() - timedelta(days=1),
        )
        self.brakes = define_checklist_item(
            self.ctx, equipment_type_id=etype.pk, label="Frenos", critical=True
        )
        self.lights = define_checklist_item(self.ctx, equipment_type_id=etype.pk, label="Luces")

    def auth(self, username: str) -> dict[str, str]:
        token = login(self.client, username=username).json()["tokens"]
        return {"HTTP_AUTHORIZATION": f"Bearer {token['access_token']}"}

    def payload(self, **overrides: Any) -> dict[str, Any]:
        body = {
            "id": str(uuid.uuid4()),
            "captured_at": (date.today() - timedelta(days=0)).isoformat() + "T01:00:00Z",
            "meter_unit": "hours",
            "meter_value": "339",
            "items": [
                {"item_id": str(self.brakes.pk), "ok": True, "note": ""},
                {"item_id": str(self.lights.pk), "ok": True, "note": ""},
            ],
            "notes": "",
        }
        body.update(overrides)
        return body

    def post(self, username: str, body: dict[str, Any], equipment_id: Any = None):
        return self.client.post(
            f"{LIST_URL}/{equipment_id or self.equipment.pk}/inspections",
            body,
            content_type="application/json",
            **self.auth(username),
        )

    def test_equipment_payload_carries_the_checklist_in_order(self) -> None:
        response = self.client.get(LIST_URL, **self.auth("operador"))
        item = response.json()["items"][0]
        self.assertEqual([c["label"] for c in item["checklist"]], ["Frenos", "Luces"])
        self.assertEqual([c["critical"] for c in item["checklist"]], [True, False])
        self.assertEqual(item["recent_inspections"], [])

    def test_operator_submits_and_the_status_comes_back_in_the_list(self) -> None:
        body = self.payload()
        response = self.post("operador", body)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json(),
            {"id": body["id"], "status": "submitted", "has_faults": False},
        )
        recent = self.client.get(LIST_URL, **self.auth("operador")).json()["items"][0][
            "recent_inspections"
        ]
        self.assertEqual([(r["id"], r["status"]) for r in recent], [(body["id"], "submitted")])

        review_inspection(
            resolve_request_context(Principal(user_id=self.reviewer.pk, org_id=self.org.pk)),
            uuid.UUID(body["id"]),
            decision="reject",
            comment="Foto ilegible",
        )
        recent = self.client.get(LIST_URL, **self.auth("operador")).json()["items"][0][
            "recent_inspections"
        ]
        self.assertEqual(
            (recent[0]["status"], recent[0]["review_comment"]), ("rejected", "Foto ilegible")
        )

    def test_retry_with_the_same_id_does_not_duplicate(self) -> None:
        body = self.payload()
        first = self.post("operador", body)
        again = self.post("operador", body)
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertEqual(EquipmentInspection.objects.count(), 1)

    def test_failed_item_reports_faults(self) -> None:
        body = self.payload(
            items=[
                {"item_id": str(self.brakes.pk), "ok": False, "note": "Pedal blando"},
                {"item_id": str(self.lights.pk), "ok": True, "note": ""},
            ]
        )
        response = self.post("operador", body)
        self.assertTrue(response.json()["has_faults"])
        self.assertEqual(EquipmentFault.objects.get().item_label, "Frenos")

    def test_incomplete_list_is_unprocessable_and_saves_nothing(self) -> None:
        body = self.payload(items=[{"item_id": str(self.brakes.pk), "ok": True, "note": ""}])
        response = self.post("operador", body)
        self.assertEqual(response.status_code, 422)
        self.assertIn("Faltan", response.json()["message"])
        self.assertEqual(EquipmentInspection.objects.count(), 0)

    def test_failed_item_without_a_note_is_unprocessable(self) -> None:
        body = self.payload(
            items=[
                {"item_id": str(self.brakes.pk), "ok": False, "note": ""},
                {"item_id": str(self.lights.pk), "ok": True, "note": ""},
            ]
        )
        self.assertEqual(self.post("operador", body).status_code, 422)

    def test_reviewer_cannot_submit_and_unknown_equipment_is_not_found(self) -> None:
        self.assertEqual(self.post("revisor", self.payload()).status_code, 403)
        self.assertEqual(self.post("operador", self.payload(), uuid.uuid4()).status_code, 404)

    def test_negative_meter_is_rejected_by_the_schema(self) -> None:
        self.assertEqual(self.post("operador", self.payload(meter_value="-1")).status_code, 422)

    def test_requires_authentication(self) -> None:
        response = self.client.post(
            f"{LIST_URL}/{self.equipment.pk}/inspections",
            self.payload(),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
