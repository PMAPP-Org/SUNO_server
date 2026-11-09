"""Registros de campo desde la web, con un usuario operador de equipo."""

from __future__ import annotations

import unittest
from datetime import UTC, date, timedelta
from decimal import Decimal

from django.test import TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    EquipmentInspection,
    EquipmentMeterReading,
    EquipmentUsage,
    FuelFill,
    Project,
    Role,
    Trip,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.datasheet import record_meter_reading
from suno.equipment.fuel import create_station, create_supplier, set_station_rate
from suno.equipment.inspections import define_checklist_item
from suno.equipment.services import (
    assign_operator,
    create_equipment,
    create_equipment_type,
    link_project,
    set_equipment_rate,
)
from suno.equipment.transport import (
    create_contractor,
    create_material,
    create_route,
    create_vehicle,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

BASE = "/plataforma/equipos/"
YESTERDAY = date.today() - timedelta(days=1)


@skip_without_db
class OperatorWebTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Operador Web")
        self.admin = make_user(self.org, username="gestor-ow", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador-ow", role=Role.FIELD_AGENT)
        self.viewer = make_user(self.org, username="lector-ow", role=Role.VIEWER)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.north = Project.objects.create(org=self.org, name="Norte", created_by=self.admin)
        self.south = Project.objects.create(org=self.org, name="Sur", created_by=self.admin)
        etype = create_equipment_type(self.ctx, code="VOL", name="Volqueta")
        self.etype = etype
        self.equipment = create_equipment(
            self.ctx,
            equipment_type_id=etype.pk,
            code="EQ-OW",
            name="Volqueta asignada",
            primary_meter_unit="hours",
        )
        self.other = create_equipment(
            self.ctx, equipment_type_id=etype.pk, code="EQ-AJENO", name="Equipo no asignado"
        )
        for equipment in (self.equipment, self.other):
            link_project(
                self.ctx,
                equipment_id=equipment.pk,
                project_id=self.north.pk,
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
        set_equipment_rate(
            self.ctx,
            equipment_id=self.equipment.pk,
            valid_from=date.today() - timedelta(days=60),
            operation_rate=Decimal("100000"),
        )
        from datetime import datetime

        record_meter_reading(
            self.ctx,
            equipment_id=self.equipment.pk,
            meter_unit="hours",
            value=Decimal("500"),
            read_at=datetime.now(UTC) - timedelta(days=5),
        )
        self.brakes = define_checklist_item(
            self.ctx, equipment_type_id=etype.pk, label="Frenos", critical=True
        )
        self.lights = define_checklist_item(self.ctx, equipment_type_id=etype.pk, label="Luces")
        self.client.force_login(self.operator)

    # --- ficha --------------------------------------------------------------

    def test_detail_offers_every_record_the_operator_can_create(self) -> None:
        page = self.client.get(f"{BASE}{self.equipment.pk}/")
        self.assertContains(page, "Nuevo registro")
        self.assertContains(page, "Reportar mi uso")
        self.assertContains(page, f"{BASE}{self.equipment.pk}/preoperacional/nuevo/")
        self.assertContains(page, "Lectura de medidor")
        self.assertContains(page, f"combustible/tanqueo/nuevo/?equipment={self.equipment.pk}")
        self.assertContains(page, f"transporte/viajes/nuevo/?equipment={self.equipment.pk}")

    def test_a_viewer_does_not_get_the_registration_block(self) -> None:
        reviewer = make_user(self.org, username="solo-lee", role=Role.VIEWER)
        set_section_grant(
            self.ctx,
            user_id=reviewer.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.VIEWER,
            scope=AccessScope.ORGANIZATION,
        )
        self.client.force_login(reviewer)
        self.assertNotContains(self.client.get(f"{BASE}{self.equipment.pk}/"), "Nuevo registro")

    def test_usage_list_has_the_report_button_for_operators(self) -> None:
        self.assertContains(self.client.get(f"{BASE}usos/"), "Reportar mi uso")
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(f"{BASE}usos/"), "Reportar mi uso")

    # --- uso ----------------------------------------------------------------

    def usage_data(self, **overrides):
        data = {
            "equipment": self.equipment.pk,
            "project": self.north.pk,
            "usage_date": YESTERDAY.isoformat(),
            "start_time": "08:00",
            "end_time": "12:00",
            "activity": "Excavación de zanja",
            "location": "Frente 2",
            "start_meter": "500",
            "end_meter": "504",
        }
        data.update(overrides)
        return data

    def test_operator_reports_usage_from_the_web(self) -> None:
        form = self.client.get(f"{BASE}uso/reportar/?equipment={self.equipment.pk}")
        self.assertContains(form, "Reportar mi uso del equipo")
        self.assertContains(form, "Norte")
        self.assertNotContains(form, "EQ-AJENO")
        response = self.client.post(f"{BASE}uso/reportar/", self.usage_data())
        self.assertRedirects(response, f"{BASE}{self.equipment.pk}/?tab=usage")
        usage = EquipmentUsage.objects.get()
        self.assertEqual((usage.status, usage.operator_id), ("completed", self.operator.pk))
        self.assertEqual(
            (usage.billable_hours, usage.cost), (Decimal("4.00"), Decimal("400000.00"))
        )
        self.assertEqual(
            EquipmentMeterReading.objects.filter(source="usage").get().value, Decimal("504.00")
        )
        self.assertContains(
            self.client.get(f"{BASE}{self.equipment.pk}/?tab=usage"), "Excavación de zanja"
        )

    def test_usage_with_overlap_or_missing_meter_is_explained_not_saved(self) -> None:
        self.client.post(f"{BASE}uso/reportar/", self.usage_data())
        clash = self.client.post(
            f"{BASE}uso/reportar/",
            self.usage_data(
                start_time="11:00", end_time="13:00", start_meter="504", end_meter="506"
            ),
            follow=True,
        )
        self.assertContains(clash, "ya tiene otro uso")
        no_meter = self.client.post(
            f"{BASE}uso/reportar/",
            self.usage_data(start_time="14:00", end_time="15:00", start_meter="", end_meter=""),
            follow=True,
        )
        self.assertContains(no_meter, "obligatorias")
        self.assertEqual(EquipmentUsage.objects.count(), 1)

    def test_operator_cannot_report_for_an_unassigned_equipment_or_project(self) -> None:
        response = self.client.post(
            f"{BASE}uso/reportar/", self.usage_data(equipment=self.other.pk)
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Escoja una opción válida")
        no_project = self.client.post(
            f"{BASE}uso/reportar/", self.usage_data(project=self.south.pk)
        )
        self.assertEqual(no_project.status_code, 200)
        self.assertEqual(EquipmentUsage.objects.count(), 0)

    # --- preoperacional ------------------------------------------------------

    def preop_data(self, **overrides):
        data = {
            "meter_value": "505",
            f"ok_{self.brakes.pk}": "yes",
            f"ok_{self.lights.pk}": "yes",
        }
        data.update(overrides)
        return data

    def test_operator_submits_a_clean_preoperational(self) -> None:
        url = f"{BASE}{self.equipment.pk}/preoperacional/nuevo/"
        page = self.client.get(url)
        self.assertContains(page, "Frenos · crítico")
        self.assertContains(page, "Luces")
        self.assertContains(page, "Lectura inicial de horómetro")
        response = self.client.post(url, self.preop_data())
        inspection = EquipmentInspection.objects.get()
        self.assertRedirects(response, f"{BASE}inspecciones/{inspection.pk}/")
        self.assertFalse(inspection.has_faults)
        self.assertEqual(inspection.operator_id, self.operator.pk)
        self.assertContains(self.client.get(f"{BASE}inspecciones/{inspection.pk}/"), "Frenos")

    def test_a_failed_item_needs_a_note_and_answers_are_kept(self) -> None:
        url = f"{BASE}{self.equipment.pk}/preoperacional/nuevo/"
        bad = self.client.post(
            url,
            self.preop_data(**{f"ok_{self.brakes.pk}": "no", "notes": "Revisar hoy"}),
            follow=True,
        )
        self.assertContains(bad, "Describe la novedad")
        self.assertContains(bad, "Revisar hoy")
        self.assertRegex(bad.content.decode(), rf'name="ok_{self.brakes.pk}" value="no" checked')
        self.assertEqual(EquipmentInspection.objects.count(), 0)
        ok = self.client.post(
            url,
            self.preop_data(
                **{f"ok_{self.brakes.pk}": "no", f"note_{self.brakes.pk}": "Pedal blando"}
            ),
        )
        self.assertEqual(ok.status_code, 302)
        self.assertTrue(EquipmentInspection.objects.get().has_faults)

    def test_preoperational_requires_every_answer_and_the_meter(self) -> None:
        url = f"{BASE}{self.equipment.pk}/preoperacional/nuevo/"
        data = self.preop_data()
        del data[f"ok_{self.lights.pk}"]
        self.assertContains(self.client.post(url, data, follow=True), "Responde todos los ítems")
        self.assertContains(
            self.client.post(url, self.preop_data(meter_value=""), follow=True), "lectura inicial"
        )
        self.assertContains(
            self.client.post(url, self.preop_data(meter_value="abc"), follow=True),
            "debe ser un número",
        )
        self.assertEqual(EquipmentInspection.objects.count(), 0)

    def test_equipment_without_checklist_explains_it(self) -> None:
        bare = create_equipment_type(self.ctx, code="SIN", name="Sin lista")
        equipment = create_equipment(
            self.ctx, equipment_type_id=bare.pk, code="EQ-SL", name="Sin lista de chequeo"
        )
        link_project(
            self.ctx,
            equipment_id=equipment.pk,
            project_id=self.north.pk,
            started_on=date.today() - timedelta(days=5),
        )
        assign_operator(
            self.ctx,
            equipment_id=equipment.pk,
            operator_id=self.operator.pk,
            project_id=self.north.pk,
            valid_from=date.today() - timedelta(days=5),
        )
        self.assertContains(
            self.client.get(f"{BASE}{equipment.pk}/preoperacional/nuevo/"),
            "no tiene lista de chequeo",
        )
        self.assertNotContains(self.client.get(f"{BASE}{equipment.pk}/"), "preoperacional/nuevo/")

    def test_unassigned_equipment_is_not_found_for_the_operator(self) -> None:
        self.assertEqual(
            self.client.get(f"{BASE}{self.other.pk}/preoperacional/nuevo/").status_code, 404
        )

    # --- lectura, tanqueo y viaje -------------------------------------------

    def test_operator_records_a_meter_reading_from_the_web(self) -> None:
        response = self.client.post(
            f"{BASE}{self.equipment.pk}/lecturas/nueva/", {"meter_unit": "hours", "value": "510"}
        )
        self.assertRedirects(response, f"{BASE}{self.equipment.pk}/?tab=meters")
        self.assertEqual(
            EquipmentMeterReading.objects.get(value=Decimal("510")).value, Decimal("510.00")
        )

    def test_fuel_form_only_offers_assigned_equipment_and_preselects_the_one_from_the_sheet(
        self,
    ) -> None:
        supplier = create_supplier(self.ctx, name="Terpel")
        station = create_station(self.ctx, supplier_id=supplier.pk, name="Norte")
        set_station_rate(
            self.ctx,
            station_id=station.pk,
            valid_from=date.today() - timedelta(days=30),
            price_per_gallon=Decimal("9000"),
        )
        form = self.client.get(f"{BASE}combustible/tanqueo/nuevo/?equipment={self.equipment.pk}")
        self.assertContains(form, "EQ-OW")
        self.assertNotContains(form, "EQ-AJENO")
        self.assertRegex(form.content.decode(), rf'<option value="{self.equipment.pk}" selected')
        data = {
            "equipment": self.equipment.pk,
            "source": "station",
            "station": station.pk,
            "fuel_type": "acpm",
            "voucher_number": "V-1",
            "gallons": "20",
            "meter_unit": "hours",
            "meter_value": "520",
        }
        self.assertEqual(
            self.client.post(f"{BASE}combustible/tanqueo/nuevo/", data).status_code, 302
        )
        self.assertEqual(FuelFill.objects.get().created_by_id, self.operator.pk)
        foreign = self.client.post(
            f"{BASE}combustible/tanqueo/nuevo/",
            {**data, "equipment": self.other.pk, "voucher_number": "V-2"},
        )
        self.assertEqual(foreign.status_code, 200)
        self.assertEqual(FuelFill.objects.count(), 1)

    def test_operator_registers_a_trip_with_the_assigned_equipment(self) -> None:
        material = create_material(self.ctx, name="Gravas")
        route = create_route(
            self.ctx,
            origin="Cantera",
            destination="Km 12",
            distance_km=Decimal("8.5"),
            zone="Oriente",
        )
        helper = make_user(self.org, username="recibe-ow", role=Role.FIELD_AGENT)
        create_vehicle(
            self.ctx,
            contractor_id=create_contractor(self.ctx, name="TDR").pk,
            plate="EXG871",
        )
        form = self.client.get(f"{BASE}transporte/viajes/nuevo/?equipment={self.equipment.pk}")
        self.assertNotContains(form, "EQ-AJENO")
        data = {
            "equipment": self.equipment.pk,
            "route": route.pk,
            "material": material.pk,
            "volume_m3": "10",
            "requested_by": helper.pk,
            "dispatched_by": self.operator.pk,
            "received_by": helper.pk,
        }
        same = self.client.post(
            f"{BASE}transporte/viajes/nuevo/",
            {**data, "received_by": self.operator.pk},
            follow=True,
        )
        self.assertContains(same, "Quien despacha")
        ok = self.client.post(
            f"{BASE}transporte/viajes/nuevo/", {**data, "received_by": self.admin.pk}
        )
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(Trip.objects.get().equipment_id, self.equipment.pk)
