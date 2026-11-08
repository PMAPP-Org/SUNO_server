"""`GET /v1/fuel/catalog` y `POST /v1/equipment/{id}/fuel-fills`."""

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
from suno.core.models import FuelFill, Project, Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.fuel import (
    create_station,
    create_store,
    create_supplier,
    record_purchase,
    set_station_rate,
)
from suno.equipment.services import (
    assign_operator,
    create_equipment,
    create_equipment_type,
    link_project,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

CATALOG = "/v1/fuel/catalog"
WHEN = (datetime.now(UTC) - timedelta(days=2)).isoformat().replace("+00:00", "Z")


@skip_without_db
class FuelApiTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Comb API")
        self.admin = make_user(self.org, username="gestor", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador", role=Role.FIELD_AGENT)
        self.outsider = make_user(self.org, username="externo", role=Role.VIEWER)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        project = Project.objects.create(org=self.org, name="Norte", created_by=self.admin)
        etype = create_equipment_type(self.ctx, code="VOL", name="Volqueta")
        self.equipment = create_equipment(
            self.ctx,
            equipment_type_id=etype.pk,
            code="EQ-1",
            name="Volqueta 1",
            primary_meter_unit="kilometers",
        )
        link_project(
            self.ctx,
            equipment_id=self.equipment.pk,
            project_id=project.pk,
            started_on=date.today() - timedelta(days=10),
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
            valid_from=date.today() - timedelta(days=5),
        )
        supplier = create_supplier(self.ctx, name="Terpel")
        self.station = create_station(self.ctx, supplier_id=supplier.pk, name="Norte")
        set_station_rate(
            self.ctx,
            station_id=self.station.pk,
            valid_from=date.today() - timedelta(days=30),
            price_per_gallon=Decimal("9000"),
        )
        self.store = create_store(self.ctx, name="Tanque 1")
        record_purchase(
            self.ctx,
            store_id=self.store.pk,
            bought_on=date.today() - timedelta(days=9),
            gallons=Decimal("100"),
            unit_cost=Decimal("8000"),
        )

    def auth(self, username: str) -> dict[str, str]:
        token = login(self.client, username=username).json()["tokens"]
        return {"HTTP_AUTHORIZATION": f"Bearer {token['access_token']}"}

    def post(self, username: str, body: dict[str, Any], equipment_id: Any = None):
        return self.client.post(
            f"/v1/equipment/{equipment_id or self.equipment.pk}/fuel-fills",
            body,
            content_type="application/json",
            **self.auth(username),
        )

    def body(self, **overrides: Any) -> dict[str, Any]:
        data = {
            "id": str(uuid.uuid4()),
            "source": "station",
            "station_id": str(self.station.pk),
            "voucher_number": "V-100",
            "filled_at": WHEN,
            "gallons": "25.5",
            "meter_unit": "kilometers",
            "meter_value": "1000",
            "work_front": "Frente 2",
            "driver_name": "Pedro Pérez",
            "driver_document": "1020",
        }
        data.update(overrides)
        return data

    # --- catálogo -------------------------------------------------------------

    def test_catalog_lists_active_stations_and_tanks_and_answers_unchanged(self) -> None:
        body = self.client.get(CATALOG, **self.auth("operador")).json()
        self.assertEqual(
            body["stations"], [{"id": str(self.station.pk), "name": "Norte", "supplier": "Terpel"}]
        )
        self.assertEqual(body["stores"], [{"id": str(self.store.pk), "name": "Tanque 1"}])
        again = self.client.get(
            CATALOG, {"version": body["version"]}, **self.auth("operador")
        ).json()
        self.assertEqual((again["changed"], again["stations"], again["stores"]), (False, [], []))

    def test_catalog_version_changes_with_the_catalog(self) -> None:
        version = self.client.get(CATALOG, **self.auth("operador")).json()["version"]
        create_station(self.ctx, supplier_id=self.station.supplier_id, name="Sur")
        after = self.client.get(CATALOG, **self.auth("operador")).json()
        self.assertNotEqual(after["version"], version)
        self.assertEqual(len(after["stations"]), 2)

    def test_catalog_is_empty_without_the_equipment_section_and_needs_login(self) -> None:
        body = self.client.get(CATALOG, **self.auth("externo")).json()
        self.assertEqual((body["stations"], body["stores"]), ([], []))
        self.assertEqual(self.client.get(CATALOG).status_code, 401)

    # --- tanqueos -------------------------------------------------------------

    def test_operator_registers_a_station_fill_and_retry_is_idempotent(self) -> None:
        payload = self.body()
        first = self.post("operador", payload)
        again = self.post("operador", payload)
        self.assertEqual((first.status_code, again.status_code), (200, 200), first.content)
        self.assertEqual(first.json(), {"id": payload["id"], "voucher_number": "V-100"})
        self.assertEqual(FuelFill.objects.count(), 1)
        fill = FuelFill.objects.get()
        self.assertEqual((fill.gallons, fill.cost), (Decimal("25.50"), Decimal("229500.00")))
        self.assertEqual(fill.created_by_id, self.operator.pk)

    def test_operator_issues_from_the_own_tank_and_stock_is_enforced(self) -> None:
        issue = self.body(
            source="store", station_id=None, store_id=str(self.store.pk), gallons="60"
        )
        self.assertEqual(self.post("operador", issue).status_code, 200)
        too_much = self.body(
            source="store",
            station_id=None,
            store_id=str(self.store.pk),
            gallons="60",
            voucher_number="V-101",
            meter_value="1100",
        )
        response = self.post("operador", too_much)
        self.assertEqual(response.status_code, 422)
        self.assertIn("Stock insuficiente", response.json()["message"])

    def test_validation_errors_are_unprocessable_and_save_nothing(self) -> None:
        self.assertEqual(self.post("operador", self.body()).status_code, 200)
        duplicate = self.post("operador", self.body(meter_value="1100"))
        self.assertEqual(duplicate.status_code, 422)
        self.assertIn("ya fue registrado", duplicate.json()["message"])
        no_meter = self.post(
            "operador", self.body(voucher_number="V-2", meter_unit=None, meter_value=None)
        )
        self.assertEqual(no_meter.status_code, 422)
        self.assertEqual(FuelFill.objects.count(), 1)

    def test_schema_rejects_bad_amounts_and_unknown_origin(self) -> None:
        for bad in (
            {"gallons": "0"},
            {"gallons": "-3"},
            {"source": "barril"},
            {"meter_value": "-1"},
        ):
            self.assertEqual(self.post("operador", self.body(**bad)).status_code, 422, bad)

    def test_permissions_and_visibility(self) -> None:
        self.assertEqual(self.post("externo", self.body()).status_code, 404)
        self.assertEqual(self.post("operador", self.body(), uuid.uuid4()).status_code, 404)
        client = self.client.post(
            f"/v1/equipment/{self.equipment.pk}/fuel-fills",
            self.body(),
            content_type="application/json",
        )
        self.assertEqual(client.status_code, 401)
