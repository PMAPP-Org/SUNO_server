"""Ficha ampliada: departamento, medidores, campos dinámicos y alertas (SINCO M&E §2)."""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    AuditEvent,
    EquipmentDocumentType,
    EquipmentFieldType,
    EquipmentMeterUnit,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.datasheet import (
    create_department,
    define_field,
    equipment_alerts,
    latest_meter_readings,
    record_meter_reading,
    set_field_value,
)
from suno.equipment.services import (
    add_document,
    create_equipment,
    create_equipment_type,
    set_equipment_rate,
    update_equipment,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

PAST = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)


@skip_without_db
class DatasheetTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Ficha")
        self.admin = make_user(self.org, username="gestor", role=Role.ADMIN)
        self.viewer = make_user(self.org, username="lector", role=Role.VIEWER)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.viewer_ctx = resolve_request_context(
            Principal(user_id=self.viewer.pk, org_id=self.org.pk)
        )
        etype = create_equipment_type(self.ctx, code="VOL", name="Volqueta")
        self.equipment = create_equipment(
            self.ctx,
            equipment_type_id=etype.pk,
            code="EQ-9",
            name="Volqueta 9",
            primary_meter_unit=EquipmentMeterUnit.HOURS,
            secondary_meter_unit=EquipmentMeterUnit.KILOMETERS,
        )

    # --- departamento y ficha -------------------------------------------------

    def test_department_is_unique_per_org_and_audited(self) -> None:
        department = create_department(self.ctx, cost_center_code="CC-01", name="Maquinaria Norte")
        self.assertTrue(AuditEvent.objects.filter(action="equipment.department_created").exists())
        with self.assertRaises(ValidationError):
            create_department(self.ctx, cost_center_code="cc-01", name="Otra")
        updated = update_equipment(self.ctx, self.equipment.pk, department=department)
        self.assertEqual(updated.department_id, department.pk)

    def test_department_cannot_cross_organizations(self) -> None:
        other = make_org("Otra")
        other_admin = make_user(other, username="otro", role=Role.ADMIN)
        other_ctx = resolve_request_context(Principal(user_id=other_admin.pk, org_id=other.pk))
        foreign = create_department(other_ctx, cost_center_code="X", name="Ajeno")
        with self.assertRaises(ValidationError):
            update_equipment(self.ctx, self.equipment.pk, department=foreign)

    def test_secondary_meter_must_differ_and_meter_scheme_needs_primary(self) -> None:
        with self.assertRaises(ValidationError):
            update_equipment(
                self.ctx, self.equipment.pk, secondary_meter_unit=EquipmentMeterUnit.HOURS
            )
        with self.assertRaises(ValidationError):
            update_equipment(self.ctx, self.equipment.pk, primary_meter_unit="")

    def test_date_scheme_does_not_need_a_meter(self) -> None:
        updated = update_equipment(
            self.ctx,
            self.equipment.pk,
            control_scheme="date",
            primary_meter_unit="",
            secondary_meter_unit="",
        )
        self.assertEqual(updated.control_scheme, "date")

    def test_fuel_tolerance_bounds(self) -> None:
        with self.assertRaises(ValidationError):
            update_equipment(self.ctx, self.equipment.pk, fuel_tolerance_pct=Decimal("101"))

    def test_rate_stores_km_rate_and_rejects_negative(self) -> None:
        rate = set_equipment_rate(
            self.ctx,
            equipment_id=self.equipment.pk,
            valid_from=date(2026, 11, 1),
            operation_rate=Decimal("100"),
            km_rate=Decimal("2500"),
        )
        self.assertEqual(rate.km_rate, Decimal("2500.00"))
        self.assertEqual(rate.hourly_rate, Decimal("100.00"))
        with self.assertRaises(ValidationError):
            set_equipment_rate(
                self.ctx,
                equipment_id=self.equipment.pk,
                valid_from=date(2026, 12, 1),
                km_rate=Decimal("-1"),
            )

    # --- lecturas -------------------------------------------------------------

    def test_meter_readings_only_advance(self) -> None:
        record_meter_reading(
            self.ctx,
            equipment_id=self.equipment.pk,
            meter_unit="hours",
            value=Decimal("339"),
            read_at=PAST,
        )
        record_meter_reading(
            self.ctx,
            equipment_id=self.equipment.pk,
            meter_unit="hours",
            value=Decimal("345.5"),
            read_at=PAST + timedelta(days=1),
        )
        with self.assertRaisesMessage(ValidationError, "no retroceden"):
            record_meter_reading(
                self.ctx,
                equipment_id=self.equipment.pk,
                meter_unit="hours",
                value=Decimal("100"),
                read_at=PAST + timedelta(days=2),
            )
        with self.assertRaisesMessage(ValidationError, "posterior"):
            record_meter_reading(
                self.ctx,
                equipment_id=self.equipment.pk,
                meter_unit="hours",
                value=Decimal("400"),
                read_at=PAST + timedelta(hours=1),
            )

    def test_latest_readings_follow_configured_meters(self) -> None:
        record_meter_reading(
            self.ctx,
            equipment_id=self.equipment.pk,
            meter_unit="hours",
            value=Decimal("10"),
            read_at=PAST,
        )
        record_meter_reading(
            self.ctx,
            equipment_id=self.equipment.pk,
            meter_unit="kilometers",
            value=Decimal("1200"),
            read_at=PAST,
        )
        snapshots = latest_meter_readings(self.ctx, self.equipment)
        self.assertEqual([s.meter_unit for s in snapshots], ["hours", "kilometers"])
        self.assertEqual(snapshots[1].value, Decimal("1200.00"))

    def test_unconfigured_meter_future_date_and_viewer_are_rejected(self) -> None:
        update_equipment(self.ctx, self.equipment.pk, secondary_meter_unit="")
        with self.assertRaisesMessage(ValidationError, "configurado"):
            record_meter_reading(
                self.ctx,
                equipment_id=self.equipment.pk,
                meter_unit="kilometers",
                value=Decimal("1"),
                read_at=PAST,
            )
        with self.assertRaisesMessage(ValidationError, "futuro"):
            record_meter_reading(
                self.ctx,
                equipment_id=self.equipment.pk,
                meter_unit="hours",
                value=Decimal("1"),
                read_at=datetime.now(UTC) + timedelta(days=2),
            )
        with self.assertRaises(Http404):
            record_meter_reading(
                self.viewer_ctx,
                equipment_id=self.equipment.pk,
                meter_unit="hours",
                value=Decimal("1"),
                read_at=PAST,
            )

    # --- campos dinámicos y alertas ------------------------------------------

    def test_alert_days_only_for_date_fields(self) -> None:
        with self.assertRaises(ValidationError):
            define_field(self.ctx, name="Peso", data_type=EquipmentFieldType.NUMBER, alert_days=5)

    def test_field_values_are_typed_and_upserted(self) -> None:
        number = define_field(self.ctx, name="Capacidad m3", data_type=EquipmentFieldType.NUMBER)
        first = set_field_value(
            self.ctx, equipment_id=self.equipment.pk, definition_id=number.pk, value="14.5"
        )
        again = set_field_value(
            self.ctx, equipment_id=self.equipment.pk, definition_id=number.pk, value="16"
        )
        self.assertEqual(first.pk, again.pk)
        again.refresh_from_db()
        self.assertEqual(again.number_value, Decimal("16"))
        with self.assertRaises(ValidationError):
            set_field_value(
                self.ctx, equipment_id=self.equipment.pk, definition_id=number.pk, value="abc"
            )

    def test_required_field_rejects_empty_value(self) -> None:
        definition = define_field(
            self.ctx, name="Placa", data_type=EquipmentFieldType.TEXT, required=True
        )
        with self.assertRaisesMessage(ValidationError, "obligatorio"):
            set_field_value(
                self.ctx, equipment_id=self.equipment.pk, definition_id=definition.pk, value=""
            )

    def test_alerts_cover_fields_and_documents_ordered_by_urgency(self) -> None:
        today = date(2026, 11, 8)
        soat = define_field(self.ctx, name="SOAT", data_type=EquipmentFieldType.DATE, alert_days=30)
        tecno = define_field(
            self.ctx, name="Tecnomecánica", data_type=EquipmentFieldType.DATE, alert_days=15
        )
        set_field_value(
            self.ctx,
            equipment_id=self.equipment.pk,
            definition_id=soat.pk,
            value=today + timedelta(days=20),
        )
        set_field_value(
            self.ctx,
            equipment_id=self.equipment.pk,
            definition_id=tecno.pk,
            value=today + timedelta(days=40),
        )
        add_document(
            self.ctx,
            equipment_id=self.equipment.pk,
            document_type=EquipmentDocumentType.INSURANCE,
            name="Póliza",
            expires_on=today - timedelta(days=3),
        )
        alerts = equipment_alerts(self.ctx, self.equipment, today=today)
        self.assertEqual([a.name for a in alerts], ["Póliza", "SOAT"])
        self.assertTrue(alerts[0].expired)
        self.assertEqual(alerts[1].days_left, 20)
