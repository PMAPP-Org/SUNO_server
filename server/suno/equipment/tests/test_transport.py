"""Transporte de materiales: viajes, tarifas, informe y liquidación de fletes."""

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
from suno.core.models import AuditEvent, LiquidationStatus, Project, Role, Trip
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.services import (
    assign_operator,
    create_equipment,
    create_equipment_type,
    link_project,
    update_equipment,
)
from suno.equipment.transport import (
    confirm_settlement,
    create_contractor,
    create_material,
    create_route,
    create_settlement,
    create_vehicle,
    recalculate_trip_prices,
    register_trip,
    set_rate,
    settlement_preview,
    trip_report,
    visible_trips,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

WHEN = datetime.now(UTC) - timedelta(days=3)


def ctx_of(user, org):
    return resolve_request_context(Principal(user_id=user.pk, org_id=org.pk))


@skip_without_db
class TransportTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Transporte")
        self.admin = make_user(self.org, username="gestor", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador", role=Role.FIELD_AGENT)
        self.requester = make_user(self.org, username="solicita", role=Role.FIELD_AGENT)
        self.receiver = make_user(self.org, username="recibe", role=Role.FIELD_AGENT)
        self.ctx = ctx_of(self.admin, self.org)
        project = Project.objects.create(org=self.org, name="Norte", created_by=self.admin)
        etype = create_equipment_type(self.ctx, code="VOL", name="Volqueta")
        self.equipment = create_equipment(
            self.ctx, equipment_type_id=etype.pk, code="EQ-1", name="Volqueta propia"
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
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=project.pk,
            valid_from=date.today() - timedelta(days=20),
        )
        self.op_ctx = ctx_of(self.operator, self.org)
        self.gravel = create_material(self.ctx, name="Gravas")
        self.debris = create_material(self.ctx, name="Derrumbes")
        self.east = create_route(
            self.ctx,
            origin="Cantera",
            destination="Km 12",
            distance_km=Decimal("8.5"),
            zone="Oriente",
        )
        self.west = create_route(
            self.ctx,
            origin="Cantera",
            destination="Km 3",
            distance_km=Decimal("3"),
            zone="Occidente",
        )
        for route, price in ((self.east, "20000"), (self.west, "12000")):
            set_rate(
                self.ctx,
                route_id=route.pk,
                material_id=self.gravel.pk,
                valid_from=WHEN.date() - timedelta(days=30),
                price_per_m3=Decimal(price),
            )
        self.contractor = create_contractor(self.ctx, name="Transportes TDR", tax_id="800111")
        self.truck = create_vehicle(
            self.ctx, contractor_id=self.contractor.pk, plate="exg871", capacity_m3=Decimal("14")
        )

    def trip(self, ctx=None, **overrides):
        values = {
            "trip_id": uuid.uuid4(),
            "recorded_at": WHEN,
            "route_id": self.east.pk,
            "material_id": self.gravel.pk,
            "volume_m3": Decimal("12"),
            "requested_by_id": self.requester.pk,
            "dispatched_by_id": self.operator.pk,
            "received_by_id": self.receiver.pk,
            "vehicle_id": self.truck.pk,
            "driver_name": "Pedro",
        }
        if "equipment_id" in overrides:
            values.pop("vehicle_id")
        values.update(overrides)
        return register_trip(ctx or self.op_ctx, **values)

    # --- catálogo -------------------------------------------------------------

    def test_catalog_is_for_managers_and_names_are_unique(self) -> None:
        with self.assertRaises(PermissionDenied):
            create_material(self.op_ctx, name="Arena")
        with self.assertRaises(ValidationError):
            create_material(self.ctx, name="gravas")
        with self.assertRaises(ValidationError):
            create_route(self.ctx, origin="cantera", destination="km 12", distance_km=Decimal("1"))
        with self.assertRaises(ValidationError):
            create_route(self.ctx, origin="A", destination="B", distance_km=Decimal("0"))
        with self.assertRaises(ValidationError):
            create_vehicle(self.ctx, contractor_id=self.contractor.pk, plate="EXG871")
        self.assertEqual(self.truck.plate, "EXG871")
        self.assertTrue(AuditEvent.objects.filter(action="transport.catalog_changed").exists())

    def test_rates_are_dated_and_immutable(self) -> None:
        with self.assertRaisesMessage(ValidationError, "Ya existe"):
            set_rate(
                self.ctx,
                route_id=self.east.pk,
                material_id=self.gravel.pk,
                valid_from=WHEN.date() - timedelta(days=30),
                price_per_m3=Decimal("1"),
            )
        with self.assertRaises(ValidationError):
            set_rate(
                self.ctx,
                route_id=self.east.pk,
                material_id=self.debris.pk,
                valid_from=date.today(),
                price_per_m3=Decimal("0"),
            )

    # --- viajes ---------------------------------------------------------------

    def test_trip_of_third_party_truck_snapshots_distance_price_and_sequence(self) -> None:
        first = self.trip()
        second = self.trip(route_id=self.west.pk, volume_m3=Decimal("10"))
        self.assertEqual((first.code, second.code), ("TV-000001", "TV-000002"))
        self.assertEqual(
            (first.distance_km, first.unit_price, first.amount),
            (Decimal("8.50"), Decimal("20000.00"), Decimal("240000.00")),
        )
        self.assertEqual(
            (second.distance_km, second.amount), (Decimal("3.00"), Decimal("120000.00"))
        )
        self.assertTrue(AuditEvent.objects.filter(action="transport.trip_registered").exists())

    def test_a_later_rate_does_not_rewrite_the_history(self) -> None:
        trip = self.trip()
        set_rate(
            self.ctx,
            route_id=self.east.pk,
            material_id=self.gravel.pk,
            valid_from=date.today(),
            price_per_m3=Decimal("99999"),
        )
        trip.refresh_from_db()
        self.assertEqual(trip.unit_price, Decimal("20000.00"))
        self.assertEqual(recalculate_trip_prices(self.ctx), 0)

    def test_trip_with_own_equipment_gets_the_project(self) -> None:
        trip = self.trip(equipment_id=self.equipment.pk)
        self.assertEqual((trip.equipment_id, trip.vehicle_id), (self.equipment.pk, None))
        self.assertEqual(trip.project.name, "Norte")

    def test_trip_without_a_rate_is_unpriced_and_can_be_repriced(self) -> None:
        trip = self.trip(material_id=self.debris.pk)
        self.assertIsNone(trip.amount)
        set_rate(
            self.ctx,
            route_id=self.east.pk,
            material_id=self.debris.pk,
            valid_from=WHEN.date() - timedelta(days=1),
            price_per_m3=Decimal("15000"),
        )
        self.assertEqual(recalculate_trip_prices(self.ctx), 1)
        trip.refresh_from_db()
        self.assertEqual(trip.amount, Decimal("180000.00"))

    def test_retry_with_the_same_id_does_not_duplicate(self) -> None:
        trip_id = uuid.uuid4()
        first = self.trip(trip_id=trip_id)
        again = self.trip(trip_id=trip_id)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(Trip.objects.count(), 1)

    def test_exactly_one_vehicle_is_required(self) -> None:
        with self.assertRaisesMessage(ValidationError, "solo uno"):
            self.trip(equipment_id=self.equipment.pk, vehicle_id=self.truck.pk)
        with self.assertRaisesMessage(ValidationError, "solo uno"):
            self.trip(vehicle_id=None)

    def test_three_users_are_required_and_dispatcher_is_not_receiver(self) -> None:
        with self.assertRaisesMessage(ValidationError, "Quien despacha"):
            self.trip(received_by_id=self.operator.pk)
        stranger = make_user(make_org("Otra"), username="ajeno", role=Role.FIELD_AGENT)
        with self.assertRaisesMessage(ValidationError, "usuarios de la organización"):
            self.trip(requested_by_id=stranger.pk)
        self.assertEqual(Trip.objects.count(), 0)

    def test_validation_rules(self) -> None:
        with self.assertRaisesMessage(ValidationError, "mayor que cero"):
            self.trip(volume_m3=Decimal("0"))
        with self.assertRaisesMessage(ValidationError, "capacidad"):
            self.trip(volume_m3=Decimal("14.01"))
        with self.assertRaisesMessage(ValidationError, "futuro"):
            self.trip(recorded_at=datetime.now(UTC) + timedelta(days=1))
        with self.assertRaisesMessage(ValidationError, "tramo o el material"):
            self.trip(route_id=uuid.uuid4())
        with self.assertRaisesMessage(ValidationError, "vehículo"):
            self.trip(vehicle_id=uuid.uuid4())
        update_equipment(self.ctx, self.equipment.pk, status="retired")
        with self.assertRaisesMessage(ValidationError, "retirado"):
            self.trip(equipment_id=self.equipment.pk)

    def test_unassigned_user_cannot_register_for_own_equipment_but_can_for_third_party(
        self,
    ) -> None:
        stranger = make_user(self.org, username="externo", role=Role.VIEWER)
        with self.assertRaises(PermissionDenied):
            self.trip(ctx_of(stranger, self.org))
        with self.assertRaises(Http404):
            self.trip(ctx_of(stranger, self.org), equipment_id=self.equipment.pk)

    def test_operator_sees_only_own_trips(self) -> None:
        self.trip()
        self.trip(self.ctx, received_by_id=self.receiver.pk, dispatched_by_id=self.admin.pk)
        self.assertEqual(visible_trips(self.op_ctx).count(), 1)
        self.assertEqual(visible_trips(self.ctx).count(), 2)

    # --- informe --------------------------------------------------------------

    def test_report_totals_and_filters(self) -> None:
        self.trip(volume_m3=Decimal("12"))
        self.trip(route_id=self.west.pk, volume_m3=Decimal("10"))
        self.trip(material_id=self.debris.pk, volume_m3=Decimal("5"))
        other = create_vehicle(
            self.ctx,
            contractor_id=create_contractor(self.ctx, name="Otra empresa").pk,
            plate="ABC123",
        )
        self.trip(vehicle_id=other.pk, volume_m3=Decimal("7"))

        everything = trip_report(self.ctx)
        self.assertEqual((everything.count, everything.total_m3), (4, Decimal("34")))
        self.assertEqual(everything.unpriced, 1)
        self.assertEqual(everything.by_zone[0], ("Oriente", Decimal("24")))
        self.assertEqual(trip_report(self.ctx, zone="oriente").count, 3)
        self.assertEqual(trip_report(self.ctx, contractor_id=self.contractor.pk).count, 3)
        self.assertEqual(trip_report(self.ctx, material_id=self.debris.pk).total_m3, Decimal("5"))
        self.assertEqual(trip_report(self.ctx, settled=True).count, 0)
        self.assertEqual(
            trip_report(self.ctx, date_from=date.today(), date_to=date.today()).count, 0
        )

    # --- liquidación ----------------------------------------------------------

    def window(self):
        return {
            "contractor_id": self.contractor.pk,
            "date_from": WHEN.date() - timedelta(days=1),
            "date_to": WHEN.date() + timedelta(days=1),
        }

    def priced_trips(self):
        self.trip(volume_m3=Decimal("12"))
        self.trip(volume_m3=Decimal("10"))
        self.trip(route_id=self.west.pk, volume_m3=Decimal("14"))

    def test_preview_sums_m3_and_routes_per_vehicle(self) -> None:
        self.priced_trips()
        self.trip(equipment_id=self.equipment.pk)  # flota propia: no se liquida
        preview = settlement_preview(self.ctx, **self.window())
        (block,) = preview.vehicles
        self.assertEqual(
            (block.vehicle.plate, block.m3, block.amount),
            ("EXG871", Decimal("36"), Decimal("608000.00")),
        )
        east, west = block.lines
        self.assertEqual(
            (east.trips, east.m3, east.amount), (2, Decimal("22"), Decimal("440000.00"))
        )
        self.assertEqual((west.trips, west.amount), (1, Decimal("168000.00")))
        self.assertEqual((preview.total_m3, preview.unpriced), (Decimal("36"), 0))

    def test_confirm_links_trips_and_amortizes_the_advance(self) -> None:
        self.priced_trips()
        settlement = create_settlement(self.ctx, **self.window())
        self.assertEqual(settlement.code, "LF-0001")
        done = confirm_settlement(self.ctx, settlement.pk, advance_applied=Decimal("100000"))
        self.assertEqual(done.status, LiquidationStatus.CONFIRMED)
        self.assertEqual(
            (done.total_m3, done.total_amount, done.net_payable),
            (Decimal("36"), Decimal("608000.00"), Decimal("508000.00")),
        )
        self.assertEqual(Trip.objects.filter(settlement=done).count(), 3)
        self.assertEqual(trip_report(self.ctx, settled=True).count, 3)
        self.assertEqual(settlement_preview(self.ctx, **self.window()).vehicles, [])
        again = create_settlement(self.ctx, **self.window())
        self.assertEqual(again.number, 2)
        with self.assertRaisesMessage(ValidationError, "No hay viajes"):
            confirm_settlement(self.ctx, again.pk)
        with self.assertRaisesMessage(ValidationError, "ya fue confirmada"):
            confirm_settlement(self.ctx, done.pk)
        self.assertTrue(AuditEvent.objects.filter(action="transport.settlement_confirmed").exists())

    def test_unpriced_trips_block_the_confirmation(self) -> None:
        self.trip(material_id=self.debris.pk)
        settlement = create_settlement(self.ctx, **self.window())
        self.assertEqual(settlement_preview(self.ctx, **self.window()).unpriced, 1)
        with self.assertRaisesMessage(ValidationError, "sin tarifa"):
            confirm_settlement(self.ctx, settlement.pk)

    def test_advance_bounds(self) -> None:
        self.trip()
        settlement = create_settlement(self.ctx, **self.window())
        with self.assertRaisesMessage(ValidationError, "anticipo"):
            confirm_settlement(self.ctx, settlement.pk, advance_applied=Decimal("999999999"))
        with self.assertRaises(ValidationError):
            confirm_settlement(self.ctx, settlement.pk, advance_applied=Decimal("-1"))

    def test_settlement_permissions_and_isolation(self) -> None:
        with self.assertRaises(PermissionDenied):
            settlement_preview(self.op_ctx, **self.window())
        with self.assertRaises(PermissionDenied):
            create_settlement(self.op_ctx, **self.window())
        with self.assertRaises(ValidationError):
            create_settlement(
                self.ctx,
                contractor_id=self.contractor.pk,
                date_from=date.today(),
                date_to=date.today() - timedelta(days=1),
            )
        other_org = make_org("Ajena")
        other = make_user(other_org, username="admin-ajeno", role=Role.ADMIN)
        with self.assertRaises(Http404):
            settlement_preview(ctx_of(other, other_org), **self.window())
