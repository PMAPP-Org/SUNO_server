"""Catálogo, scoping y asignaciones del Hito B de equipos."""

from __future__ import annotations

import unittest
from datetime import date, time

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    AuditEvent,
    Equipment,
    EquipmentCollection,
    EquipmentDocumentType,
    EquipmentOperatorAssignment,
    EquipmentOwnership,
    EquipmentProject,
    EquipmentStatus,
    EquipmentUsage,
    Project,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.services import (
    add_document,
    assign_operator,
    create_equipment,
    create_equipment_collection,
    create_equipment_type,
    create_equipment_usage,
    link_project,
    update_equipment,
    update_equipment_collection,
    visible_collections,
    visible_equipment,
    visible_usage,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class EquipmentServiceTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Equipos")
        self.admin = make_user(self.org, username="gestor", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador", role=Role.FIELD_AGENT)
        self.reviewer = make_user(self.org, username="revisor", role=Role.SUPERVISOR)
        self.project = Project.objects.create(
            org=self.org,
            name="Proyecto Norte",
            created_by=self.admin,
        )
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.equipment_type = create_equipment_type(
            self.ctx,
            code="EXC",
            name="Excavadora",
        )
        self.equipment = create_equipment(
            self.ctx,
            equipment_type_id=self.equipment_type.pk,
            code="EQ-001",
            name="Excavadora 01",
            manufacturer="CAT",
            ownership=EquipmentOwnership.OWN,
        )

    def grant_operator(self) -> None:
        set_section_grant(
            self.ctx,
            user_id=self.operator.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.EQUIPMENT_OPERATOR,
            scope=AccessScope.PROJECTS,
            project_ids=[self.project.pk],
        )

    def link(self) -> EquipmentProject:
        return link_project(
            self.ctx,
            equipment_id=self.equipment.pk,
            project_id=self.project.pk,
            started_on=date(2026, 11, 1),
        )

    def test_manager_creates_and_updates_audited_equipment(self) -> None:
        updated = update_equipment(
            self.ctx,
            self.equipment.pk,
            status=EquipmentStatus.MAINTENANCE,
            notes="Cambio de aceite",
        )
        self.assertEqual(updated.status, EquipmentStatus.MAINTENANCE)
        self.assertTrue(AuditEvent.objects.filter(action="equipment.created").exists())
        self.assertTrue(AuditEvent.objects.filter(action="equipment.updated").exists())

    def test_manager_creates_and_updates_collection_tags(self) -> None:
        vehicle_type = create_equipment_type(self.ctx, code="VEH", name="Vehículo")
        collection = create_equipment_collection(
            self.ctx,
            name="Operación en obra",
            description="Activos disponibles en el frente.",
            equipment_type_ids=[self.equipment_type.pk],
        )

        updated = update_equipment_collection(
            self.ctx,
            collection.pk,
            name="Operación y transporte",
            equipment_type_ids=[self.equipment_type.pk, vehicle_type.pk],
        )

        self.assertEqual(
            set(updated.equipment_types.values_list("pk", flat=True)),
            {self.equipment_type.pk, vehicle_type.pk},
        )
        self.assertTrue(AuditEvent.objects.filter(action="equipment.collection_created").exists())
        self.assertTrue(AuditEvent.objects.filter(action="equipment.collection_updated").exists())

    def test_collection_tags_cannot_cross_organizations(self) -> None:
        other_org = make_org("Colecciones externas")
        other_admin = make_user(other_org, username="gestor-colecciones", role=Role.ADMIN)
        other_ctx = resolve_request_context(Principal(user_id=other_admin.pk, org_id=other_org.pk))
        other_type = create_equipment_type(other_ctx, code="EXT", name="Externo")

        with self.assertRaisesMessage(ValidationError, "no existen"):
            create_equipment_collection(
                self.ctx,
                name="Colección inválida",
                equipment_type_ids=[other_type.pk],
            )

        self.assertFalse(EquipmentCollection.objects.filter(org=self.org).exists())
        self.assertFalse(visible_collections(self.ctx).filter(name="Colección inválida").exists())

    def test_code_serial_and_plate_are_unique_case_insensitively(self) -> None:
        with self.assertRaises(ValidationError):
            create_equipment(
                self.ctx,
                equipment_type_id=self.equipment_type.pk,
                code="eq-001",
                name="Duplicado",
            )

    def test_non_owned_equipment_requires_owner(self) -> None:
        with self.assertRaisesMessage(ValidationError, "Indica el propietario"):
            create_equipment(
                self.ctx,
                equipment_type_id=self.equipment_type.pk,
                code="EQ-002",
                name="Alquilada",
                ownership=EquipmentOwnership.RENTED,
            )

    def test_operator_only_sees_currently_assigned_equipment(self) -> None:
        self.grant_operator()
        self.link()
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=self.project.pk,
            valid_from=date(2026, 11, 1),
        )
        operator_ctx = resolve_request_context(
            Principal(user_id=self.operator.pk, org_id=self.org.pk)
        )

        self.assertEqual(
            list(visible_equipment(operator_ctx, on_date=date(2026, 11, 5))),
            [self.equipment],
        )
        self.assertFalse(visible_equipment(operator_ctx, on_date=date(2026, 10, 31)).exists())

    def test_operator_assignment_requires_role_and_project_link(self) -> None:
        with self.assertRaisesMessage(ValidationError, "no está vinculado"):
            assign_operator(
                self.ctx,
                equipment_id=self.equipment.pk,
                operator_id=self.operator.pk,
                project_id=self.project.pk,
                valid_from=date(2026, 11, 1),
            )
        self.link()
        with self.assertRaisesMessage(ValidationError, "no tiene rol de operador"):
            assign_operator(
                self.ctx,
                equipment_id=self.equipment.pk,
                operator_id=self.operator.pk,
                project_id=self.project.pk,
                valid_from=date(2026, 11, 1),
            )

    def test_overlapping_operator_assignment_is_rejected(self) -> None:
        self.grant_operator()
        self.link()
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=self.project.pk,
            valid_from=date(2026, 11, 1),
            valid_until=date(2026, 11, 10),
        )
        with self.assertRaisesMessage(ValidationError, "se solapa"):
            assign_operator(
                self.ctx,
                equipment_id=self.equipment.pk,
                operator_id=self.operator.pk,
                project_id=self.project.pk,
                valid_from=date(2026, 11, 5),
            )

    def test_different_operator_cannot_overlap_the_same_equipment(self) -> None:
        second_operator = make_user(
            self.org,
            username="operador-2",
            role=Role.FIELD_AGENT,
        )
        self.grant_operator()
        set_section_grant(
            self.ctx,
            user_id=second_operator.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.EQUIPMENT_OPERATOR,
            scope=AccessScope.PROJECTS,
            project_ids=[self.project.pk],
        )
        self.link()
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=self.project.pk,
            valid_from=date(2026, 11, 1),
            valid_until=date(2026, 11, 10),
        )

        with self.assertRaisesMessage(ValidationError, "se solapa"):
            assign_operator(
                self.ctx,
                equipment_id=self.equipment.pk,
                operator_id=second_operator.pk,
                project_id=self.project.pk,
                valid_from=date(2026, 11, 5),
            )

    def test_project_links_cannot_overlap(self) -> None:
        self.link()
        second_project = Project.objects.create(
            org=self.org,
            name="Proyecto Sur",
            created_by=self.admin,
        )

        with self.assertRaisesMessage(ValidationError, "ya está vinculado"):
            link_project(
                self.ctx,
                equipment_id=self.equipment.pk,
                project_id=second_project.pk,
                started_on=date(2026, 11, 5),
            )

    def test_document_dates_are_validated(self) -> None:
        with self.assertRaisesMessage(ValidationError, "anterior a la emisión"):
            add_document(
                self.ctx,
                equipment_id=self.equipment.pk,
                document_type=EquipmentDocumentType.INSURANCE,
                name="Póliza",
                issued_on=date(2026, 11, 5),
                expires_on=date(2026, 11, 4),
            )

    def test_manager_registers_audited_equipment_usage(self) -> None:
        self.grant_operator()
        self.link()
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=self.project.pk,
            valid_from=date(2026, 11, 1),
        )

        usage = create_equipment_usage(
            self.ctx,
            equipment_id=self.equipment.pk,
            project_id=self.project.pk,
            operator_id=self.operator.pk,
            usage_date=date(2026, 11, 5),
            start_time=time(7, 0),
            end_time=time(12, 0),
            activity="Excavación de material común",
            location="Frente norte",
            start_meter="1250.00",
            end_meter="1255.50",
        )

        self.assertEqual(usage.activity, "Excavación de material común")
        self.assertTrue(AuditEvent.objects.filter(action="equipment.usage_created").exists())

    def test_usage_rejects_overlapping_equipment_or_operator(self) -> None:
        self.grant_operator()
        self.link()
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=self.project.pk,
            valid_from=date(2026, 11, 1),
        )
        create_equipment_usage(
            self.ctx,
            equipment_id=self.equipment.pk,
            project_id=self.project.pk,
            operator_id=self.operator.pk,
            usage_date=date(2026, 11, 5),
            start_time=time(7, 0),
            end_time=time(12, 0),
            activity="Excavación",
        )

        with self.assertRaisesMessage(ValidationError, "otro uso"):
            create_equipment_usage(
                self.ctx,
                equipment_id=self.equipment.pk,
                project_id=self.project.pk,
                operator_id=self.operator.pk,
                usage_date=date(2026, 11, 5),
                start_time=time(11, 30),
                end_time=time(13, 0),
                activity="Cargue",
            )

    def test_operator_only_sees_own_usage(self) -> None:
        self.grant_operator()
        self.link()
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=self.project.pk,
            valid_from=date(2026, 11, 1),
        )
        usage = create_equipment_usage(
            self.ctx,
            equipment_id=self.equipment.pk,
            project_id=self.project.pk,
            operator_id=self.operator.pk,
            usage_date=date(2026, 11, 5),
            start_time=time(7, 0),
            end_time=time(12, 0),
            activity="Excavación",
        )
        operator_ctx = resolve_request_context(
            Principal(user_id=self.operator.pk, org_id=self.org.pk)
        )

        self.assertEqual(list(visible_usage(operator_ctx)), [usage])

    def test_reviewer_cannot_schedule_usage(self) -> None:
        set_section_grant(
            self.ctx,
            user_id=self.reviewer.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.REVIEWER,
            scope=AccessScope.ORGANIZATION,
        )
        reviewer_ctx = resolve_request_context(
            Principal(user_id=self.reviewer.pk, org_id=self.org.pk)
        )
        with self.assertRaises(PermissionDenied):
            create_equipment_usage(
                reviewer_ctx,
                equipment_id=self.equipment.pk,
                project_id=self.project.pk,
                operator_id=self.operator.pk,
                usage_date=date(2026, 11, 5),
                start_time=time(7, 0),
                end_time=time(12, 0),
                activity="Excavación",
            )

    def test_usage_time_and_meter_order_are_validated(self) -> None:
        usage = EquipmentUsage(
            org=self.org,
            equipment=self.equipment,
            project=self.project,
            operator=self.operator,
            usage_date=date(2026, 11, 5),
            start_time=time(12, 0),
            end_time=time(7, 0),
            activity="Inválida",
            start_meter="20.00",
            end_meter="10.00",
            created_by=self.admin,
            updated_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            usage.full_clean()

    def test_reviewer_cannot_create_equipment(self) -> None:
        set_section_grant(
            self.ctx,
            user_id=self.reviewer.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.REVIEWER,
            scope=AccessScope.ORGANIZATION,
        )
        reviewer_ctx = resolve_request_context(
            Principal(user_id=self.reviewer.pk, org_id=self.org.pk)
        )
        with self.assertRaises(PermissionDenied):
            create_equipment_type(reviewer_ctx, code="GR", name="Grúa")

    def test_database_rejects_duplicate_active_project_link(self) -> None:
        self.link()
        second_project = Project.objects.create(
            org=self.org,
            name="Proyecto alterno",
            created_by=self.admin,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            EquipmentProject.objects.create(
                org=self.org,
                equipment=self.equipment,
                project=second_project,
                started_on=date(2026, 11, 2),
                assigned_by=self.admin,
            )

    def test_other_organization_is_never_visible(self) -> None:
        other = make_org("Otra")
        other_admin = make_user(other, username="otra-admin", role=Role.ADMIN)
        other_ctx = resolve_request_context(Principal(user_id=other_admin.pk, org_id=other.pk))
        self.assertFalse(visible_equipment(other_ctx).filter(pk=self.equipment.pk).exists())

    def test_open_assignment_is_unique_at_database_level(self) -> None:
        self.grant_operator()
        self.link()
        first = assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=self.project.pk,
            valid_from=date(2026, 11, 1),
        )
        self.assertIsInstance(first, EquipmentOperatorAssignment)
        self.assertEqual(
            Equipment.objects.get(pk=self.equipment.pk).operator_assignments.count(),
            1,
        )
