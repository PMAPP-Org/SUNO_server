"""Costeo por hora de equipos: aritmética, tarifas, imputación a partidas y permisos."""

from __future__ import annotations

import re
import unittest
from datetime import date, time
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.budgets.tests.factories import make_budget
from visitacampo.core.access import AccessScope, Section, SectionRole
from visitacampo.core.access_services import set_section_grant
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import (
    BudgetItem,
    BudgetItemVersion,
    BudgetRowKind,
    BudgetVersion,
    BudgetVersionStatus,
    EquipmentUsageStatus,
    Project,
    Role,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.equipment.charts import (
    PALETTE,
    build_bullets,
    build_combo,
    build_hbars,
    donut_gradient,
    money_axis,
    month_label,
    nice_ceil,
    percent_axis,
    scale,
    segments,
    share,
    short_money,
)
from visitacampo.equipment.costing import (
    pct_of,
    usage_cost,
    usage_hours,
    utilization_pct,
    weekdays_between,
)
from visitacampo.equipment.services import (
    CostBreakdown,
    assign_operator,
    create_equipment,
    create_equipment_type,
    create_equipment_usage,
    equipment_cost_summary,
    equipment_kpis,
    link_project,
    recalculate_usage_costs,
    set_equipment_rate,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

DAY = date(2026, 11, 3)


class CostingArithmeticTests(SimpleTestCase):
    def test_hours_are_exact_to_two_decimals(self) -> None:
        self.assertEqual(usage_hours(time(7, 0), time(15, 30)), Decimal("8.50"))
        self.assertEqual(usage_hours(time(8, 0), time(8, 20)), Decimal("0.33"))

    def test_cost_rounds_half_up_to_cents(self) -> None:
        self.assertEqual(usage_cost(Decimal("0.33"), Decimal("100.50")), Decimal("33.17"))
        self.assertEqual(usage_cost(Decimal("1.00"), Decimal("0.005")), Decimal("0.01"))

    def test_pct_needs_a_base(self) -> None:
        self.assertEqual(pct_of(Decimal("25"), Decimal("200")), Decimal("12.5"))
        self.assertIsNone(pct_of(Decimal("1"), Decimal("0")))


@skip_without_db
class CostingBase(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Costeo")
        self.admin = make_user(self.org, username="gestor-costeo", role=Role.ADMIN)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        etype = create_equipment_type(self.ctx, code="EXC", name="Excavadora")
        self.equipment = create_equipment(
            self.ctx, equipment_type_id=etype.pk, code="EX-01", name="Excavadora 1"
        )
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.admin)
        link_project(
            self.ctx,
            equipment_id=self.equipment.pk,
            project_id=self.project.pk,
            started_on=date(2026, 10, 1),
        )
        self.operator = make_user(self.org, username="operador-costeo", role=Role.FIELD_AGENT)
        set_section_grant(
            self.ctx,
            user_id=self.operator.pk,
            section=Section.EQUIPMENT,
            role=SectionRole.EQUIPMENT_OPERATOR,
            scope=AccessScope.PROJECTS,
            project_ids=[self.project.pk],
        )
        assign_operator(
            self.ctx,
            equipment_id=self.equipment.pk,
            operator_id=self.operator.pk,
            project_id=self.project.pk,
            valid_from=date(2026, 10, 1),
        )
        self.budget = make_budget(self.project, self.admin)
        self.version = BudgetVersion.objects.create(
            org=self.org,
            budget=self.budget,
            number=1,
            status=BudgetVersionStatus.APPLIED,
            file_name="p.xlsx",
            source_file=b"x",
            uploaded_by=self.admin,
        )
        self.item = BudgetItem.objects.create(org=self.org, budget=self.budget, code="2.1")
        BudgetItemVersion.objects.create(
            org=self.org,
            version=self.version,
            item=self.item,
            kind=BudgetRowKind.ITEM,
            sort_order=0,
            description="Excavación mecánica",
            unit="h",
            quantity=Decimal("100"),
            unit_price=Decimal("500.00"),
            total=Decimal("50000.00"),
        )
        self.budget.contractual_version = self.version
        self.budget.save()

    def use(self, day=DAY, start=time(7, 0), end=time(15, 0), **extra):
        return create_equipment_usage(
            self.ctx,
            equipment_id=self.equipment.pk,
            project_id=self.project.pk,
            operator_id=self.operator.pk,
            usage_date=day,
            start_time=start,
            end_time=end,
            activity="Excavación",
            status=extra.pop("status", EquipmentUsageStatus.COMPLETED),
            **extra,
        )

    def rate(self, value: str, valid_from: date = date(2026, 11, 1)):
        return set_equipment_rate(
            self.ctx,
            equipment_id=self.equipment.pk,
            valid_from=valid_from,
            operation_rate=Decimal(value),
        )


@skip_without_db
class CostingTests(CostingBase):
    def test_usage_is_costed_with_the_rate_in_effect(self) -> None:
        self.rate("100.00", date(2026, 11, 1))
        self.rate("150.00", date(2026, 11, 10))
        before = self.use(date(2026, 11, 3))
        after = self.use(date(2026, 11, 12))
        self.assertEqual(
            (before.billable_hours, before.hourly_rate), (Decimal("8.00"), Decimal("100.00"))
        )
        self.assertEqual(before.cost, Decimal("800.00"))
        self.assertEqual(after.cost, Decimal("1200.00"))

    def test_a_new_rate_does_not_rewrite_history(self) -> None:
        self.rate("100.00", date(2026, 11, 1))
        usage = self.use()
        self.rate("999.00", date(2026, 11, 2))  # retroactiva respecto al uso del 3
        recalculate_usage_costs(self.ctx)
        usage.refresh_from_db()
        self.assertEqual(usage.cost, Decimal("800.00"))

    def test_usage_without_rate_is_flagged_then_recalculated(self) -> None:
        usage = self.use()
        self.assertIsNone(usage.cost)
        self.rate("200.00")
        self.assertEqual(recalculate_usage_costs(self.ctx), 1)
        usage.refresh_from_db()
        self.assertEqual(usage.cost, Decimal("1600.00"))
        self.assertEqual(recalculate_usage_costs(self.ctx), 0)

    def test_rates_are_unique_per_date_and_non_negative(self) -> None:
        self.rate("100.00")
        with self.assertRaises(ValidationError):
            self.rate("120.00")
        with self.assertRaises(ValidationError):
            self.rate("-5.00", date(2026, 12, 1))

    def test_total_rate_is_the_sum_of_components(self) -> None:
        rate = set_equipment_rate(
            self.ctx,
            equipment_id=self.equipment.pk,
            valid_from=DAY,
            ownership_rate=Decimal("10.10"),
            operation_rate=Decimal("20.20"),
            maintenance_rate=Decimal("30.30"),
            labor_rate=Decimal("40.40"),
        )
        self.assertEqual(rate.hourly_rate, Decimal("101.00"))
        usage = self.use(DAY)
        self.assertEqual(usage.rate_id, rate.pk)
        self.assertEqual(usage.cost, Decimal("808.00"))

    def test_budget_item_must_exist_in_the_project(self) -> None:
        self.rate("100.00")
        with self.assertRaises(ValidationError):
            self.use(budget_item_code="9.9")
        usage = self.use(budget_item_code="2.1")
        self.assertEqual(usage.budget_item_id, self.item.pk)

    def test_budget_item_of_another_project_is_rejected(self) -> None:
        other = Project.objects.create(org=self.org, name="Otra", created_by=self.admin)
        foreign = make_budget(other, self.admin, name="Otro", code="O-1")
        BudgetItem.objects.create(org=self.org, budget=foreign, code="7.7")
        with self.assertRaises(ValidationError):
            self.use(budget_item_code="7.7")

    def test_summary_by_equipment_and_by_budget_item(self) -> None:
        self.rate("100.00")
        self.use(date(2026, 11, 3), budget_item_code="2.1")  # 800
        self.use(date(2026, 11, 4), budget_item_code="2.1")  # 800
        self.use(date(2026, 11, 5))  # 800 sin partida
        self.use(date(2026, 11, 6), status=EquipmentUsageStatus.CANCELLED)
        self.use(date(2026, 11, 7), status=EquipmentUsageStatus.SCHEDULED)
        summary = equipment_cost_summary(self.ctx)
        self.assertEqual(summary.total_cost, Decimal("2400.00"))
        self.assertEqual(summary.total_hours, Decimal("24.00"))
        self.assertEqual(summary.unassigned_cost, Decimal("800.00"))
        self.assertEqual(summary.unpriced, 0)
        (row,) = summary.budget_rows
        self.assertEqual(
            (row.code, row.imputed, row.budgeted, row.pct_consumed),
            ("2.1", Decimal("1600.00"), Decimal("50000.00"), Decimal("3.2")),
        )
        projected = equipment_cost_summary(self.ctx, include_scheduled=True)
        self.assertEqual(projected.total_cost, Decimal("3200.00"))

    def test_summary_filters_by_date_and_counts_unpriced(self) -> None:
        self.use(date(2026, 11, 3))
        self.rate("100.00", date(2026, 11, 4))
        self.use(date(2026, 11, 5))
        full = equipment_cost_summary(self.ctx)
        self.assertEqual((full.total_cost, full.unpriced), (Decimal("800.00"), 1))
        ranged = equipment_cost_summary(self.ctx, date_from=date(2026, 11, 5))
        self.assertEqual((ranged.total_cost, ranged.unpriced), (Decimal("800.00"), 0))

    def test_only_the_current_budget_version_is_compared(self) -> None:
        self.rate("100.00")
        self.use(budget_item_code="2.1")
        v2 = BudgetVersion.objects.create(
            org=self.org,
            budget=self.budget,
            number=2,
            status=BudgetVersionStatus.APPLIED,
            file_name="p2.xlsx",
            source_file=b"x",
            uploaded_by=self.admin,
            applied_at=timezone.now(),
        )
        BudgetItemVersion.objects.create(
            org=self.org,
            version=v2,
            item=self.item,
            kind=BudgetRowKind.ITEM,
            sort_order=0,
            description="Excavación mecánica",
            unit="h",
            quantity=Decimal("10"),
            unit_price=Decimal("500.00"),
            total=Decimal("5000.00"),
        )
        (row,) = equipment_cost_summary(self.ctx).budget_rows
        self.assertEqual(row.budgeted, Decimal("5000.00"))

    def test_roles_without_export_cannot_see_costs_or_set_rates(self) -> None:
        operator_ctx = resolve_request_context(
            Principal(user_id=self.operator.pk, org_id=self.org.pk)
        )
        with self.assertRaises(PermissionDenied):
            equipment_cost_summary(operator_ctx)
        with self.assertRaises(PermissionDenied):
            set_equipment_rate(
                operator_ctx,
                equipment_id=self.equipment.pk,
                valid_from=DAY,
                operation_rate=Decimal("1"),
            )

    def test_other_organization_sees_nothing(self) -> None:
        self.rate("100.00")
        self.use()
        other_org = make_org("Otra costeo")
        other_admin = make_user(other_org, username="gestor-otra", role=Role.ADMIN)
        other_ctx = resolve_request_context(Principal(user_id=other_admin.pk, org_id=other_org.pk))
        self.assertEqual(equipment_cost_summary(other_ctx).total_cost, Decimal("0"))


@skip_without_db
class CostingWebTests(CostingBase):
    """Las pantallas muestran costos solo a quien puede verlos."""

    def test_manager_sees_costs_page_rates_tab_and_usage_cost(self) -> None:
        self.rate("100.00")
        self.use(budget_item_code="2.1")
        self.client.force_login(self.admin)
        page = self.client.get("/plataforma/equipos/costos/")
        self.assertContains(page, "Panel de costos de equipos")
        self.assertContains(page, "conic-gradient")
        self.assertContains(page, "cx-plot")
        self.assertContains(page, "800,00")
        self.assertContains(page, "Excavación mecánica")
        self.assertContains(page, "1,6")  # 800 / 50000 = 1,6 %
        tab = self.client.get(f"/plataforma/equipos/{self.equipment.pk}/?tab=rates")
        self.assertContains(tab, "100,00 / hora")
        usage = self.client.get("/plataforma/equipos/usos/")
        self.assertContains(usage, "800,00")

    def test_rate_form_creates_a_rate_and_returns_to_the_rates_tab(self) -> None:
        self.client.force_login(self.admin)
        url = f"/plataforma/equipos/{self.equipment.pk}/tarifas/nueva/"
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(
            url,
            {
                "valid_from": "2026-11-01",
                "ownership_rate": "20.50",
                "operation_rate": "60",
                "maintenance_rate": "15",
                "labor_rate": "25",
                "notes": "Tarifa base",
            },
        )
        self.assertRedirects(
            response,
            f"/plataforma/equipos/{self.equipment.pk}/?tab=rates",
            fetch_redirect_response=False,
        )
        rate = self.equipment.rates.get()
        self.assertEqual(rate.hourly_rate, Decimal("120.50"))
        self.assertEqual(rate.ownership_rate, Decimal("20.50"))

    def test_operator_gets_no_costs_anywhere(self) -> None:
        self.rate("100.00")
        self.use()
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get("/plataforma/equipos/costos/").status_code, 403)
        rate_url = f"/plataforma/equipos/{self.equipment.pk}/tarifas/nueva/"
        self.assertEqual(self.client.get(rate_url).status_code, 403)
        detail = self.client.get(f"/plataforma/equipos/{self.equipment.pk}/?tab=usage")
        self.assertNotContains(detail, "800,00")
        self.assertNotContains(detail, "Tarifas")
        usage = self.client.get("/plataforma/equipos/usos/")
        self.assertNotContains(usage, "800,00")

    def test_recalculate_button_costs_pending_usages(self) -> None:
        usage = self.use()
        self.rate("50.00")
        self.client.force_login(self.admin)
        response = self.client.post("/plataforma/equipos/costos/recalcular/")
        self.assertRedirects(response, "/plataforma/equipos/costos/", fetch_redirect_response=False)
        usage.refresh_from_db()
        self.assertEqual(usage.cost, Decimal("400.00"))


@skip_without_db
class KpiTests(CostingBase):
    TODAY = date(2026, 11, 13)  # viernes: 10 días hábiles del 1 al 13 de nov

    def kpis(self, ctx=None):
        return equipment_kpis(ctx or self.ctx, self.equipment, today=self.TODAY)

    def test_hours_utilization_and_meter_come_from_usage_records(self) -> None:
        self.use(date(2026, 11, 3), start_meter=Decimal("100"), end_meter=Decimal("108"))
        self.use(date(2026, 11, 4), start_meter=Decimal("108"), end_meter=Decimal("116"))
        self.use(date(2026, 10, 20))  # mes anterior: cuenta en el acumulado, no en el mes
        self.use(date(2026, 11, 5), status=EquipmentUsageStatus.CANCELLED)
        self.use(date(2026, 11, 20), status=EquipmentUsageStatus.SCHEDULED)
        kpis = self.kpis()
        self.assertEqual(kpis.hours_month, Decimal("16.00"))
        self.assertEqual(kpis.hours_total, Decimal("24.00"))
        self.assertEqual(kpis.hours_scheduled, Decimal("8.00"))
        self.assertEqual((kpis.usages_total, kpis.days_used_month), (3, 2))
        self.assertEqual(kpis.utilization_month, Decimal("20.0"))  # 16 / (10 d × 8 h)
        self.assertEqual(kpis.last_used_on, date(2026, 11, 4))
        self.assertEqual((kpis.last_meter, kpis.last_meter_unit), (Decimal("116.00"), "Horómetro"))

    def test_costs_and_component_breakdown(self) -> None:
        set_equipment_rate(
            self.ctx,
            equipment_id=self.equipment.pk,
            valid_from=date(2026, 10, 1),
            ownership_rate=Decimal("10"),
            operation_rate=Decimal("20"),
            maintenance_rate=Decimal("5"),
            labor_rate=Decimal("15"),
        )
        self.use(date(2026, 11, 3))  # 8 h × 50 = 400
        self.use(date(2026, 11, 4))
        kpis = self.kpis()
        self.assertEqual(kpis.cost_total, Decimal("800.00"))
        self.assertEqual(kpis.cost_month, Decimal("800.00"))
        self.assertEqual(kpis.cost_per_hour, Decimal("50.00"))
        self.assertEqual(
            kpis.breakdown,
            CostBreakdown(
                ownership=Decimal("160.00"),
                operation=Decimal("320.00"),
                maintenance=Decimal("80.00"),
                labor=Decimal("240.00"),
            ),
        )

    def test_kpis_update_when_a_new_usage_is_recorded(self) -> None:
        self.rate("100.00")
        self.use(date(2026, 11, 3))
        self.assertEqual(self.kpis().hours_total, Decimal("8.00"))
        self.use(date(2026, 11, 4), start=time(8, 0), end=time(10, 30))
        after = self.kpis()
        self.assertEqual(after.hours_total, Decimal("10.50"))
        self.assertEqual(after.cost_total, Decimal("1050.00"))

    def test_costs_are_hidden_from_roles_that_cannot_see_them(self) -> None:
        self.rate("100.00")
        self.use(date(2026, 11, 3))
        operator_ctx = resolve_request_context(
            Principal(user_id=self.operator.pk, org_id=self.org.pk)
        )
        kpis = self.kpis(operator_ctx)
        self.assertEqual(kpis.hours_total, Decimal("8.00"))
        self.assertIsNone(kpis.cost_total)
        self.assertIsNone(kpis.breakdown)

    def test_no_usage_gives_empty_indicators(self) -> None:
        kpis = self.kpis()
        self.assertEqual((kpis.hours_total, kpis.usages_total), (Decimal("0"), 0))
        self.assertIsNone(kpis.last_used_on)
        self.assertIsNone(kpis.cost_per_hour)


class WeekdayTests(SimpleTestCase):
    def test_weekdays_between(self) -> None:
        self.assertEqual(weekdays_between(date(2026, 11, 1), date(2026, 11, 13)), 10)
        self.assertEqual(weekdays_between(date(2026, 11, 7), date(2026, 11, 8)), 0)  # fin de semana
        self.assertEqual(weekdays_between(date(2026, 11, 1), date(2026, 11, 30)), 21)
        self.assertEqual(weekdays_between(date(2026, 11, 5), date(2026, 11, 4)), 0)
        self.assertIsNone(utilization_pct(Decimal("5"), date(2026, 11, 7), date(2026, 11, 8)))


class ChartHelpersTests(SimpleTestCase):
    def test_scale_is_relative_to_the_maximum(self) -> None:
        values = [Decimal("50"), Decimal("200"), Decimal("0")]
        self.assertEqual(scale(values), [Decimal("25.0"), Decimal("100.0"), Decimal("0.0")])
        self.assertEqual(scale([Decimal("0")]), [Decimal("0")])
        self.assertEqual(scale([]), [])

    def test_segments_and_donut_gradient(self) -> None:
        parts = segments([("A", Decimal("75")), ("B", Decimal("25")), ("C", Decimal("0"))])
        self.assertEqual([p.pct for p in parts], [Decimal("75.0"), Decimal("25.0"), Decimal("0.0")])
        gradient = donut_gradient(parts)
        self.assertTrue(gradient.startswith("conic-gradient("))
        self.assertIn("0.00% 75.00%", gradient)
        self.assertIn("75.00% 100.00%", gradient)
        self.assertEqual(
            donut_gradient(segments([("A", Decimal("0"))])), "conic-gradient(#e9e9e7 0 100%)"
        )

    def test_month_label_and_share(self) -> None:
        self.assertEqual(month_label(date(2026, 11, 1)), "Nov-2026")
        self.assertEqual(share(Decimal("1"), Decimal("0")), Decimal("0"))
        self.assertEqual(share(Decimal("1"), Decimal("3")), Decimal("33.3"))


@skip_without_db
class MonthlySummaryTests(CostingBase):
    def test_monthly_series_and_breakdown(self) -> None:
        set_equipment_rate(
            self.ctx,
            equipment_id=self.equipment.pk,
            valid_from=date(2026, 10, 1),
            ownership_rate=Decimal("10"),
            operation_rate=Decimal("40"),
        )
        self.use(date(2026, 10, 20))  # 8 h × 50 = 400
        self.use(date(2026, 11, 3))
        self.use(date(2026, 11, 4))
        summary = equipment_cost_summary(self.ctx)
        self.assertEqual(
            [(m.month, m.hours, m.cost) for m in summary.monthly],
            [
                (date(2026, 10, 1), Decimal("8.00"), Decimal("400.00")),
                (date(2026, 11, 1), Decimal("16.00"), Decimal("800.00")),
            ],
        )
        self.assertEqual(summary.breakdown.ownership, Decimal("240.00"))
        self.assertEqual(summary.breakdown.operation, Decimal("960.00"))


class ComboChartTests(SimpleTestCase):
    def test_nice_ceil_and_formats(self) -> None:
        self.assertEqual(nice_ceil(Decimal("87332000")), Decimal("100000000"))
        self.assertEqual(nice_ceil(Decimal("76")), Decimal("80"))
        self.assertEqual(nice_ceil(Decimal("0")), Decimal("1"))
        self.assertEqual(short_money(Decimal("76444000")), "$76,4 M")
        self.assertEqual(short_money(Decimal("850000")), "$850 mil")
        self.assertEqual(short_money(Decimal("900")), "$900")
        self.assertEqual(month_label(date(2026, 8, 1)), "Ago-2026")

    def test_money_axis_picks_units_and_round_ticks(self) -> None:
        millions = money_axis(Decimal("87332000"))
        self.assertEqual(millions.unit, "COP millones")
        self.assertEqual([label for _, label in millions.ticks], ["0", "25", "50", "75", "100"])
        thousands = money_axis(Decimal("6200"))
        self.assertEqual(thousands.unit, "COP miles")
        self.assertEqual(thousands.ticks[-1][1], "8")
        self.assertEqual(money_axis(Decimal("40")).unit, "COP")

    def test_percent_axis_extends_past_100_when_needed(self) -> None:
        self.assertEqual(percent_axis(Decimal("75.7")).maximum, Decimal("100"))
        over = percent_axis(Decimal("108.2"))
        self.assertEqual(over.maximum, Decimal("125"))
        self.assertEqual(over.ticks[-1][1], "125 %")

    def test_columns_and_cumulative_line_use_their_own_axis(self) -> None:
        rows = [
            ("Ago-2026", Decimal("40"), Decimal("0"), Decimal("5")),
            ("Sep-2026", Decimal("60"), Decimal("20"), Decimal("7")),
        ]
        chart = build_combo(rows)
        first, second = chart.points
        # Eje izquierdo: máximo redondo 80 (60 <= 80); eje derecho: acumulado 100 -> 100.
        self.assertEqual(first.completed_h, Decimal("50.0"))
        self.assertEqual(second.completed_h, Decimal("50.0"))
        self.assertEqual(second.scheduled_h, Decimal("25.0"))
        self.assertEqual((first.cumulative, second.cumulative), (Decimal("40"), Decimal("100")))
        self.assertEqual(first.cumulative_y, Decimal("40.00"))
        self.assertEqual(second.cumulative_y, Decimal("100.00"))
        self.assertEqual((first.x, second.x), (Decimal("25.00"), Decimal("75.00")))
        self.assertEqual(chart.polyline, "25.00,60.00 75.00,0.00")
        self.assertEqual([pos for pos, _ in chart.left.ticks], [0, 25, 50, 75, 100])
        self.assertEqual(chart.left.maximum, Decimal("80"))
        self.assertEqual(chart.right.maximum, Decimal("100"))
        self.assertTrue(second.label_below)  # 100 % del eje derecho: etiqueta bajo el punto
        self.assertFalse(first.label_below)

    def test_short_bars_get_their_label_outside(self) -> None:
        one, two = Decimal("1"), Decimal("0")
        chart = build_combo([("a", Decimal("100"), two, one), ("b", Decimal("5"), two, one)])
        self.assertFalse(chart.points[0].low_bar)
        self.assertTrue(chart.points[1].low_bar)

    def test_empty_series(self) -> None:
        chart = build_combo([])
        self.assertEqual((chart.points, chart.polyline), ([], ""))

    def test_hbars_and_bullets(self) -> None:
        hb = build_hbars(
            [
                ("A", "EQ-1", Decimal("75"), Decimal("3"), 1),
                ("B", "EQ-2", Decimal("25"), Decimal("1"), 2),
            ],
            Decimal("100"),
        )
        self.assertEqual([i.width for i in hb.items], [Decimal("93.8"), Decimal("31.2")])
        self.assertEqual(hb.items[0].share, Decimal("75.0"))
        bl = build_bullets(
            [
                ("2.1", "Excavación", Decimal("50"), Decimal("100")),
                ("2.2", "Transporte", Decimal("108"), Decimal("100")),
                ("9.9", "Sin vigente", Decimal("5"), None),
            ]
        )
        self.assertEqual(bl.axis.maximum, Decimal("125"))
        self.assertEqual([i.over for i in bl.items], [False, True, False])
        self.assertIsNone(bl.items[2].pct)
        self.assertEqual(bl.marker, Decimal("80"))
        self.assertEqual(bl.step, Decimal("20"))


@skip_without_db
class ScheduledToggleTests(CostingBase):
    def test_including_scheduled_changes_totals_and_draws_the_stacked_part(self) -> None:
        self.rate("100.00")
        self.use(date(2026, 11, 3))  # completado: 800
        self.use(date(2026, 11, 20), status=EquipmentUsageStatus.SCHEDULED)  # programado: 800
        self.client.force_login(self.admin)
        without = self.client.get("/plataforma/equipos/costos/")
        with_scheduled = self.client.get("/plataforma/equipos/costos/", {"scheduled": "1"})
        self.assertContains(without, "$800,00")
        self.assertNotContains(without, "is-scheduled")
        self.assertContains(with_scheduled, "$1.600,00")
        self.assertContains(with_scheduled, "is-scheduled")
        self.assertContains(with_scheduled, "incluye $800 programado")
        self.assertContains(with_scheduled, "Figura 1.")
        self.assertContains(with_scheduled, "Fuente: registros de uso de equipos")
        self.assertContains(with_scheduled, "cx-line")
        self.assertContains(with_scheduled, "data-autosubmit")


class PaletteTests(SimpleTestCase):
    def test_default_dashboard_palette(self) -> None:
        self.assertEqual(
            list(PALETTE.values()),
            ["#9e2b2f", "#007a99", "#f28c28", "#6787b7", "#19b7c6", "#3b3b46"],
        )
        parts = segments([(label, Decimal(10)) for label in "ABCD"])
        self.assertEqual([p.color for p in parts], ["#9e2b2f", "#007a99", "#f28c28", "#6787b7"])


@skip_without_db
class CssSafetyTests(CostingBase):
    def test_css_positions_never_use_a_decimal_comma(self) -> None:
        """Con 160 % de consumo el eje llega a 175 %: sus marcas caen en decimales."""

        self.rate("10000.00")  # 8 h x 10.000 = 80.000 contra 50.000 presupuestados
        self.use(budget_item_code="2.1")
        self.client.force_login(self.admin)
        html = self.client.get("/plataforma/equipos/costos/").content.decode()
        self.assertIn("175 %", html)
        styles = re.findall(r'style="([^"]*)"', html)
        self.assertTrue(styles)
        for style in styles:
            with self.subTest(style=style):
                self.assertNotRegex(style, r"\d,\d")
