"""Mantenimiento: planes, alertas, ciclo de la orden, modo de falla e indicadores."""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, date, datetime, time, timedelta
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
    EquipmentStatus,
    FaultStatus,
    PlanKind,
    Project,
    Role,
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
from suno.equipment.maintenance import (
    add_line,
    approve_order,
    assign_mechanic,
    cancel_order,
    close_order,
    create_plan,
    create_work_order,
    maintenance_alerts,
    maintenance_kpis,
    plan_status,
    preventive_history,
    remove_line,
    retire_plan,
    start_order,
    visible_orders,
)
from suno.equipment.services import (
    assign_operator,
    create_equipment,
    create_equipment_type,
    create_equipment_usage,
    link_project,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

PAST = datetime.now(UTC) - timedelta(days=2)


def ctx_of(user, org):
    return resolve_request_context(Principal(user_id=user.pk, org_id=org.pk))


@skip_without_db
class MaintenanceTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Mantenimiento")
        self.admin = make_user(self.org, username="gestor", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador", role=Role.FIELD_AGENT)
        self.approver = make_user(self.org, username="aprobador", role=Role.SUPERVISOR)
        self.mechanic = make_user(self.org, username="mecanico", role=Role.FIELD_AGENT)
        self.ctx = ctx_of(self.admin, self.org)
        project = Project.objects.create(org=self.org, name="Norte", created_by=self.admin)
        self.project = project
        self.etype = create_equipment_type(self.ctx, code="VOL", name="Volqueta")
        self.equipment = create_equipment(
            self.ctx,
            equipment_type_id=self.etype.pk,
            code="EQ-1",
            name="Volqueta 1",
            primary_meter_unit="hours",
        )
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
            project_ids=[project.pk],
        )
        set_section_grant(
            self.ctx,
            user_id=self.approver.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.REVIEWER,
            scope=AccessScope.ORGANIZATION,
        )
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=project.pk,
            valid_from=date.today() - timedelta(days=20),
        )
        self.op_ctx = ctx_of(self.operator, self.org)
        self.app_ctx = ctx_of(self.approver, self.org)
        self.reading(Decimal("1000"), minutes=0)

    def reading(self, value: Decimal, *, minutes: int):
        return record_meter_reading(
            self.ctx,
            equipment_id=self.equipment.pk,
            meter_unit="hours",
            value=value,
            read_at=PAST + timedelta(minutes=minutes),
        )

    def meter_plan(self, **overrides):
        values = {
            "equipment_id": self.equipment.pk,
            "name": "Cambio de aceite",
            "kind": PlanKind.METER,
            "meter_unit": "hours",
            "interval_value": Decimal("250"),
            "alert_window": Decimal("50"),
            "last_done_value": Decimal("800"),
        }
        values.update(overrides)
        return create_plan(self.ctx, **values)

    def preventive_order(self, plan=None):
        plan = plan or self.meter_plan()
        return create_work_order(
            self.ctx,
            equipment_id=self.equipment.pk,
            kind=WorkOrderKind.PREVENTIVE,
            description="Cambio de aceite y filtros",
            plan_id=plan.pk,
        )

    def run_to_execution(self, order):
        approve_order(self.app_ctx, order.pk)
        return start_order(self.ctx, order.pk)

    # --- planes y alertas -----------------------------------------------------

    def test_plan_by_meter_computes_remaining_and_state(self) -> None:
        plan = self.meter_plan(last_done_value=Decimal("750"))
        status = plan_status(self.ctx, plan)
        self.assertEqual((status.due_value, status.current, status.remaining), (1000, 1000, 0))
        self.assertEqual(status.state, "due_soon")

        plan.last_done_value = Decimal("600")
        self.assertEqual(plan_status(self.ctx, plan).state, "overdue")
        self.assertEqual(plan_status(self.ctx, plan).remaining, Decimal("-150"))
        far = self.meter_plan(name="Filtros", last_done_value=Decimal("950"))
        self.assertEqual(plan_status(self.ctx, far).state, "ok")

    def test_without_a_baseline_the_plan_counts_from_the_current_meter(self) -> None:
        plan = self.meter_plan(last_done_value=None)
        self.assertEqual(plan.last_done_value, Decimal("1000.00"))
        self.assertEqual(plan_status(self.ctx, plan).remaining, Decimal("250.00"))

    def test_plan_by_date_uses_days_and_window(self) -> None:
        plan = create_plan(
            self.ctx,
            equipment_id=self.equipment.pk,
            name="Revisión anual",
            kind=PlanKind.DATE,
            interval_days=365,
            alert_window=Decimal("30"),
            last_done_on=date.today() - timedelta(days=340),
        )
        status = plan_status(self.ctx, plan)
        self.assertEqual((status.remaining, status.state), (Decimal("25"), "due_soon"))
        self.assertEqual(status.due_on, date.today() + timedelta(days=25))

    def test_plan_validation(self) -> None:
        with self.assertRaisesMessage(ValidationError, "no tiene configurado"):
            self.meter_plan(meter_unit="kilometers")
        with self.assertRaises(ValidationError):
            self.meter_plan(interval_value=Decimal("0"))
        with self.assertRaises(ValidationError):
            create_plan(
                self.ctx,
                equipment_id=self.equipment.pk,
                name="x",
                kind=PlanKind.DATE,
                interval_days=0,
            )
        with self.assertRaises(PermissionDenied):
            create_plan(
                self.op_ctx,
                equipment_id=self.equipment.pk,
                name="y",
                kind=PlanKind.DATE,
                interval_days=30,
            )

    def test_alerts_list_only_due_plans_most_urgent_first(self) -> None:
        self.meter_plan(name="Aceite", last_done_value=Decimal("760"))  # falta 10
        self.meter_plan(name="Filtros", last_done_value=Decimal("700"))  # vencido -50
        self.meter_plan(name="Lejano", last_done_value=Decimal("990"))
        self.assertEqual([a.plan.name for a in maintenance_alerts(self.ctx)], ["Filtros", "Aceite"])

    def test_alert_stays_until_the_order_is_closed_technically(self) -> None:
        plan = self.meter_plan(last_done_value=Decimal("760"))
        order = self.preventive_order(plan)
        self.assertEqual(len(maintenance_alerts(self.ctx)), 1)
        open_order = maintenance_alerts(self.ctx)[0].open_order
        self.assertIsNotNone(open_order)
        self.assertEqual(open_order.pk if open_order else None, order.pk)
        cancel_order(self.ctx, order.pk, reason="No era necesaria")
        self.assertEqual(len(maintenance_alerts(self.ctx)), 1)
        self.assertIsNone(maintenance_alerts(self.ctx)[0].open_order)

        order = self.preventive_order(plan)
        self.run_to_execution(order)
        self.assertEqual(len(maintenance_alerts(self.ctx)), 1)
        assign_mechanic(self.ctx, order.pk, mechanic_id=self.mechanic.pk)
        close_order(self.ctx, order.pk, downtime_hours=Decimal("3"))
        self.assertEqual(maintenance_alerts(self.ctx), [])
        plan.refresh_from_db()
        self.assertEqual(plan.last_done_value, Decimal("1000.00"))

    def test_retire_plan_blocked_with_open_order(self) -> None:
        plan = self.meter_plan()
        self.preventive_order(plan)
        with self.assertRaisesMessage(ValidationError, "orden abierta"):
            retire_plan(self.ctx, plan.pk)

    # --- ciclo de la orden ----------------------------------------------------

    def test_full_cycle_with_numbering_costs_and_equipment_status(self) -> None:
        order = self.preventive_order()
        self.assertEqual((order.code, order.status), ("OT-0001", WorkOrderStatus.DRAFT))
        add_line(
            self.ctx,
            order.pk,
            kind="part",
            description="Aceite 15W40",
            quantity=Decimal("4"),
            unit_cost=Decimal("85000"),
        )
        add_line(
            self.ctx,
            order.pk,
            kind="service",
            description="Rectificación",
            supplier="Tornería Ruiz",
            quantity=Decimal("1"),
            unit_cost=Decimal("300000.50"),
        )
        self.assertEqual(sum(line.total for line in order.lines.all()), Decimal("640000.50"))

        approve_order(self.app_ctx, order.pk)
        start_order(self.ctx, order.pk)
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.status, EquipmentStatus.MAINTENANCE)
        assign_mechanic(self.ctx, order.pk, mechanic_id=self.mechanic.pk)
        closed = close_order(self.ctx, order.pk, downtime_hours=Decimal("5.5"), notes="Sin novedad")
        self.equipment.refresh_from_db()
        self.assertEqual(closed.status, WorkOrderStatus.CLOSED_TECHNICAL)
        self.assertEqual(self.equipment.status, EquipmentStatus.ASSIGNED)
        self.assertEqual(
            (closed.meter_at_close, closed.downtime_hours), (Decimal("1000.00"), Decimal("5.5"))
        )
        for action in ("created", "approved", "started", "closed"):
            self.assertTrue(AuditEvent.objects.filter(action=f"work_order.{action}").exists())
        second = create_work_order(
            self.ctx,
            equipment_id=self.equipment.pk,
            kind=WorkOrderKind.CORRECTIVE,
            description="Fuga",
        )
        self.assertEqual(second.number, 2)

    def test_transitions_are_enforced(self) -> None:
        order = self.preventive_order()
        with self.assertRaisesMessage(ValidationError, "aprobada"):
            start_order(self.ctx, order.pk)
        with self.assertRaisesMessage(ValidationError, "en ejecución"):
            close_order(self.ctx, order.pk, downtime_hours=Decimal("1"))
        approve_order(self.app_ctx, order.pk)
        with self.assertRaisesMessage(ValidationError, "borrador"):
            approve_order(self.app_ctx, order.pk)
        start_order(self.ctx, order.pk)
        with self.assertRaisesMessage(ValidationError, "no se ejecuta"):
            cancel_order(self.ctx, order.pk, reason="tarde")

    def test_creator_cannot_approve_and_operator_cannot_manage(self) -> None:
        order = self.preventive_order()
        with self.assertRaises(PermissionDenied):
            approve_order(self.ctx, order.pk)
        with self.assertRaises(PermissionDenied):
            approve_order(self.op_ctx, order.pk)
        with self.assertRaises(PermissionDenied):
            create_work_order(
                self.op_ctx,
                equipment_id=self.equipment.pk,
                kind=WorkOrderKind.CORRECTIVE,
                description="x",
            )
        self.assertEqual(visible_orders(self.op_ctx).count(), 1)

    def test_close_requires_duration_mechanic_and_failure_mode(self) -> None:
        order = self.run_to_execution(self.preventive_order())
        with self.assertRaisesMessage(ValidationError, "duración"):
            close_order(self.ctx, order.pk, downtime_hours=None)
        with self.assertRaisesMessage(ValidationError, "mecánico"):
            close_order(self.ctx, order.pk, downtime_hours=Decimal("1"))
        close_order(
            self.ctx, order.pk, downtime_hours=Decimal("1"), mechanic_ids=[self.mechanic.pk]
        )

        corrective = create_work_order(
            self.ctx,
            equipment_id=self.equipment.pk,
            kind=WorkOrderKind.CORRECTIVE,
            description="Fuga de aceite",
            mechanic_ids=[self.mechanic.pk],
        )
        self.run_to_execution(corrective)
        with self.assertRaisesMessage(ValidationError, "parte afectada"):
            close_order(self.ctx, corrective.pk, downtime_hours=Decimal("4"))
        done = close_order(
            self.ctx,
            corrective.pk,
            downtime_hours=Decimal("4"),
            failed_part="Sello del cárter",
            failure_cause="Desgaste",
        )
        self.assertEqual((done.failed_part, done.failure_cause), ("Sello del cárter", "Desgaste"))

    def test_only_one_open_order_per_plan_and_plan_orders_are_preventive(self) -> None:
        plan = self.meter_plan()
        self.preventive_order(plan)
        with self.assertRaisesMessage(ValidationError, "orden abierta"):
            self.preventive_order(plan)
        other = self.meter_plan(name="Otro")
        with self.assertRaisesMessage(ValidationError, "preventiva"):
            create_work_order(
                self.ctx,
                equipment_id=self.equipment.pk,
                kind=WorkOrderKind.CORRECTIVE,
                description="x",
                plan_id=other.pk,
            )

    def test_lines_validate_and_cannot_change_after_closing(self) -> None:
        order = self.preventive_order()
        with self.assertRaisesMessage(ValidationError, "proveedor"):
            add_line(
                self.ctx,
                order.pk,
                kind="service",
                description="Torno",
                quantity=Decimal("1"),
                unit_cost=Decimal("10"),
            )
        with self.assertRaises(ValidationError):
            add_line(
                self.ctx,
                order.pk,
                kind="part",
                description="Aceite",
                quantity=Decimal("0"),
                unit_cost=Decimal("10"),
            )
        line = add_line(
            self.ctx,
            order.pk,
            kind="labor",
            description="Horas mecánico",
            quantity=Decimal("2"),
            unit_cost=Decimal("30000"),
        )
        remove_line(self.ctx, line.pk)
        self.assertEqual(order.lines.count(), 0)
        cancel_order(self.ctx, order.pk, reason="Duplicada")
        with self.assertRaisesMessage(ValidationError, "cerrada o cancelada"):
            add_line(
                self.ctx,
                order.pk,
                kind="labor",
                description="x",
                quantity=Decimal("1"),
                unit_cost=Decimal("1"),
            )

    def test_cancel_needs_a_reason(self) -> None:
        order = self.preventive_order()
        with self.assertRaisesMessage(ValidationError, "motivo"):
            cancel_order(self.ctx, order.pk, reason=" ")

    def test_mechanics_must_belong_to_the_org_and_not_repeat(self) -> None:
        order = self.preventive_order()
        assign_mechanic(self.ctx, order.pk, mechanic_id=self.mechanic.pk)
        with self.assertRaisesMessage(ValidationError, "ya está asignado"):
            assign_mechanic(self.ctx, order.pk, mechanic_id=self.mechanic.pk)
        stranger = make_user(make_org("Otra"), username="ajeno", role=Role.FIELD_AGENT)
        with self.assertRaisesMessage(ValidationError, "no pertenecen"):
            assign_mechanic(self.ctx, order.pk, mechanic_id=stranger.pk)

    def test_other_organizations_cannot_see_the_order(self) -> None:
        order = self.preventive_order()
        other_org = make_org("Ajena")
        other = make_user(other_org, username="admin-ajeno", role=Role.ADMIN)
        with self.assertRaises(Http404):
            approve_order(ctx_of(other, other_org), order.pk)

    # --- novedades → correctiva ----------------------------------------------

    def faults(self):
        etype = self.etype
        brakes = define_checklist_item(
            self.ctx, equipment_type_id=etype.pk, label="Frenos", critical=True
        )
        submit_inspection(
            self.op_ctx,
            inspection_id=uuid.uuid4(),
            equipment_id=self.equipment.pk,
            captured_at=PAST + timedelta(hours=1),
            answers=[AnswerIn(brakes.pk, False, "Pedal blando")],
            meter_unit="hours",
            meter_value=Decimal("1001"),
        )
        return EquipmentFault.objects.get()

    def test_corrective_order_from_a_fault_closes_it_with_the_order(self) -> None:
        fault = self.faults()
        order = create_work_order(
            self.ctx,
            equipment_id=self.equipment.pk,
            kind=WorkOrderKind.CORRECTIVE,
            description="Revisar frenos",
            fault_ids=[fault.pk],
            mechanic_ids=[self.mechanic.pk],
        )
        with self.assertRaisesMessage(ValidationError, "ya tienen"):
            create_work_order(
                self.ctx,
                equipment_id=self.equipment.pk,
                kind=WorkOrderKind.CORRECTIVE,
                description="otra",
                fault_ids=[fault.pk],
            )
        self.run_to_execution(order)
        close_order(
            self.ctx,
            order.pk,
            downtime_hours=Decimal("6"),
            failed_part="Bomba de freno",
            failure_cause="Fuga",
        )
        fault.refresh_from_db()
        self.assertEqual(fault.status, FaultStatus.CLOSED)
        self.assertIn("OT-0001", fault.closing_note)

    def test_cancelling_frees_the_faults(self) -> None:
        fault = self.faults()
        order = create_work_order(
            self.ctx,
            equipment_id=self.equipment.pk,
            kind=WorkOrderKind.CORRECTIVE,
            description="Revisar",
            fault_ids=[fault.pk],
        )
        cancel_order(self.ctx, order.pk, reason="Error")
        fault.refresh_from_db()
        self.assertIsNone(fault.work_order_id)
        self.assertEqual(fault.status, FaultStatus.OPEN)

    def test_faults_do_not_go_into_preventive_orders_or_foreign_equipment(self) -> None:
        fault = self.faults()
        with self.assertRaisesMessage(ValidationError, "correctiva"):
            create_work_order(
                self.ctx,
                equipment_id=self.equipment.pk,
                kind=WorkOrderKind.PREVENTIVE,
                description="x",
                fault_ids=[fault.pk],
            )
        with self.assertRaisesMessage(ValidationError, "no existen"):
            create_work_order(
                self.ctx,
                equipment_id=self.equipment.pk,
                kind=WorkOrderKind.CORRECTIVE,
                description="x",
                fault_ids=[uuid.uuid4()],
            )

    # --- indicadores ----------------------------------------------------------

    def test_kpis_availability_mtbf_and_admin_times(self) -> None:
        for number in range(2):
            order = create_work_order(
                self.ctx,
                equipment_id=self.equipment.pk,
                kind=WorkOrderKind.CORRECTIVE,
                description=f"Falla {number}",
                mechanic_ids=[self.mechanic.pk],
            )
            self.run_to_execution(order)
            close_order(
                self.ctx,
                order.pk,
                downtime_hours=Decimal("8"),
                failed_part="Bomba",
                failure_cause="Desgaste",
            )
        start = date.today() - timedelta(days=6)
        for offset in range(5):
            day = start + timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            create_equipment_usage(
                self.ctx,
                equipment_id=self.equipment.pk,
                project_id=self.project.pk,
                operator_id=self.operator.pk,
                usage_date=day,
                start_time=time(8),
                end_time=time(16),
                activity="Acarreo",
                status="completed",
            )
        kpis = maintenance_kpis(
            self.ctx, self.equipment, since=start, until=date.today() + timedelta(days=1)
        )
        self.assertEqual(kpis.corrective_orders, 2)
        self.assertEqual(kpis.downtime_hours, Decimal("16"))
        self.assertGreater(kpis.operating_hours, Decimal("0"))
        self.assertEqual(kpis.mtbf_hours, (kpis.operating_hours / 2).quantize(Decimal("0.01")))
        self.assertEqual(
            kpis.availability_pct,
            ((kpis.scheduled_hours - 16) / kpis.scheduled_hours * 100).quantize(Decimal("0.1")),
        )
        self.assertIsNotNone(kpis.avg_days_to_approve)

    def test_kpis_without_failures_have_no_mtbf(self) -> None:
        kpis = maintenance_kpis(
            self.ctx, self.equipment, since=date.today() - timedelta(days=7), until=date.today()
        )
        self.assertIsNone(kpis.mtbf_hours)
        self.assertEqual(kpis.downtime_hours, Decimal("0"))

    def test_preventive_history_lists_the_last_five_with_mechanics(self) -> None:
        for number in range(6):
            plan = self.meter_plan(name=f"Rutina {number}", last_done_value=Decimal("100"))
            order = self.preventive_order(plan)
            self.run_to_execution(order)
            close_order(
                self.ctx, order.pk, downtime_hours=Decimal("1"), mechanic_ids=[self.mechanic.pk]
            )
        history = preventive_history(self.ctx, self.equipment)
        self.assertEqual(len(history), 5)
        self.assertEqual(history[0].plan.name, "Rutina 5")
        self.assertEqual([m.mechanic.username for m in history[0].mechanics.all()], ["mecanico"])
