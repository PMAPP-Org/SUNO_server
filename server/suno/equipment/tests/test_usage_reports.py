"""Reporte de uso desde campo: servicio, API móvil y visibilidad en la ficha."""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, date, time, timedelta
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import TestCase

from suno.authn.tests.factories import login, make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    AuditEvent,
    EquipmentMeterReading,
    EquipmentUsage,
    EquipmentUsageStatus,
    Project,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.datasheet import record_meter_reading
from suno.equipment.services import (
    assign_operator,
    create_equipment,
    create_equipment_type,
    link_project,
    set_equipment_rate,
    update_equipment,
)
from suno.equipment.usage_reports import recent_usage, report_equipment_usage, usage_projects

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

YESTERDAY = date.today() - timedelta(days=1)


def ctx_of(user, org):
    return resolve_request_context(Principal(user_id=user.pk, org_id=org.pk))


class _World(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Uso")
        self.admin = make_user(self.org, username="gestor", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador", role=Role.FIELD_AGENT)
        self.other_operator = make_user(self.org, username="otro-op", role=Role.FIELD_AGENT)
        self.ctx = ctx_of(self.admin, self.org)
        self.north = Project.objects.create(org=self.org, name="Norte", created_by=self.admin)
        self.south = Project.objects.create(org=self.org, name="Sur", created_by=self.admin)
        etype = create_equipment_type(self.ctx, code="EXC", name="Excavadora")
        self.equipment = create_equipment(
            self.ctx,
            equipment_type_id=etype.pk,
            code="EQ-1",
            name="Excavadora 1",
            primary_meter_unit="hours",
        )
        for project in (self.north,):
            link_project(
                self.ctx,
                equipment_id=self.equipment.pk,
                project_id=project.pk,
                started_on=date.today() - timedelta(days=30),
            )
        set_section_grant(
            self.ctx,
            user_id=self.operator.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.EQUIPMENT_OPERATOR,
            scope=AccessScope.PROJECTS,
            project_ids=[self.north.pk, self.south.pk],
        )
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=self.north.pk,
            valid_from=date.today() - timedelta(days=20),
        )
        self.operator_ctx = ctx_of(self.operator, self.org)
        set_equipment_rate(
            self.ctx,
            equipment_id=self.equipment.pk,
            valid_from=date.today() - timedelta(days=60),
            operation_rate=Decimal("100000"),
        )
        record_meter_reading(
            self.ctx,
            equipment_id=self.equipment.pk,
            meter_unit="hours",
            value=Decimal("500"),
            read_at=_aware(YESTERDAY - timedelta(days=3), 8),
        )


def _aware(day: date, hour: int):
    from datetime import datetime

    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@skip_without_db
class UsageReportServiceTests(_World):
    def report(self, ctx=None, **overrides: Any):
        values = {
            "usage_id": uuid.uuid4(),
            "equipment_id": self.equipment.pk,
            "project_id": self.north.pk,
            "usage_date": YESTERDAY,
            "start_time": time(8),
            "end_time": time(12),
            "activity": "Excavación de zanja",
            "location": "Frente 2",
            "start_meter": Decimal("500"),
            "end_meter": Decimal("504"),
        }
        values.update(overrides)
        return report_equipment_usage(ctx or self.operator_ctx, **values)

    def test_operator_reports_a_completed_usage_with_hours_and_cost(self) -> None:
        usage = self.report()
        self.assertEqual(usage.status, EquipmentUsageStatus.COMPLETED)
        self.assertEqual(
            (usage.operator_id, usage.created_by_id), (self.operator.pk, self.operator.pk)
        )
        self.assertEqual(
            (usage.billable_hours, usage.cost), (Decimal("4.00"), Decimal("400000.00"))
        )
        self.assertEqual(
            (usage.start_meter, usage.end_meter, usage.location),
            (Decimal("500"), Decimal("504"), "Frente 2"),
        )
        self.assertTrue(AuditEvent.objects.filter(action="equipment.usage_created").exists())

    def test_the_final_meter_becomes_the_last_known_reading(self) -> None:
        self.report()
        latest = EquipmentMeterReading.objects.filter(source="usage").get()
        self.assertEqual(latest.value, Decimal("504.00"))

    def test_retry_with_the_same_id_does_not_duplicate_usage_or_reading(self) -> None:
        usage_id = uuid.uuid4()
        first = self.report(usage_id=usage_id)
        again = self.report(usage_id=usage_id)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(EquipmentUsage.objects.count(), 1)
        self.assertEqual(EquipmentMeterReading.objects.filter(source="usage").count(), 1)

    def test_same_id_from_another_operator_is_refused(self) -> None:
        usage_id = uuid.uuid4()
        self.report(usage_id=usage_id)
        with self.assertRaises((ValidationError, Http404)):
            self.report(ctx_of(self.other_operator, self.org), usage_id=usage_id)

    def test_only_an_assigned_operator_with_the_operator_role_can_report(self) -> None:
        set_section_grant(
            self.ctx,
            user_id=self.other_operator.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.EQUIPMENT_OPERATOR,
            scope=AccessScope.PROJECTS,
            project_ids=[self.north.pk],
        )
        # Un operador sin asignación ni siquiera ve el equipo.
        with self.assertRaises(Http404):
            self.report(ctx_of(self.other_operator, self.org))
        with self.assertRaises(ValidationError):
            self.report(self.ctx)

    def test_project_must_be_linked_to_the_equipment(self) -> None:
        with self.assertRaisesMessage(ValidationError, "no está vinculado"):
            self.report(project_id=self.south.pk)

    def test_overlapping_usage_is_rejected_but_a_split_day_is_not(self) -> None:
        self.report()
        with self.assertRaisesMessage(ValidationError, "ya tiene otro uso"):
            self.report(
                start_time=time(11),
                end_time=time(13),
                start_meter=Decimal("504"),
                end_meter=Decimal("506"),
            )
        afternoon = self.report(
            start_time=time(13),
            end_time=time(17),
            start_meter=Decimal("504"),
            end_meter=Decimal("508"),
        )
        self.assertEqual(afternoon.billable_hours, Decimal("4.00"))
        self.assertEqual(EquipmentUsage.objects.count(), 2)

    def test_validation_rules_and_nothing_is_saved_on_failure(self) -> None:
        with self.assertRaisesMessage(ValidationError, "futuro"):
            self.report(usage_date=date.today() + timedelta(days=1))
        with self.assertRaisesMessage(ValidationError, "posterior"):
            self.report(start_time=time(12), end_time=time(8))
        with self.assertRaisesMessage(ValidationError, "actividad"):
            self.report(activity="  ")
        with self.assertRaisesMessage(ValidationError, "obligatorias"):
            self.report(start_meter=None, end_meter=None)
        with self.assertRaisesMessage(ValidationError, "menor que la inicial"):
            self.report(start_meter=Decimal("504"), end_meter=Decimal("500"))
        with self.assertRaisesMessage(ValidationError, "no retroceden"):
            self.report(start_meter=Decimal("100"), end_meter=Decimal("120"))
        update_equipment(self.ctx, self.equipment.pk, status="retired")
        with self.assertRaisesMessage(ValidationError, "retirado"):
            self.report()
        self.assertEqual(EquipmentUsage.objects.count(), 0)
        self.assertEqual(EquipmentMeterReading.objects.filter(source="usage").count(), 0)

    def test_equipment_controlled_by_date_does_not_need_meters(self) -> None:
        update_equipment(self.ctx, self.equipment.pk, control_scheme="date", primary_meter_unit="")
        usage = self.report(start_meter=None, end_meter=None)
        self.assertIsNone(usage.end_meter)
        self.assertEqual(EquipmentMeterReading.objects.filter(source="usage").count(), 0)

    def test_without_a_rate_the_usage_is_unpriced(self) -> None:
        from suno.core.models import EquipmentRate

        EquipmentRate.objects.all().delete()
        usage = self.report()
        self.assertIsNone(usage.cost)
        self.assertEqual(usage.billable_hours, Decimal("4.00"))

    def test_projects_offered_are_the_assigned_and_linked_ones(self) -> None:
        self.assertEqual(
            [
                p.name
                for p in usage_projects(self.operator_ctx, [self.equipment.pk])[self.equipment.pk]
            ],
            ["Norte"],
        )
        self.assertEqual(usage_projects(ctx_of(self.admin, self.org), [self.equipment.pk]), {})

    def test_recent_usage_is_own_capped_and_without_cancelled(self) -> None:
        usage = self.report()
        rows = recent_usage(self.operator_ctx, [self.equipment.pk])
        self.assertEqual([u.pk for u in rows[self.equipment.pk]], [usage.pk])
        self.assertEqual(recent_usage(ctx_of(self.admin, self.org), [self.equipment.pk]), {})


@skip_without_db
class UsageReportApiTests(_World):
    def auth(self, username: str) -> dict[str, str]:
        token = login(self.client, username=username).json()["tokens"]
        return {"HTTP_AUTHORIZATION": f"Bearer {token['access_token']}"}

    def body(self, **overrides: Any) -> dict[str, Any]:
        data = {
            "id": str(uuid.uuid4()),
            "project_id": str(self.north.pk),
            "usage_date": YESTERDAY.isoformat(),
            "start_time": "08:00:00",
            "end_time": "12:00:00",
            "activity": "Excavación de zanja",
            "location": "Frente 2",
            "start_meter": "500",
            "end_meter": "504",
        }
        data.update(overrides)
        return data

    def post(self, username: str, body: dict[str, Any], equipment_id: Any = None):
        return self.client.post(
            f"/v1/equipment/{equipment_id or self.equipment.pk}/usage-reports",
            body,
            content_type="application/json",
            **self.auth(username),
        )

    def test_operator_reports_and_the_ficha_shows_projects_and_recent_usage(self) -> None:
        item = self.client.get("/v1/equipment", **self.auth("operador")).json()["items"][0]
        self.assertEqual(item["usage_projects"], [{"id": str(self.north.pk), "name": "Norte"}])
        self.assertEqual(item["recent_usage"], [])

        payload = self.body()
        response = self.post("operador", payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json(), {"id": payload["id"], "hours": 4.0, "status": "completed"}
        )

        item = self.client.get("/v1/equipment", **self.auth("operador")).json()["items"][0]
        (row,) = item["recent_usage"]
        self.assertEqual(
            (
                row["usage_date"],
                row["start_time"],
                row["end_time"],
                row["hours"],
                row["project_name"],
                row["status"],
            ),
            (YESTERDAY.isoformat(), "08:00", "12:00", 4.0, "Norte", "completed"),
        )
        self.assertNotIn("cost", row)
        self.assertEqual(item["meters"][0]["value"], 504.0)

    def test_retry_is_idempotent(self) -> None:
        payload = self.body()
        self.assertEqual(self.post("operador", payload).status_code, 200)
        self.assertEqual(self.post("operador", payload).status_code, 200)
        self.assertEqual(EquipmentUsage.objects.count(), 1)

    def test_violations_are_unprocessable_and_save_nothing(self) -> None:
        overlap_first = self.body()
        self.assertEqual(self.post("operador", overlap_first).status_code, 200)
        clash = self.post(
            "operador",
            self.body(
                start_time="10:00:00", end_time="13:00:00", start_meter="504", end_meter="506"
            ),
        )
        self.assertEqual(clash.status_code, 422)
        self.assertIn("otro uso", clash.json()["message"])
        no_meters = self.post(
            "operador",
            self.body(start_meter=None, end_meter=None, start_time="14:00:00", end_time="15:00:00"),
        )
        self.assertEqual(no_meters.status_code, 422)
        self.assertEqual(EquipmentUsage.objects.count(), 1)

    def test_schema_rejects_bad_values(self) -> None:
        for bad in (
            {"activity": ""},
            {"start_meter": "-1"},
            {"usage_date": "ayer"},
            {"end_time": "no"},
        ):
            self.assertEqual(self.post("operador", self.body(**bad)).status_code, 422, bad)

    def test_permissions_and_visibility(self) -> None:
        outsider = make_user(self.org, username="externo", role=Role.VIEWER)
        self.assertEqual(self.post(outsider.username, self.body()).status_code, 404)
        self.assertEqual(self.post("operador", self.body(), uuid.uuid4()).status_code, 404)
        anonymous = self.client.post(
            f"/v1/equipment/{self.equipment.pk}/usage-reports",
            self.body(),
            content_type="application/json",
        )
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(self.post("gestor", self.body()).status_code, 422)
