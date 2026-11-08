"""Transporte: flujos web (informe, catálogo, liquidación) y API móvil (catálogo y viajes)."""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.test import TestCase

from suno.authn.tests.factories import login, make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import Principal, resolve_request_context
from suno.core.models import LiquidationStatus, Project, Role, TransportSettlement, Trip
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
)
from suno.equipment.transport import (
    create_contractor,
    create_material,
    create_route,
    create_vehicle,
    set_rate,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

WEB = "/plataforma/equipos/transporte/"
WHEN = datetime.now(UTC) - timedelta(days=2)
WHEN_INPUT = WHEN.strftime("%Y-%m-%dT%H:%M")


class _World(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Transp Web")
        self.admin = make_user(self.org, username="gestor", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador", role=Role.FIELD_AGENT)
        self.requester = make_user(self.org, username="solicita", role=Role.FIELD_AGENT)
        self.receiver = make_user(self.org, username="recibe", role=Role.FIELD_AGENT)
        self.outsider = make_user(self.org, username="externo", role=Role.VIEWER)
        ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.ctx = ctx
        project = Project.objects.create(org=self.org, name="Norte", created_by=self.admin)
        etype = create_equipment_type(ctx, code="VOL", name="Volqueta")
        self.equipment = create_equipment(
            ctx, equipment_type_id=etype.pk, code="EQ-1", name="Volqueta propia"
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
        assign_operator(
            ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=project.pk,
            valid_from=date.today() - timedelta(days=5),
        )
        self.gravel = create_material(ctx, name="Gravas")
        self.route = create_route(
            ctx, origin="Cantera", destination="Km 12", distance_km=Decimal("8.5"), zone="Oriente"
        )
        set_rate(
            ctx,
            route_id=self.route.pk,
            material_id=self.gravel.pk,
            valid_from=WHEN.date() - timedelta(days=10),
            price_per_m3=Decimal("20000"),
        )
        self.contractor = create_contractor(ctx, name="Transportes TDR")
        self.truck = create_vehicle(
            ctx, contractor_id=self.contractor.pk, plate="EXG871", capacity_m3=Decimal("14")
        )


@skip_without_db
class TransportWebTests(_World):
    def trip_data(self, **overrides: Any) -> dict[str, Any]:
        data = {
            "vehicle": self.truck.pk,
            "route": self.route.pk,
            "material": self.gravel.pk,
            "volume_m3": "12",
            "recorded_at": WHEN_INPUT,
            "requested_by": self.requester.pk,
            "dispatched_by": self.operator.pk,
            "received_by": self.receiver.pk,
            "driver_name": "Pedro",
        }
        data.update(overrides)
        return data

    def test_manager_builds_the_catalog_from_the_web(self) -> None:
        self.client.force_login(self.admin)
        self.client.post(f"{WEB}materiales/nuevo/", {"name": "Arena"})
        self.client.post(
            f"{WEB}tramos/nuevo/",
            {"origin": "Patio", "destination": "Km 3", "zone": "Sur", "distance_km": "3"},
        )
        self.client.post(f"{WEB}empresas/nueva/", {"name": "Otra empresa", "tax_id": "1"})
        panel = self.client.get(WEB)
        self.assertContains(panel, "Tarifa por m³")
        self.assertContains(panel, "Arena")

    def test_trip_registration_report_and_totals(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(f"{WEB}viajes/nuevo/", self.trip_data())
        self.assertRedirects(response, WEB)
        trip = Trip.objects.get()
        self.assertEqual((trip.code, trip.amount), ("TV-000001", Decimal("240000.00")))
        panel = self.client.get(WEB)
        self.assertContains(panel, "TV-000001")
        self.assertContains(panel, "EXG871")
        self.assertContains(panel, "Oriente")
        self.assertContains(panel, "1 viaje")
        self.assertContains(panel, "Por liquidar")

    def test_trip_needs_exactly_one_vehicle_and_a_different_receiver(self) -> None:
        self.client.force_login(self.admin)
        both = self.client.post(
            f"{WEB}viajes/nuevo/", self.trip_data(equipment=self.equipment.pk), follow=True
        )
        self.assertContains(both, "uno solo")
        same = self.client.post(
            f"{WEB}viajes/nuevo/",
            self.trip_data(received_by=self.operator.pk),
            follow=True,
        )
        self.assertContains(same, "Quien despacha")
        self.assertEqual(Trip.objects.count(), 0)

    def test_operator_registers_but_cannot_manage_or_settle_or_see_costs(self) -> None:
        self.client.force_login(self.operator)
        self.client.post(f"{WEB}viajes/nuevo/", self.trip_data())
        self.assertEqual(Trip.objects.get().created_by_id, self.operator.pk)
        panel = self.client.get(WEB)
        self.assertNotContains(panel, "Nueva liquidación")
        self.assertNotContains(panel, "240.000,00", msg_prefix="el operador no ve costos")
        for path in (
            "materiales/nuevo/",
            "tramos/nuevo/",
            "tarifas/nueva/",
            "liquidaciones/nueva/",
        ):
            self.assertEqual(self.client.get(f"{WEB}{path}").status_code, 403, path)

    def test_filters_narrow_the_report(self) -> None:
        self.client.force_login(self.admin)
        self.client.post(f"{WEB}viajes/nuevo/", self.trip_data())
        self.assertContains(self.client.get(WEB, {"zone": "oriente"}), "TV-000001")
        self.assertNotContains(self.client.get(WEB, {"zone": "Sur"}), "TV-000001")
        self.assertNotContains(self.client.get(WEB, {"state": "settled"}), "TV-000001")
        self.assertContains(self.client.get(WEB, {"contractor": "no-es-uuid"}), "TV-000001")

    def test_settlement_flow_from_the_web(self) -> None:
        self.client.force_login(self.admin)
        self.client.post(f"{WEB}viajes/nuevo/", self.trip_data())
        response = self.client.post(
            f"{WEB}liquidaciones/nueva/",
            {
                "contractor": self.contractor.pk,
                "date_from": (WHEN.date() - timedelta(days=1)).isoformat(),
                "date_to": date.today().isoformat(),
            },
        )
        settlement = TransportSettlement.objects.get()
        self.assertRedirects(response, f"{WEB}liquidaciones/{settlement.pk}/")
        detail = self.client.get(f"{WEB}liquidaciones/{settlement.pk}/")
        self.assertContains(detail, "LF-0001")
        self.assertContains(detail, "EXG871")
        self.assertContains(detail, "Confirmar liquidación")

        self.client.post(
            f"{WEB}liquidaciones/{settlement.pk}/confirmar/", {"advance_applied": "40000"}
        )
        settlement.refresh_from_db()
        self.assertEqual(settlement.status, LiquidationStatus.CONFIRMED)
        self.assertEqual(settlement.net_payable, Decimal("200000.00"))
        self.assertContains(self.client.get(WEB), "Liquidado")
        self.assertNotContains(
            self.client.get(f"{WEB}liquidaciones/{settlement.pk}/"), "Confirmar liquidación"
        )


@skip_without_db
class TransportApiTests(_World):
    def auth(self, username: str) -> dict[str, str]:
        token = login(self.client, username=username).json()["tokens"]
        return {"HTTP_AUTHORIZATION": f"Bearer {token['access_token']}"}

    def body(self, **overrides: Any) -> dict[str, Any]:
        data = {
            "id": str(uuid.uuid4()),
            "recorded_at": WHEN.isoformat().replace("+00:00", "Z"),
            "route_id": str(self.route.pk),
            "material_id": str(self.gravel.pk),
            "volume_m3": "12.5",
            "vehicle_id": str(self.truck.pk),
            "requested_by_id": str(self.requester.pk),
            "dispatched_by_id": str(self.operator.pk),
            "received_by_id": str(self.receiver.pk),
            "driver_name": "Pedro",
        }
        data.update(overrides)
        return data

    def post(self, username: str, body: dict[str, Any]):
        return self.client.post(
            "/v1/transport/trips", body, content_type="application/json", **self.auth(username)
        )

    def test_catalog_lists_routes_materials_vehicles_and_people_with_unchanged(self) -> None:
        body = self.client.get("/v1/transport/catalog", **self.auth("operador")).json()
        self.assertEqual(
            body["routes"],
            [
                {
                    "id": str(self.route.pk),
                    "origin": "Cantera",
                    "destination": "Km 12",
                    "zone": "Oriente",
                    "distance_km": 8.5,
                }
            ],
        )
        self.assertEqual([m["name"] for m in body["materials"]], ["Gravas"])
        self.assertEqual(
            body["vehicles"],
            [
                {
                    "id": str(self.truck.pk),
                    "plate": "EXG871",
                    "contractor": "Transportes TDR",
                    "capacity_m3": 14.0,
                }
            ],
        )
        self.assertIn("operador", {p["name"] for p in body["people"]})
        again = self.client.get(
            "/v1/transport/catalog", {"version": body["version"]}, **self.auth("operador")
        ).json()
        self.assertEqual((again["changed"], again["routes"], again["people"]), (False, [], []))

    def test_catalog_is_empty_without_the_section_and_needs_login(self) -> None:
        body = self.client.get("/v1/transport/catalog", **self.auth("externo")).json()
        self.assertEqual((body["routes"], body["vehicles"], body["people"]), ([], [], []))
        self.assertEqual(self.client.get("/v1/transport/catalog").status_code, 401)

    def test_operator_registers_a_trip_and_retry_is_idempotent(self) -> None:
        payload = self.body()
        first = self.post("operador", payload)
        again = self.post("operador", payload)
        self.assertEqual((first.status_code, again.status_code), (200, 200), first.content)
        self.assertEqual(first.json(), {"id": payload["id"], "code": "TV-000001", "number": 1})
        self.assertEqual(again.json(), first.json())
        trip = Trip.objects.get()
        self.assertEqual((trip.volume_m3, trip.amount), (Decimal("12.50"), Decimal("250000.00")))

    def test_own_equipment_trip(self) -> None:
        body = self.body(vehicle_id=None, equipment_id=str(self.equipment.pk))
        self.assertEqual(self.post("operador", body).status_code, 200)
        self.assertEqual(Trip.objects.get().equipment_id, self.equipment.pk)

    def test_violations_are_unprocessable_and_save_nothing(self) -> None:
        both = self.body(equipment_id=str(self.equipment.pk))
        self.assertEqual(self.post("operador", both).status_code, 422)
        same = self.body(received_by_id=str(self.operator.pk))
        response = self.post("operador", same)
        self.assertEqual(response.status_code, 422)
        self.assertIn("Quien despacha", response.json()["message"])
        too_big = self.body(volume_m3="15")
        self.assertIn("capacidad", self.post("operador", too_big).json()["message"])
        for bad in ({"volume_m3": "0"}, {"volume_m3": "-1"}):
            self.assertEqual(self.post("operador", self.body(**bad)).status_code, 422)
        self.assertEqual(Trip.objects.count(), 0)

    def test_permissions(self) -> None:
        self.assertEqual(self.post("externo", self.body()).status_code, 403)
        unknown = self.body(vehicle_id=None, equipment_id=str(uuid.uuid4()))
        self.assertEqual(self.post("operador", unknown).status_code, 404)
        anonymous = self.client.post(
            "/v1/transport/trips", self.body(), content_type="application/json"
        )
        self.assertEqual(anonymous.status_code, 401)
