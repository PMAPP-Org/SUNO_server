"""Flujos web de preoperacionales: bandeja, revisión, novedades y lista de chequeo."""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    EquipmentFault,
    EquipmentInspection,
    FaultStatus,
    InspectionStatus,
    PreoperationalItem,
    Project,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.inspections import AnswerIn, define_checklist_item, submit_inspection
from suno.equipment.services import (
    assign_operator,
    create_equipment,
    create_equipment_type,
    link_project,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

BASE = "/plataforma/equipos/"


@skip_without_db
class InspectionWebTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Preop Web")
        self.admin = make_user(self.org, username="gestor-pw", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador-pw", role=Role.FIELD_AGENT)
        self.reviewer = make_user(self.org, username="revisor-pw", role=Role.SUPERVISOR)
        ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.ctx = ctx
        project = Project.objects.create(org=self.org, name="Norte", created_by=self.admin)
        self.etype = create_equipment_type(ctx, code="VOL", name="Volqueta")
        self.equipment = create_equipment(
            ctx,
            equipment_type_id=self.etype.pk,
            code="EQ-PW",
            name="Volqueta PW",
            primary_meter_unit="hours",
        )
        link_project(
            ctx,
            equipment_id=self.equipment.pk,
            project_id=project.pk,
            started_on=date.today() - timedelta(days=3),
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
            user_id=self.reviewer.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.REVIEWER,
            scope=AccessScope.ORGANIZATION,
        )
        assign_operator(
            ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=project.pk,
            valid_from=date.today() - timedelta(days=1),
        )
        self.brakes = define_checklist_item(
            ctx, equipment_type_id=self.etype.pk, label="Frenos", critical=True
        )
        op_ctx = resolve_request_context(Principal(user_id=self.operator.pk, org_id=self.org.pk))
        self.inspection = submit_inspection(
            op_ctx,
            inspection_id=uuid.uuid4(),
            equipment_id=self.equipment.pk,
            captured_at=datetime.now(UTC) - timedelta(hours=1),
            answers=[AnswerIn(self.brakes.pk, False, "Pedal blando")],
            meter_unit="hours",
            meter_value=Decimal("100"),
        )
        self.url = f"{BASE}inspecciones/{self.inspection.pk}/"

    def test_reviewer_sees_the_inbox_with_pending_and_open_faults(self) -> None:
        self.client.force_login(self.reviewer)
        page = self.client.get(f"{BASE}inspecciones/")
        self.assertContains(page, "EQ-PW")
        self.assertContains(page, "1 pendiente de revisión")
        self.assertContains(page, "1 novedad abierta")
        filtered = self.client.get(f"{BASE}inspecciones/", {"status": "approved"})
        self.assertNotContains(filtered, "EQ-PW · Volqueta PW")

    def test_operator_sees_only_own_inspections_and_cannot_review(self) -> None:
        self.client.force_login(self.operator)
        self.assertContains(self.client.get(f"{BASE}inspecciones/"), "EQ-PW")
        detail = self.client.get(self.url)
        self.assertNotContains(detail, "Tu decisión")
        response = self.client.post(f"{self.url}revisar/", {"decision": "approve"})
        self.assertEqual(response.status_code, 403)
        self.inspection.refresh_from_db()
        self.assertEqual(self.inspection.status, InspectionStatus.SUBMITTED)

    def test_reviewer_approves_from_the_web(self) -> None:
        self.client.force_login(self.reviewer)
        self.assertContains(self.client.get(self.url), "Tu decisión")
        response = self.client.post(f"{self.url}revisar/", {"decision": "approve"})
        self.assertRedirects(response, self.url)
        self.inspection.refresh_from_db()
        self.assertEqual(self.inspection.status, InspectionStatus.APPROVED)
        self.assertNotContains(self.client.get(self.url), "Tu decisión")

    def test_reject_without_comment_keeps_it_pending(self) -> None:
        self.client.force_login(self.reviewer)
        self.client.post(f"{self.url}revisar/", {"decision": "reject", "comment": ""})
        self.inspection.refresh_from_db()
        self.assertEqual(self.inspection.status, InspectionStatus.SUBMITTED)

    def test_manager_closes_the_fault_from_the_web(self) -> None:
        fault = EquipmentFault.objects.get()
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url), "Cómo se resolvió")
        response = self.client.post(
            f"{BASE}novedades/{fault.pk}/cerrar/", {"note": "Cambio de pastillas"}
        )
        self.assertRedirects(response, self.url)
        fault.refresh_from_db()
        self.assertEqual(fault.status, FaultStatus.CLOSED)

    def test_operator_cannot_close_a_fault(self) -> None:
        fault = EquipmentFault.objects.get()
        self.client.force_login(self.operator)
        self.client.post(f"{BASE}novedades/{fault.pk}/cerrar/", {"note": "listo"})
        fault.refresh_from_db()
        self.assertEqual(fault.status, FaultStatus.OPEN)

    def test_manager_edits_the_checklist_and_others_cannot(self) -> None:
        url = f"{BASE}tipos/{self.etype.pk}/lista/"
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url), "Frenos")
        self.client.post(url, {"label": "Luces", "critical": "on"})
        self.assertTrue(PreoperationalItem.objects.get(label="Luces").critical)
        self.client.post(url, {"action": "retire", "item_id": str(self.brakes.pk)})
        self.brakes.refresh_from_db()
        self.assertFalse(self.brakes.active)
        duplicate = self.client.post(url, {"label": "luces"}, follow=True)
        self.assertContains(duplicate, "ya está en la lista")

        self.client.force_login(self.operator)
        self.assertEqual(self.client.post(url, {"label": "Espejos"}).status_code, 403)
        self.assertFalse(PreoperationalItem.objects.filter(label="Espejos").exists())

    def test_other_organizations_cannot_open_it(self) -> None:
        other_org = make_org("Otra")
        stranger = make_user(other_org, username="intruso", role=Role.ADMIN)
        self.client.force_login(stranger)
        self.assertIn(self.client.get(self.url).status_code, (403, 404))
        self.assertEqual(EquipmentInspection.objects.count(), 1)
