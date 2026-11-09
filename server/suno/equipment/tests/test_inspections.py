"""Preoperacionales: lista de chequeo, envío idempotente, revisión y novedades."""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    AuditEvent,
    EquipmentFault,
    EquipmentInspection,
    EquipmentMeterReading,
    FaultStatus,
    InspectionStatus,
    Project,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.inspections import (
    AnswerIn,
    checklist_for,
    close_fault,
    define_checklist_item,
    recent_inspections,
    retire_checklist_item,
    review_inspection,
    submit_inspection,
    visible_faults,
    visible_inspections,
)
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

NOW = datetime.now(UTC) - timedelta(hours=1)


def ctx_of(user, org):
    return resolve_request_context(Principal(user_id=user.pk, org_id=org.pk))


@skip_without_db
class PreoperationalTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Preop")
        self.admin = make_user(self.org, username="gestor", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador", role=Role.FIELD_AGENT)
        self.reviewer = make_user(self.org, username="revisor", role=Role.SUPERVISOR)
        self.ctx = ctx_of(self.admin, self.org)
        self.project = Project.objects.create(
            org=self.org, name="Proyecto Norte", created_by=self.admin
        )
        etype = create_equipment_type(self.ctx, code="VOL", name="Volqueta")
        self.etype = etype
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
            project_id=self.project.pk,
            started_on=date.today() - timedelta(days=3),
        )
        set_section_grant(
            self.ctx,
            user_id=self.operator.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.EQUIPMENT_OPERATOR,
            scope=AccessScope.PROJECTS,
            project_ids=[self.project.pk],
        )
        set_section_grant(
            self.ctx,
            user_id=self.reviewer.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.REVIEWER,
            scope=AccessScope.ORGANIZATION,
        )
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=self.project.pk,
            valid_from=date.today() - timedelta(days=1),
        )
        self.brakes = define_checklist_item(
            self.ctx, equipment_type_id=etype.pk, label="Frenos", critical=True
        )
        self.lights = define_checklist_item(self.ctx, equipment_type_id=etype.pk, label="Luces")
        self.op_ctx = ctx_of(self.operator, self.org)
        self.rev_ctx = ctx_of(self.reviewer, self.org)

    def answers(self, *, brakes_ok=True, lights_ok=True) -> list[AnswerIn]:
        return [
            AnswerIn(self.brakes.pk, brakes_ok, "" if brakes_ok else "Pedal blando"),
            AnswerIn(self.lights.pk, lights_ok, "" if lights_ok else "Faro izquierdo"),
        ]

    def submit(self, ctx=None, **overrides):
        values = {
            "inspection_id": uuid.uuid4(),
            "equipment_id": self.equipment.pk,
            "captured_at": NOW,
            "answers": self.answers(),
            "meter_unit": "hours",
            "meter_value": Decimal("339"),
        }
        values.update(overrides)
        return submit_inspection(ctx or self.op_ctx, **values)

    # --- lista de chequeo -----------------------------------------------------

    def test_checklist_keeps_order_and_retiring_hides_without_deleting(self) -> None:
        self.assertEqual(
            [i.label for i in checklist_for(self.ctx, self.etype.pk)], ["Frenos", "Luces"]
        )
        retire_checklist_item(self.ctx, self.lights.pk)
        self.assertEqual([i.label for i in checklist_for(self.ctx, self.etype.pk)], ["Frenos"])
        self.lights.refresh_from_db()
        self.assertFalse(self.lights.active)

    def test_retired_item_is_restored_instead_of_duplicated(self) -> None:
        retire_checklist_item(self.ctx, self.lights.pk)
        restored = define_checklist_item(
            self.ctx, equipment_type_id=self.etype.pk, label="LUCES", critical=True
        )
        self.assertEqual(restored.pk, self.lights.pk)
        self.assertTrue(restored.active and restored.critical)
        self.assertEqual(len(checklist_for(self.ctx, self.etype.pk)), 2)

    def test_duplicate_label_is_rejected_and_operator_cannot_edit_the_list(self) -> None:
        with self.assertRaisesMessage(ValidationError, "ya está en la lista"):
            define_checklist_item(self.ctx, equipment_type_id=self.etype.pk, label="frenos")
        with self.assertRaises(PermissionDenied):
            define_checklist_item(self.op_ctx, equipment_type_id=self.etype.pk, label="Espejos")

    # --- envío ----------------------------------------------------------------

    def test_clean_inspection_is_stored_with_snapshot_and_meter_reading(self) -> None:
        inspection = self.submit()
        self.assertFalse(inspection.has_faults)
        self.assertEqual(inspection.status, InspectionStatus.SUBMITTED)
        self.assertEqual(inspection.project_id, self.project.pk)
        self.assertEqual([row["label"] for row in inspection.items], ["Frenos", "Luces"])
        reading = EquipmentMeterReading.objects.get(equipment=self.equipment)
        self.assertEqual((reading.value, reading.source), (Decimal("339.00"), "preoperational"))
        self.assertTrue(AuditEvent.objects.filter(action="equipment.inspection_submitted").exists())

    def test_failed_items_open_faults_and_critical_ones_are_marked(self) -> None:
        inspection = self.submit(answers=self.answers(brakes_ok=False, lights_ok=False))
        faults = {f.item_label: f for f in EquipmentFault.objects.filter(inspection=inspection)}
        self.assertTrue(inspection.has_faults)
        self.assertTrue(faults["Frenos"].critical)
        self.assertFalse(faults["Luces"].critical)
        self.assertEqual(faults["Frenos"].description, "Pedal blando")

    def test_resubmitting_the_same_id_returns_the_stored_inspection(self) -> None:
        inspection_id = uuid.uuid4()
        first = self.submit(inspection_id=inspection_id)
        again = self.submit(inspection_id=inspection_id)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(EquipmentInspection.objects.count(), 1)
        self.assertEqual(EquipmentMeterReading.objects.count(), 1)

    def test_same_id_from_another_operator_is_refused(self) -> None:
        inspection_id = uuid.uuid4()
        self.submit(inspection_id=inspection_id)
        with self.assertRaises((ValidationError, Http404, PermissionDenied)):
            self.submit(self.rev_ctx, inspection_id=inspection_id)

    def test_every_active_item_must_be_answered_once_and_failures_explained(self) -> None:
        with self.assertRaisesMessage(ValidationError, "Faltan"):
            self.submit(answers=self.answers()[:1])
        with self.assertRaisesMessage(ValidationError, "más de una vez"):
            self.submit(answers=[*self.answers(), AnswerIn(self.brakes.pk, True)])
        with self.assertRaisesMessage(ValidationError, "no pertenecen"):
            self.submit(answers=[*self.answers(), AnswerIn(uuid.uuid4(), True)])
        with self.assertRaisesMessage(ValidationError, "Describe la novedad"):
            self.submit(
                answers=[AnswerIn(self.brakes.pk, False, ""), AnswerIn(self.lights.pk, True)]
            )
        self.assertEqual(EquipmentInspection.objects.count(), 0)

    def test_meter_is_mandatory_and_a_backwards_reading_discards_the_whole_inspection(self) -> None:
        with self.assertRaisesMessage(ValidationError, "obligatoria"):
            self.submit(meter_unit="", meter_value=None)
        self.submit(meter_value=Decimal("500"))
        with self.assertRaisesMessage(ValidationError, "no retroceden"):
            self.submit(meter_value=Decimal("100"), captured_at=NOW + timedelta(minutes=5))
        self.assertEqual(EquipmentInspection.objects.count(), 1)

    def test_date_scheme_does_not_need_a_meter(self) -> None:
        update_equipment(
            self.ctx,
            self.equipment.pk,
            control_scheme="date",
            primary_meter_unit="",
        )
        inspection = self.submit(meter_unit="", meter_value=None)
        self.assertEqual(inspection.meter_unit, "")

    def test_unavailable_equipment_and_unassigned_operator(self) -> None:
        update_equipment(self.ctx, self.equipment.pk, status="out_of_service")
        with self.assertRaisesMessage(ValidationError, "no está disponible"):
            self.submit()
        update_equipment(self.ctx, self.equipment.pk, status="available")
        stranger = make_user(self.org, username="otro", role=Role.FIELD_AGENT)
        with self.assertRaises(Http404):
            self.submit(ctx_of(stranger, self.org))

    def test_future_capture_is_rejected(self) -> None:
        with self.assertRaisesMessage(ValidationError, "futuro"):
            self.submit(captured_at=datetime.now(UTC) + timedelta(days=1))

    # --- visibilidad ----------------------------------------------------------

    def test_operator_sees_only_own_and_reviewer_sees_all(self) -> None:
        mine = self.submit()
        self.assertEqual(list(visible_inspections(self.op_ctx)), [mine])
        self.assertEqual(list(visible_inspections(self.rev_ctx)), [mine])
        other = make_user(self.org, username="otro-op", role=Role.FIELD_AGENT)
        set_section_grant(
            self.ctx,
            user_id=other.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.EQUIPMENT_OPERATOR,
            scope=AccessScope.PROJECTS,
            project_ids=[self.project.pk],
        )
        self.assertEqual(list(visible_inspections(ctx_of(other, self.org))), [])
        self.assertEqual(len(visible_faults(self.rev_ctx)), 0)

    def test_recent_inspections_are_own_and_capped(self) -> None:
        for minutes in range(7):
            self.submit(
                captured_at=NOW + timedelta(minutes=minutes),
                meter_value=Decimal(400 + minutes),
            )
        recent = recent_inspections(self.op_ctx, [self.equipment.pk], per_equipment=5)
        self.assertEqual(len(recent[self.equipment.pk]), 5)
        self.assertEqual(recent_inspections(self.rev_ctx, [self.equipment.pk]), {})

    # --- revisión -------------------------------------------------------------

    def test_reviewer_approves_and_the_evidence_is_untouched(self) -> None:
        inspection = self.submit(answers=self.answers(lights_ok=False))
        before = list(inspection.items)
        reviewed = review_inspection(self.rev_ctx, inspection.pk, decision="approve")
        reviewed.refresh_from_db()
        self.assertEqual(reviewed.status, InspectionStatus.APPROVED)
        self.assertEqual(reviewed.reviewed_by_id, self.reviewer.pk)
        self.assertEqual(reviewed.items, before)
        self.assertTrue(AuditEvent.objects.filter(action="equipment.inspection_reviewed").exists())

    def test_reject_and_correction_need_a_reason_and_only_once(self) -> None:
        inspection = self.submit()
        with self.assertRaisesMessage(ValidationError, "motivo"):
            review_inspection(self.rev_ctx, inspection.pk, decision="reject")
        review_inspection(
            self.rev_ctx, inspection.pk, decision="request_correction", comment="Falta foto"
        )
        with self.assertRaisesMessage(ValidationError, "ya fue revisada"):
            review_inspection(self.rev_ctx, inspection.pk, decision="approve")
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, InspectionStatus.CORRECTION_REQUESTED)
        self.assertEqual(inspection.review_comment, "Falta foto")

    def test_operator_and_manager_cannot_review_and_nobody_reviews_their_own(self) -> None:
        inspection = self.submit()
        with self.assertRaises(PermissionDenied):
            review_inspection(self.op_ctx, inspection.pk, decision="approve")
        with self.assertRaises(PermissionDenied):
            review_inspection(self.ctx, inspection.pk, decision="approve")
        set_section_grant(
            self.ctx,
            user_id=self.operator.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.REVIEWER,
            scope=AccessScope.ORGANIZATION,
        )
        with self.assertRaisesMessage(PermissionDenied, "propia"):
            review_inspection(ctx_of(self.operator, self.org), inspection.pk, decision="approve")

    def test_unknown_decision_is_rejected(self) -> None:
        inspection = self.submit()
        with self.assertRaises(ValidationError):
            review_inspection(self.rev_ctx, inspection.pk, decision="shrug")

    # --- novedades ------------------------------------------------------------

    def test_manager_closes_a_fault_with_a_note_only_once(self) -> None:
        inspection = self.submit(answers=self.answers(brakes_ok=False))
        fault = EquipmentFault.objects.get(inspection=inspection)
        with self.assertRaisesMessage(ValidationError, "resolvió"):
            close_fault(self.ctx, fault.pk, note=" ")
        closed = close_fault(self.ctx, fault.pk, note="Cambio de pastillas")
        self.assertEqual((closed.status, closed.closed_by_id), (FaultStatus.CLOSED, self.admin.pk))
        with self.assertRaisesMessage(ValidationError, "ya está cerrada"):
            close_fault(self.ctx, fault.pk, note="otra vez")
        self.assertTrue(AuditEvent.objects.filter(action="equipment.fault_closed").exists())

    def test_operator_cannot_close_faults(self) -> None:
        self.submit(answers=self.answers(brakes_ok=False))
        fault = EquipmentFault.objects.get()
        with self.assertRaises(PermissionDenied):
            close_fault(self.op_ctx, fault.pk, note="yo mismo")
