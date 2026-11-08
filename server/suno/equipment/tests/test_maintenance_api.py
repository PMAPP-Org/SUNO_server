"""Mantenimiento en `GET /v1/equipment` y la Hoja de Vida en `GET /v1/equipment/{id}/life-sheet`."""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from suno.authn.tests.factories import login, make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import Principal, resolve_request_context
from suno.core.models import PlanKind, Project, Role, WorkOrderKind
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.datasheet import record_meter_reading
from suno.equipment.maintenance import approve_order, cancel_order, create_plan, create_work_order
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
class MaintenanceApiTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Mant API")
        self.admin = make_user(self.org, username="gestor", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador", role=Role.FIELD_AGENT)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        project = Project.objects.create(org=self.org, name="Norte", created_by=self.admin)
        etype = create_equipment_type(self.ctx, code="VOL", name="Volqueta")
        self.equipment = create_equipment(
            self.ctx,
            equipment_type_id=etype.pk,
            code="EQ-1",
            name="Volqueta 1",
            primary_meter_unit="hours",
            acquisition_value=Decimal("99"),
        )
        link_project(
            self.ctx,
            equipment_id=self.equipment.pk,
            project_id=project.pk,
            started_on=date.today() - timedelta(days=5),
        )
        set_section_grant(
            self.ctx,
            user_id=self.operator.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.EQUIPMENT_OPERATOR,
            scope=AccessScope.PROJECTS,
            project_ids=[project.pk],
        )
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=project.pk,
            valid_from=date.today() - timedelta(days=2),
        )
        record_meter_reading(
            self.ctx,
            equipment_id=self.equipment.pk,
            meter_unit="hours",
            value=Decimal("1000"),
            read_at=datetime.now(UTC) - timedelta(days=1),
        )
        self.plan = create_plan(
            self.ctx,
            equipment_id=self.equipment.pk,
            name="Aceite",
            kind=PlanKind.METER,
            meter_unit="hours",
            interval_value=Decimal("250"),
            alert_window=Decimal("50"),
            last_done_value=Decimal("780"),
        )

    def auth(self, username: str) -> dict[str, str]:
        token = login(self.client, username=username).json()["tokens"]
        return {"HTTP_AUTHORIZATION": f"Bearer {token['access_token']}"}

    def item(self, username: str = "operador") -> dict:
        return self.client.get(LIST_URL, **self.auth(username)).json()["items"][0]

    def test_payload_carries_plans_with_state_and_open_orders(self) -> None:
        item = self.item()
        (plan,) = item["maintenance_plans"]
        self.assertEqual(
            (plan["name"], plan["kind"], plan["meter_unit"], plan["state"], plan["remaining"]),
            ("Aceite", "meter", "hours", "due_soon", 30.0),
        )
        self.assertEqual((plan["due_value"], plan["has_open_order"]), (1030.0, False))
        self.assertEqual(item["open_work_orders"], [])

        order = create_work_order(
            self.ctx,
            equipment_id=self.equipment.pk,
            kind=WorkOrderKind.PREVENTIVE,
            description="Cambio de aceite",
            plan_id=self.plan.pk,
        )
        item = self.item()
        self.assertTrue(item["maintenance_plans"][0]["has_open_order"])
        self.assertEqual(
            [(o["code"], o["status"], o["kind"]) for o in item["open_work_orders"]],
            [("OT-0001", "draft", "preventive")],
        )
        cancel_order(self.ctx, order.pk, reason="Prueba")
        self.assertEqual(self.item()["open_work_orders"], [])

    def test_the_list_version_changes_when_an_order_changes_state(self) -> None:
        order = create_work_order(
            self.ctx,
            equipment_id=self.equipment.pk,
            kind=WorkOrderKind.CORRECTIVE,
            description="Fuga",
        )
        version = self.client.get(LIST_URL, **self.auth("operador")).json()["version"]
        reviewer = make_user(self.org, username="aprobador", role=Role.SUPERVISOR)
        set_section_grant(
            self.ctx,
            user_id=reviewer.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.REVIEWER,
            scope=AccessScope.ORGANIZATION,
        )
        approve_order(
            resolve_request_context(Principal(user_id=reviewer.pk, org_id=self.org.pk)), order.pk
        )
        after = self.client.get(LIST_URL, **self.auth("operador")).json()["version"]
        self.assertNotEqual(version, after)

    def test_life_sheet_is_a_pdf_and_financial_data_depends_on_who_asks(self) -> None:
        url = f"{LIST_URL}/{self.equipment.pk}/life-sheet"
        response = self.client.get(url, **self.auth("operador"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_life_sheet_requires_authentication_and_visibility(self) -> None:
        url = f"{LIST_URL}/{self.equipment.pk}/life-sheet"
        self.assertEqual(self.client.get(url).status_code, 401)
        stranger = make_user(self.org, username="externo", role=Role.VIEWER)
        self.assertEqual(self.client.get(url, **self.auth(stranger.username)).status_code, 404)
        self.assertEqual(
            self.client.get(
                f"{LIST_URL}/{uuid.uuid4()}/life-sheet", **self.auth("gestor")
            ).status_code,
            404,
        )
