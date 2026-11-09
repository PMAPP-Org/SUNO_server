"""Flujos web de la ficha ampliada: departamento, medidores y campos con vigencia."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from django.test import TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    Equipment,
    EquipmentFieldDefinition,
    EquipmentFieldValue,
    EquipmentMeterReading,
    MachineryDepartment,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.services import create_equipment, create_equipment_type

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

BASE = "/plataforma/equipos/"


@skip_without_db
class DatasheetWebTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Ficha Web")
        self.admin = make_user(self.org, username="gestor-ficha", role=Role.ADMIN)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        etype = create_equipment_type(self.ctx, code="CAR", name="Cargador")
        self.equipment = create_equipment(
            self.ctx, equipment_type_id=etype.pk, code="EQ-F1", name="Cargador 1"
        )
        self.client.force_login(self.admin)

    def test_department_is_created_and_used_when_editing_the_datasheet(self) -> None:
        created = self.client.post(
            f"{BASE}departamentos/nuevo/", {"cost_center_code": "CC-7", "name": "Maquinaria Sur"}
        )
        self.assertRedirects(created, f"{BASE}nuevo/")
        department = MachineryDepartment.objects.get(org=self.org)
        response = self.client.post(
            f"{BASE}{self.equipment.pk}/editar/",
            {
                "equipment_type": self.equipment.equipment_type_id,
                "department": department.pk,
                "code": "EQ-F1",
                "name": "Cargador 1",
                "ownership": "own",
                "status": "available",
                "control_scheme": "meter",
                "primary_meter_unit": "hours",
                "secondary_meter_unit": "kilometers",
                "work_source": "fuel",
                "nominal_fuel_yield": "4.5",
                "fuel_tolerance_pct": "10",
                "acquisition_value": "250000000",
                "specifications": "{}",
            },
        )
        self.assertRedirects(response, f"{BASE}{self.equipment.pk}/")
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.department_id, department.pk)
        self.assertEqual(self.equipment.secondary_meter_unit, "kilometers")
        detail = self.client.get(f"{BASE}{self.equipment.pk}/")
        self.assertContains(detail, "CC-7 · Maquinaria Sur")
        self.assertContains(detail, "Horómetro + Kilometraje")
        self.assertContains(detail, "$250.000.000,00")

    def test_meter_reading_is_recorded_from_the_web_and_listed(self) -> None:
        Equipment.objects.filter(pk=self.equipment.pk).update(
            primary_meter_unit="hours", secondary_meter_unit=""
        )
        response = self.client.post(
            f"{BASE}{self.equipment.pk}/lecturas/nueva/",
            {"meter_unit": "hours", "value": "339.5", "notes": "Inicio de turno"},
        )
        self.assertRedirects(response, f"{BASE}{self.equipment.pk}/?tab=meters")
        self.assertEqual(EquipmentMeterReading.objects.get().value, 339.5)
        page = self.client.get(f"{BASE}{self.equipment.pk}/?tab=meters")
        self.assertContains(page, "Inicio de turno")
        self.assertContains(page, "Horómetro")

    def test_backwards_reading_is_reported_not_saved(self) -> None:
        url = f"{BASE}{self.equipment.pk}/lecturas/nueva/"
        self.client.post(url, {"meter_unit": "hours", "value": "500"})
        again = self.client.post(url, {"meter_unit": "hours", "value": "100"})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(EquipmentMeterReading.objects.count(), 1)

    def test_field_definition_value_and_alert_show_in_the_datasheet(self) -> None:
        defined = self.client.post(
            f"{BASE}campos/nuevo/",
            {"name": "SOAT", "data_type": "date", "alert_days": "30"},
        )
        self.assertRedirects(defined, BASE)
        definition = EquipmentFieldDefinition.objects.get(org=self.org)
        expiry = date.today() + timedelta(days=12)
        self.client.post(
            f"{BASE}{self.equipment.pk}/campos/valor/",
            {"definition": definition.pk, "value": expiry.isoformat()},
        )
        self.assertEqual(EquipmentFieldValue.objects.get().date_value, expiry)
        detail = self.client.get(f"{BASE}{self.equipment.pk}/?tab=meters")
        self.assertContains(detail, "SOAT")
        self.assertContains(detail, "en 12 días")

    def test_alert_days_for_a_non_date_field_is_rejected(self) -> None:
        response = self.client.post(
            f"{BASE}campos/nuevo/", {"name": "Peso", "data_type": "number", "alert_days": "5"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(EquipmentFieldDefinition.objects.exists())
