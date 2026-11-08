"""Flujos web de mantenimiento y Hoja de Vida en PDF."""

from __future__ import annotations

import io
import unittest
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from pypdf import PdfReader

from suno.authn.tests.factories import make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    AuditEvent,
    EquipmentFault,
    EquipmentStatus,
    MaintenancePlan,
    PlanKind,
    Project,
    Role,
    WorkOrder,
    WorkOrderKind,
    WorkOrderStatus,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.datasheet import record_meter_reading
from suno.equipment.inspections import AnswerIn, define_checklist_item, submit_inspection
from suno.equipment.maintenance import create_plan, create_work_order
from suno.equipment.services import (
    assign_operator,
    create_equipment,
    create_equipment_type,
    link_project,
    update_equipment,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

BASE = "/plataforma/equipos/"
PAST = datetime.now(UTC) - timedelta(days=2)


@skip_without_db
class MaintenanceWebTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Mant Web")
        self.admin = make_user(self.org, username="gestor-mw", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador-mw", role=Role.FIELD_AGENT)
        self.approver = make_user(self.org, username="aprobador-mw", role=Role.SUPERVISOR)
        self.mechanic = make_user(self.org, username="mecanico-mw", role=Role.FIELD_AGENT)
        ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.ctx = ctx
        project = Project.objects.create(org=self.org, name="Norte", created_by=self.admin)
        etype = create_equipment_type(ctx, code="VOL", name="Volqueta")
        self.etype = etype
        self.equipment = create_equipment(
            ctx,
            equipment_type_id=etype.pk,
            code="EQ-MW",
            name="Volqueta MW",
            primary_meter_unit="hours",
            manufacturer="Kenworth",
            acquisition_value=Decimal("250000000"),
        )
        link_project(
            ctx,
            equipment_id=self.equipment.pk,
            project_id=project.pk,
            started_on=date.today() - timedelta(days=10),
        )
        set_section_grant(
            ctx,
            user_id=self.operator.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.EQUIPMENT_OPERATOR,
            scope=AccessScope.PROJECTS,
            project_ids=[project.pk],
        )
        set_section_grant(
            ctx,
            user_id=self.approver.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.REVIEWER,
            scope=AccessScope.ORGANIZATION,
        )
        assign_operator(
            ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=project.pk,
            valid_from=date.today() - timedelta(days=5),
        )
        record_meter_reading(
            ctx,
            equipment_id=self.equipment.pk,
            meter_unit="hours",
            value=Decimal("1000"),
            read_at=PAST,
        )
        self.plan = create_plan(
            ctx,
            equipment_id=self.equipment.pk,
            name="Cambio de aceite",
            kind=PlanKind.METER,
            meter_unit="hours",
            interval_value=Decimal("250"),
            alert_window=Decimal("50"),
            last_done_value=Decimal("780"),
        )

    def test_panel_shows_the_alert_and_the_manager_generates_the_order(self) -> None:
        self.client.force_login(self.admin)
        panel = self.client.get(f"{BASE}mantenimiento/")
        self.assertContains(panel, "Cambio de aceite")
        self.assertContains(panel, "Faltan 30")
        self.assertContains(panel, "Generar orden")

        url = f"{BASE}{self.equipment.pk}/ordenes/nueva/?plan={self.plan.pk}"
        self.assertContains(self.client.get(url), "Mantenimiento preventivo: Cambio de aceite")
        response = self.client.post(url, {"kind": "preventive", "description": "Cambio de aceite"})
        order = WorkOrder.objects.get()
        self.assertRedirects(response, f"{BASE}mantenimiento/ordenes/{order.pk}/")
        self.assertEqual((order.number, order.plan_id), (1, self.plan.pk))
        self.assertContains(self.client.get(f"{BASE}mantenimiento/"), "OT-0001")

    def test_full_cycle_from_the_web(self) -> None:
        order = create_work_order(
            self.ctx,
            equipment_id=self.equipment.pk,
            kind=WorkOrderKind.PREVENTIVE,
            description="Cambio de aceite",
            plan_id=self.plan.pk,
        )
        url = f"{BASE}mantenimiento/ordenes/{order.pk}/"

        self.client.force_login(self.admin)
        self.client.post(
            f"{url}line/",
            {
                "kind": "part",
                "description": "Aceite 15W40",
                "quantity": "4",
                "unit_cost": "85000",
            },
        )
        self.assertContains(self.client.get(url), "Aceite 15W40")
        self.client.post(f"{url}mechanic/", {"mechanic": str(self.mechanic.pk)})
        self.client.post(f"{url}approve/")
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrderStatus.DRAFT, "el creador no aprueba")

        self.client.force_login(self.approver)
        self.client.post(f"{url}approve/")
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrderStatus.APPROVED)

        self.client.force_login(self.admin)
        self.client.post(f"{url}start/")
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.status, EquipmentStatus.MAINTENANCE)
        self.assertContains(self.client.get(url), "Cierre técnico")
        self.client.post(f"{url}close/", {"downtime_hours": "4.5", "notes": "Sin novedad"})
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrderStatus.CLOSED_TECHNICAL)
        self.assertEqual(order.downtime_hours, Decimal("4.50"))
        self.assertNotContains(self.client.get(f"{BASE}mantenimiento/"), "Faltan 30")
        self.assertTrue(AuditEvent.objects.filter(action="work_order.closed").exists())

    def test_close_without_failure_mode_is_reported_not_saved(self) -> None:
        order = create_work_order(
            self.ctx,
            equipment_id=self.equipment.pk,
            kind=WorkOrderKind.CORRECTIVE,
            description="Fuga",
            mechanic_ids=[self.mechanic.pk],
        )
        url = f"{BASE}mantenimiento/ordenes/{order.pk}/"
        self.client.force_login(self.approver)
        self.client.post(f"{url}approve/")
        self.client.force_login(self.admin)
        self.client.post(f"{url}start/")
        response = self.client.post(f"{url}close/", {"downtime_hours": "2"}, follow=True)
        self.assertContains(response, "parte afectada")
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrderStatus.IN_EXECUTION)

    def test_operator_cannot_manage_but_can_see_the_orders_of_his_equipment(self) -> None:
        order = create_work_order(
            self.ctx,
            equipment_id=self.equipment.pk,
            kind=WorkOrderKind.CORRECTIVE,
            description="Fuga",
        )
        self.client.force_login(self.operator)
        self.assertContains(self.client.get(f"{BASE}mantenimiento/ordenes/{order.pk}/"), "OT-0001")
        self.assertNotContains(self.client.get(f"{BASE}mantenimiento/"), "Generar orden")
        self.assertEqual(
            self.client.post(f"{BASE}mantenimiento/ordenes/{order.pk}/start/").status_code, 403
        )
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrderStatus.DRAFT)
        self.assertEqual(
            self.client.get(f"{BASE}{self.equipment.pk}/ordenes/nueva/").status_code, 403
        )

    def test_corrective_order_is_created_from_a_fault(self) -> None:
        brakes = define_checklist_item(
            self.ctx, equipment_type_id=self.etype.pk, label="Frenos", critical=True
        )
        submit_inspection(
            resolve_request_context(Principal(user_id=self.operator.pk, org_id=self.org.pk)),
            inspection_id=uuid.uuid4(),
            equipment_id=self.equipment.pk,
            captured_at=PAST + timedelta(hours=1),
            answers=[AnswerIn(brakes.pk, False, "Pedal blando")],
            meter_unit="hours",
            meter_value=Decimal("1001"),
        )
        fault = EquipmentFault.objects.get()
        inspection_url = f"{BASE}inspecciones/{fault.inspection_id}/"
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(inspection_url), "Crear orden correctiva")
        form = self.client.get(f"{BASE}{self.equipment.pk}/ordenes/nueva/?fault={fault.pk}")
        self.assertContains(form, "Frenos: Pedal blando")
        self.client.post(
            f"{BASE}{self.equipment.pk}/ordenes/nueva/?fault={fault.pk}",
            {"kind": "corrective", "description": "Revisar frenos"},
        )
        fault.refresh_from_db()
        self.assertIsNotNone(fault.work_order_id)
        self.assertContains(self.client.get(inspection_url), "Con orden de trabajo")

    def test_plan_is_created_and_retired_from_the_web(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(
            f"{BASE}{self.equipment.pk}/planes/nuevo/",
            {
                "name": "Revisión anual",
                "kind": "date",
                "interval_days": "365",
                "alert_window": "30",
            },
        )
        self.assertRedirects(response, f"{BASE}{self.equipment.pk}/?tab=maintenance")
        annual = MaintenancePlan.objects.get(name="Revisión anual")
        tab = self.client.get(f"{BASE}{self.equipment.pk}/?tab=maintenance")
        self.assertContains(tab, "Revisión anual")
        self.assertContains(tab, "Disponibilidad")
        self.client.post(f"{BASE}mantenimiento/planes/{annual.pk}/retirar/")
        annual.refresh_from_db()
        self.assertFalse(annual.active)

    def test_invalid_plan_is_rejected(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(
            f"{BASE}{self.equipment.pk}/planes/nuevo/",
            {
                "name": "Mala",
                "kind": "meter",
                "meter_unit": "kilometers",
                "interval_value": "100",
                "alert_window": "0",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MaintenancePlan.objects.filter(name="Mala").exists())

    # --- Hoja de Vida ---------------------------------------------------------

    def pdf_text(self, response) -> str:
        reader = PdfReader(io.BytesIO(response.content))
        return "\n".join(page.extract_text() for page in reader.pages)

    def test_life_sheet_pdf_has_the_required_sections(self) -> None:
        order = create_work_order(
            self.ctx,
            equipment_id=self.equipment.pk,
            kind=WorkOrderKind.PREVENTIVE,
            description="Aceite",
            plan_id=self.plan.pk,
            mechanic_ids=[self.mechanic.pk],
        )
        self.client.force_login(self.approver)
        self.client.post(f"{BASE}mantenimiento/ordenes/{order.pk}/approve/")
        self.client.force_login(self.admin)
        self.client.post(f"{BASE}mantenimiento/ordenes/{order.pk}/start/")
        self.client.post(f"{BASE}mantenimiento/ordenes/{order.pk}/close/", {"downtime_hours": "3"})
        update_equipment(self.ctx, self.equipment.pk, license_plate="EXG871")

        response = self.client.get(f"{BASE}{self.equipment.pk}/hoja-de-vida.pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        text = self.pdf_text(response)
        for expected in (
            "Hoja de Vida",
            "EQ-MW",
            "EXG871",
            "Kenworth",
            "Cambio de aceite",
            "OT-0001",
        ):
            self.assertIn(expected, text)
        self.assertIn("Valor de adquisición", text)
        self.assertTrue(AuditEvent.objects.filter(action="equipment.life_sheet_exported").exists())

    def test_operator_life_sheet_hides_financial_data(self) -> None:
        self.client.force_login(self.operator)
        response = self.client.get(f"{BASE}{self.equipment.pk}/hoja-de-vida.pdf")
        self.assertEqual(response.status_code, 200)
        text = self.pdf_text(response)
        self.assertIn("EQ-MW", text)
        self.assertNotIn("Valor de adquisición", text)

    def test_life_sheet_of_another_organization_is_not_found(self) -> None:
        other_org = make_org("Otra")
        stranger = make_user(other_org, username="ajeno-mw", role=Role.ADMIN)
        self.client.force_login(stranger)
        self.assertIn(
            self.client.get(f"{BASE}{self.equipment.pk}/hoja-de-vida.pdf").status_code, (403, 404)
        )
