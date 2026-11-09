"""Flujos web de combustibles: catálogo, tanqueo, liquidación y rendimiento."""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    FuelFill,
    FuelLiquidation,
    FuelStation,
    FuelStationRate,
    FuelStore,
    FuelSupplier,
    LiquidationStatus,
    Project,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.fuel import record_purchase
from suno.equipment.services import (
    assign_operator,
    create_equipment,
    create_equipment_type,
    link_project,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

BASE = "/plataforma/equipos/combustible/"
PAST = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")


@skip_without_db
class FuelWebTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Comb Web")
        self.admin = make_user(self.org, username="gestor-cw", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador-cw", role=Role.FIELD_AGENT)
        ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.ctx = ctx
        project = Project.objects.create(org=self.org, name="Norte", created_by=self.admin)
        etype = create_equipment_type(ctx, code="VOL", name="Volqueta")
        self.equipment = create_equipment(
            ctx,
            equipment_type_id=etype.pk,
            code="EQ-CW",
            name="Volqueta CW",
            primary_meter_unit="kilometers",
            nominal_fuel_yield=Decimal("10"),
            fuel_tolerance_pct=Decimal("10"),
        )
        link_project(
            ctx,
            equipment_id=self.equipment.pk,
            project_id=project.pk,
            started_on=date.today() - timedelta(days=30),
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
            valid_from=date.today() - timedelta(days=20),
        )

    def catalog(self):
        self.client.force_login(self.admin)
        self.client.post(f"{BASE}proveedores/nuevo/", {"name": "Terpel", "tax_id": "900123"})
        supplier = FuelSupplier.objects.get()
        self.client.post(f"{BASE}estaciones/nueva/", {"supplier": supplier.pk, "name": "Norte"})
        return supplier, FuelStation.objects.get()

    def fill_data(self, station, **overrides):
        data = {
            "equipment": self.equipment.pk,
            "source": "station",
            "station": station.pk,
            "fuel_type": "acpm",
            "voucher_number": "V-1",
            "filled_at": PAST,
            "gallons": "20",
            "meter_unit": "kilometers",
            "meter_value": "1000",
            "work_front": "Frente 2",
            "driver_name": "Pedro",
        }
        data.update(overrides)
        return data

    def test_manager_builds_the_catalog_and_the_rate_prices_pending_fills(self) -> None:
        _, station = self.catalog()
        self.client.post(f"{BASE}tanqueo/nuevo/", self.fill_data(station))
        fill = FuelFill.objects.get()
        self.assertIsNone(fill.cost)

        response = self.client.post(
            f"{BASE}tarifas/nueva/",
            {
                "station": station.pk,
                "valid_from": (date.today() - timedelta(days=30)).isoformat(),
                "price_per_gallon": "9000",
            },
        )
        self.assertRedirects(response, BASE)
        fill.refresh_from_db()
        self.assertEqual(fill.cost, Decimal("180000.00"))
        self.assertEqual(FuelStationRate.objects.count(), 1)
        panel = self.client.get(BASE)
        self.assertContains(panel, "EQ-CW")
        self.assertContains(panel, "vale V-1")
        self.assertContains(panel, "Por liquidar")

    def test_operator_registers_a_fill_but_cannot_manage_catalog_or_liquidate(self) -> None:
        _, station = self.catalog()
        self.client.force_login(self.operator)
        self.client.post(f"{BASE}tanqueo/nuevo/", self.fill_data(station))
        self.assertEqual(FuelFill.objects.get().created_by_id, self.operator.pk)
        panel = self.client.get(BASE)
        self.assertNotContains(panel, "Nueva liquidación")
        self.assertNotContains(panel, "Proveedor</a>")
        self.assertNotContains(panel, "Por liquidar", msg_prefix="el operador no ve costos")
        for path in ("proveedores/nuevo/", "estaciones/nueva/", "tarifas/nueva/", "compras/nueva/"):
            self.assertEqual(self.client.get(f"{BASE}{path}").status_code, 403, path)
        self.assertEqual(self.client.get(f"{BASE}liquidaciones/nueva/").status_code, 403)

    def test_duplicate_voucher_is_reported_and_not_saved(self) -> None:
        _, station = self.catalog()
        self.client.post(f"{BASE}tanqueo/nuevo/", self.fill_data(station))
        response = self.client.post(
            f"{BASE}tanqueo/nuevo/", self.fill_data(station, meter_value="1100"), follow=True
        )
        self.assertContains(response, "ya fue registrado")
        self.assertEqual(FuelFill.objects.count(), 1)

    def test_own_tank_purchase_and_issue_show_the_stock(self) -> None:
        self.client.force_login(self.admin)
        self.client.post(f"{BASE}tanques/nuevo/", {"name": "Tanque 11000"})
        store = FuelStore.objects.get()
        self.client.post(
            f"{BASE}compras/nueva/",
            {
                "store": store.pk,
                "bought_on": date.today().isoformat(),
                "gallons": "1000",
                "unit_cost": "8000",
            },
        )
        self.client.post(
            f"{BASE}tanqueo/nuevo/",
            {
                "equipment": self.equipment.pk,
                "source": "store",
                "store": store.pk,
                "fuel_type": "acpm",
                "voucher_number": "S-1",
                "filled_at": PAST,
                "gallons": "60",
                "meter_unit": "kilometers",
                "meter_value": "500",
            },
        )
        panel = self.client.get(BASE)
        self.assertContains(panel, "Tanque 11000")
        self.assertContains(panel, "940")
        self.assertEqual(FuelFill.objects.get().cost, Decimal("480000.00"))

    def test_liquidation_flow_from_the_web(self) -> None:
        supplier, station = self.catalog()
        record = record_purchase  # silencia importación usada solo por otras pruebas
        self.assertTrue(callable(record))
        self.client.post(
            f"{BASE}tarifas/nueva/",
            {
                "station": station.pk,
                "valid_from": (date.today() - timedelta(days=30)).isoformat(),
                "price_per_gallon": "9000",
            },
        )
        self.client.post(f"{BASE}tanqueo/nuevo/", self.fill_data(station))
        response = self.client.post(
            f"{BASE}liquidaciones/nueva/",
            {
                "supplier": supplier.pk,
                "date_from": (date.today() - timedelta(days=10)).isoformat(),
                "date_to": date.today().isoformat(),
            },
        )
        liquidation = FuelLiquidation.objects.get()
        self.assertRedirects(response, f"{BASE}liquidaciones/{liquidation.pk}/")
        detail = self.client.get(f"{BASE}liquidaciones/{liquidation.pk}/")
        self.assertContains(detail, "LQ-0001")
        self.assertContains(detail, "EQ-CW")
        self.assertContains(detail, "Confirmar liquidación")

        self.client.post(
            f"{BASE}liquidaciones/{liquidation.pk}/confirmar/", {"advance_applied": "50000"}
        )
        liquidation.refresh_from_db()
        self.assertEqual(liquidation.status, LiquidationStatus.CONFIRMED)
        self.assertEqual(liquidation.net_payable, Decimal("130000.00"))
        done = self.client.get(f"{BASE}liquidaciones/{liquidation.pk}/")
        self.assertContains(done, "130.000,00")
        self.assertNotContains(done, "Confirmar liquidación")
        self.assertContains(self.client.get(BASE), "Liquidado")

    def test_liquidation_with_unpriced_fills_cannot_be_confirmed(self) -> None:
        supplier, station = self.catalog()
        self.client.post(f"{BASE}tanqueo/nuevo/", self.fill_data(station))
        self.client.post(
            f"{BASE}liquidaciones/nueva/",
            {
                "supplier": supplier.pk,
                "date_from": (date.today() - timedelta(days=10)).isoformat(),
                "date_to": date.today().isoformat(),
            },
        )
        liquidation = FuelLiquidation.objects.get()
        self.assertContains(self.client.get(f"{BASE}liquidaciones/{liquidation.pk}/"), "sin tarifa")
        self.client.post(
            f"{BASE}liquidaciones/{liquidation.pk}/confirmar/", {"advance_applied": "0"}
        )
        liquidation.refresh_from_db()
        self.assertEqual(liquidation.status, LiquidationStatus.DRAFT)

    def test_yield_page_lists_months_and_flags_the_deviation(self) -> None:
        _, station = self.catalog()
        for n, (km, gal) in enumerate([("1000", "20"), ("1100", "20"), ("1300", "30")]):
            when = (datetime.now(UTC) - timedelta(days=5 - n)).strftime("%Y-%m-%dT%H:%M")
            self.client.post(
                f"{BASE}tanqueo/nuevo/",
                self.fill_data(
                    station, voucher_number=f"V-{n}", filled_at=when, meter_value=km, gallons=gal
                ),
            )
        page = self.client.get(
            f"{BASE.replace('combustible/', '')}{self.equipment.pk}/rendimiento/"
        )
        self.assertContains(page, "Rendimiento de combustible")
        self.assertContains(page, "fuera de tolerancia")
        self.assertContains(page, "vs nominal")

    def test_yield_without_data_explains_itself(self) -> None:
        self.client.force_login(self.admin)
        page = self.client.get(
            f"{BASE.replace('combustible/', '')}{self.equipment.pk}/rendimiento/"
        )
        self.assertContains(page, "al menos dos")
