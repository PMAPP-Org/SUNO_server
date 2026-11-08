"""Combustibles: tanqueos, tanque propio, rendimiento y liquidación a terceros."""

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
    EquipmentMeterReading,
    FuelFill,
    FuelSource,
    LiquidationStatus,
    Project,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.fuel import (
    confirm_liquidation,
    create_liquidation,
    create_station,
    create_store,
    create_supplier,
    liquidation_preview,
    recalculate_fill_prices,
    record_purchase,
    register_fill,
    set_station_rate,
    store_stock,
    visible_fills,
    yield_report,
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

BASE = datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=40)


def ctx_of(user, org):
    return resolve_request_context(Principal(user_id=user.pk, org_id=org.pk))


@skip_without_db
class FuelTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Combustible")
        self.admin = make_user(self.org, username="gestor", role=Role.ADMIN)
        self.operator = make_user(self.org, username="operador", role=Role.FIELD_AGENT)
        self.ctx = ctx_of(self.admin, self.org)
        project = Project.objects.create(org=self.org, name="Norte", created_by=self.admin)
        etype = create_equipment_type(self.ctx, code="VOL", name="Volqueta")
        self.equipment = create_equipment(
            self.ctx,
            equipment_type_id=etype.pk,
            code="EQ-1",
            name="Volqueta 1",
            primary_meter_unit="kilometers",
            nominal_fuel_yield=Decimal("10"),
            fuel_tolerance_pct=Decimal("10"),
        )
        link_project(
            self.ctx,
            equipment_id=self.equipment.pk,
            project_id=project.pk,
            started_on=date.today() - timedelta(days=60),
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
            valid_from=date.today() - timedelta(days=50),
        )
        self.op_ctx = ctx_of(self.operator, self.org)
        self.supplier = create_supplier(self.ctx, name="Terpel", tax_id="900123")
        self.north = create_station(self.ctx, supplier_id=self.supplier.pk, name="Norte")
        self.south = create_station(self.ctx, supplier_id=self.supplier.pk, name="Sur")
        set_station_rate(
            self.ctx,
            station_id=self.north.pk,
            valid_from=BASE.date() - timedelta(days=30),
            price_per_gallon=Decimal("9000"),
        )

    def fill(self, ctx=None, **overrides):
        values = {
            "fill_id": uuid.uuid4(),
            "equipment_id": self.equipment.pk,
            "source": "station",
            "station_id": self.north.pk,
            "voucher_number": f"V-{uuid.uuid4().hex[:6]}",
            "filled_at": BASE,
            "gallons": Decimal("20"),
            "meter_unit": "kilometers",
            "meter_value": Decimal("1000"),
            "driver_name": "Pedro Pérez",
            "driver_document": "1020",
            "work_front": "Frente 2",
        }
        values.update(overrides)
        return register_fill(ctx or self.op_ctx, **values)

    # --- catálogo -------------------------------------------------------------

    def test_catalog_is_managed_by_the_manager_only(self) -> None:
        with self.assertRaises(PermissionDenied):
            create_supplier(self.op_ctx, name="Otra")
        with self.assertRaises(ValidationError):
            create_supplier(self.ctx, name="terpel")
        with self.assertRaises(ValidationError):
            create_station(self.ctx, supplier_id=self.supplier.pk, name="NORTE")
        self.assertTrue(AuditEvent.objects.filter(action="fuel.catalog_changed").exists())

    def test_station_rates_are_dated_and_immutable(self) -> None:
        with self.assertRaisesMessage(ValidationError, "Ya existe"):
            set_station_rate(
                self.ctx,
                station_id=self.north.pk,
                valid_from=BASE.date() - timedelta(days=30),
                price_per_gallon=Decimal("1"),
            )
        with self.assertRaises(ValidationError):
            set_station_rate(
                self.ctx,
                station_id=self.north.pk,
                valid_from=date.today(),
                price_per_gallon=Decimal("0"),
            )

    # --- tanqueos -------------------------------------------------------------

    def test_station_fill_is_priced_with_the_rate_of_its_date(self) -> None:
        set_station_rate(
            self.ctx,
            station_id=self.north.pk,
            valid_from=BASE.date() + timedelta(days=10),
            price_per_gallon=Decimal("10000"),
        )
        early = self.fill()
        late = self.fill(filled_at=BASE + timedelta(days=11), meter_value=Decimal("1200"))
        self.assertEqual((early.unit_price, early.cost), (Decimal("9000.00"), Decimal("180000.00")))
        self.assertEqual(late.unit_price, Decimal("10000.00"))
        self.assertEqual(early.project.name, "Norte")
        reading = EquipmentMeterReading.objects.filter(source="fuel").order_by("read_at").first()
        self.assertEqual(reading.value, Decimal("1000.00"))
        self.assertTrue(AuditEvent.objects.filter(action="fuel.fill_registered").exists())

    def test_fill_without_a_rate_is_left_unpriced_and_can_be_repriced(self) -> None:
        fill = self.fill(station_id=self.south.pk)
        self.assertIsNone(fill.cost)
        set_station_rate(
            self.ctx,
            station_id=self.south.pk,
            valid_from=BASE.date() - timedelta(days=1),
            price_per_gallon=Decimal("9500"),
        )
        self.assertEqual(recalculate_fill_prices(self.ctx), 1)
        fill.refresh_from_db()
        self.assertEqual(fill.cost, Decimal("190000.00"))
        self.assertEqual(recalculate_fill_prices(self.ctx), 0)

    def test_retry_with_the_same_id_does_not_duplicate_fill_or_meter(self) -> None:
        fill_id = uuid.uuid4()
        first = self.fill(fill_id=fill_id)
        again = self.fill(fill_id=fill_id)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(FuelFill.objects.count(), 1)
        self.assertEqual(EquipmentMeterReading.objects.filter(source="fuel").count(), 1)

    def test_vouchers_do_not_repeat_per_station(self) -> None:
        self.fill(voucher_number="A-1")
        with self.assertRaisesMessage(ValidationError, "ya fue registrado"):
            self.fill(voucher_number="A-1", meter_value=Decimal("1100"))
        self.fill(voucher_number="A-1", station_id=self.south.pk, meter_value=Decimal("1100"))

    def test_validation_rules(self) -> None:
        with self.assertRaisesMessage(ValidationError, "vale"):
            self.fill(voucher_number=" ")
        with self.assertRaisesMessage(ValidationError, "mayores que cero"):
            self.fill(gallons=Decimal("0"))
        with self.assertRaisesMessage(ValidationError, "futuro"):
            self.fill(filled_at=datetime.now(UTC) + timedelta(days=1))
        with self.assertRaisesMessage(ValidationError, "estación"):
            self.fill(station_id=None)
        with self.assertRaisesMessage(ValidationError, "obligatoria"):
            self.fill(meter_unit="", meter_value=None)
        with self.assertRaisesMessage(ValidationError, "estación"):
            self.fill(station_id=uuid.uuid4())
        self.assertEqual(FuelFill.objects.count(), 0)

    def test_meter_cannot_go_backwards_and_the_fill_is_discarded(self) -> None:
        self.fill(meter_value=Decimal("1000"))
        with self.assertRaisesMessage(ValidationError, "no retroceden"):
            self.fill(filled_at=BASE + timedelta(days=1), meter_value=Decimal("900"))
        self.assertEqual(FuelFill.objects.count(), 1)

    def test_equipment_controlled_by_date_does_not_need_a_meter(self) -> None:
        update_equipment(self.ctx, self.equipment.pk, control_scheme="date", primary_meter_unit="")
        fill = self.fill(meter_unit="", meter_value=None)
        self.assertIsNone(fill.meter_value)

    def test_unassigned_operator_cannot_register(self) -> None:
        stranger = make_user(self.org, username="externo", role=Role.VIEWER)
        with self.assertRaises(Http404):
            self.fill(ctx_of(stranger, self.org))

    def test_operator_sees_only_own_fills(self) -> None:
        self.fill()
        self.fill(self.ctx, meter_value=Decimal("1100"), filled_at=BASE + timedelta(days=1))
        self.assertEqual(visible_fills(self.op_ctx).count(), 1)
        self.assertEqual(visible_fills(self.ctx).count(), 2)

    # --- tanque propio --------------------------------------------------------

    def own_store(self):
        store = create_store(self.ctx, name="Tanque 11000")
        record_purchase(
            self.ctx,
            store_id=store.pk,
            bought_on=BASE.date(),
            gallons=Decimal("1000"),
            unit_cost=Decimal("8000"),
        )
        record_purchase(
            self.ctx,
            store_id=store.pk,
            bought_on=BASE.date(),
            gallons=Decimal("500"),
            unit_cost=Decimal("9000"),
        )
        return store

    def test_store_stock_and_weighted_average_cost(self) -> None:
        store = self.own_store()
        stock = store_stock(self.ctx, store)
        self.assertEqual(stock.gallons, Decimal("1500.00"))
        self.assertEqual(stock.average_cost, Decimal("8333.33"))

    def test_issue_decrements_stock_and_is_valued_at_average_cost(self) -> None:
        store = self.own_store()
        fill = self.fill(source="store", station_id=None, store_id=store.pk, gallons=Decimal("60"))
        self.assertEqual((fill.unit_price, fill.cost), (Decimal("8333.33"), Decimal("499999.80")))
        self.assertEqual(store_stock(self.ctx, store).gallons, Decimal("1440.00"))
        self.assertEqual(fill.source, FuelSource.STORE)

    def test_issue_above_stock_is_refused(self) -> None:
        store = self.own_store()
        with self.assertRaisesMessage(ValidationError, "Stock insuficiente"):
            self.fill(source="store", station_id=None, store_id=store.pk, gallons=Decimal("1501"))
        self.assertEqual(store_stock(self.ctx, store).gallons, Decimal("1500.00"))

    def test_store_requires_a_manager_to_buy(self) -> None:
        store = self.own_store()
        with self.assertRaises(PermissionDenied):
            record_purchase(
                self.op_ctx,
                store_id=store.pk,
                bought_on=date.today(),
                gallons=Decimal("1"),
                unit_cost=Decimal("1"),
            )

    # --- rendimiento ----------------------------------------------------------

    def monthly_fills(self):
        # Km 1000 → 1200 → 1500 con 20 y 30 galones: 200/20 = 10 y 300/30 = 10 (mes 1);
        # luego 1650 con 25 galones: 150/25 = 6 (mes siguiente, bajo la tolerancia).
        self.fill(meter_value=Decimal("1000"), gallons=Decimal("20"))
        self.fill(
            filled_at=BASE + timedelta(days=2), meter_value=Decimal("1200"), gallons=Decimal("20")
        )
        self.fill(
            filled_at=BASE + timedelta(days=4), meter_value=Decimal("1500"), gallons=Decimal("30")
        )
        self.fill(
            filled_at=BASE + timedelta(days=35), meter_value=Decimal("1650"), gallons=Decimal("25")
        )

    def test_yield_report_splits_by_month_and_flags_out_of_tolerance(self) -> None:
        self.monthly_fills()
        rows = yield_report(
            self.ctx, self.equipment, since=BASE.date(), until=BASE.date() + timedelta(days=60)
        )
        first, second = rows
        self.assertEqual((first.gallons, first.distance, first.real), (50, 500, Decimal("10.00")))
        self.assertEqual((first.deviation_pct, first.out_of_tolerance), (Decimal("0.0"), False))
        self.assertEqual((second.real, second.deviation_pct), (Decimal("6.00"), Decimal("-40.0")))
        self.assertTrue(second.out_of_tolerance)

    def test_yield_without_enough_fills_or_meter_is_empty(self) -> None:
        self.fill()
        self.assertEqual(
            yield_report(self.ctx, self.equipment, since=BASE.date(), until=date.today()), []
        )
        update_equipment(self.ctx, self.equipment.pk, control_scheme="date", primary_meter_unit="")
        self.equipment.refresh_from_db()
        self.assertEqual(
            yield_report(self.ctx, self.equipment, since=BASE.date(), until=date.today()), []
        )

    # --- liquidación ----------------------------------------------------------

    def liquidation_data(self):
        set_station_rate(
            self.ctx,
            station_id=self.south.pk,
            valid_from=BASE.date() - timedelta(days=30),
            price_per_gallon=Decimal("9500"),
        )
        self.fill(meter_value=Decimal("1000"), gallons=Decimal("20"))
        self.fill(
            station_id=self.south.pk,
            filled_at=BASE + timedelta(days=1),
            meter_value=Decimal("1100"),
            gallons=Decimal("10"),
        )
        self.fill(
            filled_at=BASE + timedelta(days=2),
            meter_value=Decimal("1300"),
            gallons=Decimal("15"),
        )

    def window(self):
        return {
            "supplier_id": self.supplier.pk,
            "date_from": BASE.date(),
            "date_to": BASE.date() + timedelta(days=5),
        }

    def test_preview_groups_by_station_and_equipment_with_totals(self) -> None:
        self.liquidation_data()
        preview = liquidation_preview(self.ctx, **self.window())
        self.assertEqual([b.station.name for b in preview.stations], ["Norte", "Sur"])
        north, south = preview.stations
        self.assertEqual((north.gallons, north.amount), (Decimal("35"), Decimal("315000.00")))
        self.assertEqual((south.gallons, south.amount), (Decimal("10"), Decimal("95000.00")))
        self.assertEqual(north.lines[0].fills, 2)
        self.assertEqual((preview.total_gallons, preview.total_amount), (45, Decimal("410000.00")))
        self.assertEqual(preview.unpriced, 0)

    def test_preview_drops_stations_without_activity(self) -> None:
        create_station(self.ctx, supplier_id=self.supplier.pk, name="Centro")
        self.fill()
        preview = liquidation_preview(self.ctx, **self.window())
        self.assertEqual([b.station.name for b in preview.stations], ["Norte"])

    def test_confirm_links_fills_and_amortizes_the_advance(self) -> None:
        self.liquidation_data()
        liquidation = create_liquidation(self.ctx, **self.window())
        self.assertEqual(liquidation.code, "LQ-0001")
        confirmed = confirm_liquidation(self.ctx, liquidation.pk, advance_applied=Decimal("100000"))
        self.assertEqual(confirmed.status, LiquidationStatus.CONFIRMED)
        self.assertEqual(
            (confirmed.total_gallons, confirmed.total_amount, confirmed.net_payable),
            (Decimal("45"), Decimal("410000.00"), Decimal("310000.00")),
        )
        self.assertEqual(FuelFill.objects.filter(liquidation=confirmed).count(), 3)
        self.assertTrue(AuditEvent.objects.filter(action="fuel.liquidation_confirmed").exists())

        again = create_liquidation(self.ctx, **self.window())
        self.assertEqual(again.number, 2)
        with self.assertRaisesMessage(ValidationError, "No hay tanqueos"):
            confirm_liquidation(self.ctx, again.pk)
        with self.assertRaisesMessage(ValidationError, "ya fue confirmada"):
            confirm_liquidation(self.ctx, confirmed.pk)

    def test_unpriced_fills_block_the_confirmation(self) -> None:
        self.fill(station_id=self.south.pk)
        liquidation = create_liquidation(self.ctx, **self.window())
        self.assertEqual(liquidation_preview(self.ctx, **self.window()).unpriced, 1)
        with self.assertRaisesMessage(ValidationError, "sin tarifa"):
            confirm_liquidation(self.ctx, liquidation.pk)
        liquidation.refresh_from_db()
        self.assertEqual(liquidation.status, LiquidationStatus.DRAFT)

    def test_advance_cannot_exceed_the_total(self) -> None:
        self.fill()
        liquidation = create_liquidation(self.ctx, **self.window())
        with self.assertRaisesMessage(ValidationError, "anticipo"):
            confirm_liquidation(self.ctx, liquidation.pk, advance_applied=Decimal("999999999"))
        with self.assertRaises(ValidationError):
            confirm_liquidation(self.ctx, liquidation.pk, advance_applied=Decimal("-1"))

    def test_liquidation_is_for_cost_viewers_only_and_dates_must_be_ordered(self) -> None:
        with self.assertRaises(PermissionDenied):
            liquidation_preview(self.op_ctx, **self.window())
        with self.assertRaises(PermissionDenied):
            create_liquidation(self.op_ctx, **self.window())
        with self.assertRaises(ValidationError):
            create_liquidation(
                self.ctx,
                supplier_id=self.supplier.pk,
                date_from=date.today(),
                date_to=date.today() - timedelta(days=1),
            )

    def test_other_organizations_cannot_liquidate_this_supplier(self) -> None:
        other_org = make_org("Ajena")
        other = make_user(other_org, username="admin-ajeno", role=Role.ADMIN)
        with self.assertRaises(Http404):
            liquidation_preview(ctx_of(other, other_org), **self.window())
