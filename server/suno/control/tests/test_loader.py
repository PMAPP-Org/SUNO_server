"""Cargador del control (costos, pieza 5): queries fijas y qué entra."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from suno.budgets.tests.factories import apply_rows, make_budget
from suno.budgets.tests.samples import V1_ROWS, V2_ROWS
from suno.control.actual_cost import AdminLine, ChapterRef
from suno.control.earned_value import ControlInput, Execution, PercentTask, TaskWindow, compute
from suno.control.loader import load_control_input
from suno.control.periods import MONTH
from suno.core.models import (
    Budget,
    CostScope,
    CostStatus,
    EquipmentOwnership,
    EquipmentUsageStatus,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import cost_world, entry, equipment, trip, usage
from suno.matrix.tests.factories import (
    budget_world,
    import_schedule,
    item,
    locate,
    plan,
    quantity_record,
    quantity_row,
    suno_levels,
)
from suno.projects.tests.factories import T0, make_field_world, make_record
from suno.projects.tests.samples import v2

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
CUT = date(2026, 3, 20)


@skip_without_db
class LoaderTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        self.ctx = self.world.ctx()
        suno_levels(self.world)
        locate(self.world, 3, "Tramo 1")
        self.concrete = item(self.budget, "2.1")
        plan(self.world, 3, self.concrete, "4")
        plan(self.world, 5, self.concrete, "8")
        self.world.approve(quantity_record(self.world, 5, quantity_row(self.concrete, "2")))

    def load(self, cut: date = CUT) -> ControlInput:
        return load_control_input(self.ctx, self.world.project, cut=cut, kind=MONTH)

    def test_query_count_does_not_grow_with_the_data(self) -> None:
        with self.assertNumQueries(9):
            self.load()
        location = item(self.budget, "1.1")
        plan(self.world, 2, location, "50")
        for hours in (1, 2, 3):
            self.world.approve(
                quantity_record(self.world, 2, quantity_row(location, "5"), hours=hours)
            )
        with self.assertNumQueries(9):
            self.load()

    def test_windows_items_and_executions(self) -> None:
        data = self.load()
        task = self.world.task(3)
        self.assertEqual(
            data.tasks[task.pk],
            TaskWindow(
                task_id=task.pk,
                label="1.2 Descapote",
                location="Tramo 1",
                start=date(2026, 3, 9),
                finish=date(2026, 3, 13),
                from_baseline=True,
                milestone=False,
                order=task.sort_order,
            ),
        )
        priced = data.items[self.concrete.pk]
        self.assertEqual(
            (priced.label, priced.unit, priced.unit_price, priced.chapter),
            ("2.1 Concreto zapatas", "m3", D("450000"), "2 Estructura"),
        )
        self.assertEqual(
            data.executions,
            [Execution(self.world.task(5).pk, self.concrete.pk, date(2026, 3, 10), D("2"))],
        )
        self.assertEqual(sorted(p.planned for p in data.plans), [D("4"), D("8")])
        self.assertTrue(data.has_budget)
        self.assertEqual(data.contractual_direct_cost, D("9130000.00"))
        self.assertFalse(data.calendar.is_working(date(2026, 3, 23)))  # festivo del XML

    def test_only_live_validated_records_until_the_cut(self) -> None:
        row = quantity_row(self.concrete, "1")
        quantity_record(self.world, 5, row, hours=1)  # sin revisar
        self.world.reject(quantity_record(self.world, 5, row, hours=2))
        self.world.approve(
            make_record(
                self.world.org,
                self.world.agent,
                task=self.world.task(5),
                at=T0,
                deleted=True,
                quantities=[row],
            )
        )
        self.world.approve(quantity_record(self.world, 5, row, hours=24 * 30))  # 9 de abril
        # 21 de marzo a las 03:00 UTC: en Bogotá todavía es el 20.
        self.world.approve(
            quantity_record(self.world, 5, quantity_row(self.concrete, "3"), hours=258)
        )
        days = sorted((e.day, e.quantity) for e in self.load().executions)
        self.assertEqual(days, [(date(2026, 3, 10), D("2")), (date(2026, 3, 20), D("3"))])

    def test_current_prices_and_retired_items(self) -> None:
        apply_rows(self.ctx, self.budget, V2_ROWS)
        data = self.load()
        self.assertEqual(data.items[self.concrete.pk].unit_price, D("470000"))
        retired = item(self.budget, "1.2")
        self.assertNotIn(retired.pk, data.items)
        self.assertEqual(data.item_labels[retired.pk], "1.2")
        self.assertEqual(data.contractual_direct_cost, D("9130000.00"))

    def test_several_budgets_name_their_items(self) -> None:
        other = make_budget(self.world.project, self.world.admin, name="Interventoría", code="C-02")
        apply_rows(self.ctx, other, V1_ROWS)
        data = self.load()
        self.assertEqual(data.items[self.concrete.pk].chapter, "2 Estructura · Contrato de obra")
        self.assertEqual(data.contractual_direct_cost, D("18260000.00"))

    def test_several_budgets_name_the_missing_chapter(self) -> None:
        other = make_budget(self.world.project, self.world.admin, name="Interventoría", code="C-02")
        apply_rows(self.ctx, other, [["0.1", "Global inicial", "gl", 1, 1000, 1000], *V1_ROWS])
        data = self.load()
        self.assertEqual(data.items[item(other, "0.1").pk].chapter, "Sin capítulo · Interventoría")

    def test_contractual_reference_counts_its_budgets(self) -> None:
        data = self.load()
        self.assertEqual((data.contractual_budgets, data.applied_budgets), (1, 1))
        other = make_budget(self.world.project, self.world.admin, name="Interventoría", code="C-02")
        apply_rows(self.ctx, other, V1_ROWS)
        Budget.objects.filter(pk=other.pk).update(contractual_version=None)
        data = self.load()
        self.assertEqual(data.contractual_direct_cost, D("9130000.00"))
        self.assertEqual((data.contractual_budgets, data.applied_budgets), (1, 2))

    def test_retired_tasks_are_left_out(self) -> None:
        import_schedule(self.world, v2())
        self.assertNotIn(self.world.task(5).pk, self.load().tasks)

    def test_percent_tasks_are_the_leaves_without_plan(self) -> None:
        self.world.approve(self.world.record(2, "40"))
        self.assertEqual(self.load().percent_tasks, [PercentTask("1.1 Localización", D("40"))])

    def test_percent_tasks_use_the_last_validated_pct_until_the_cut(self) -> None:
        self.world.approve(self.world.record(2, "40"))
        self.world.approve(self.world.record(2, "90", hours=24 * 15))  # 25 de marzo
        self.assertEqual(self.load().percent_tasks, [PercentTask("1.1 Localización", D("40"))])
        self.assertEqual(
            self.load(date(2026, 3, 9)).percent_tasks, [PercentTask("1.1 Localización", None)]
        )

    def test_project_without_budget(self) -> None:
        world = make_field_world("Sin presupuesto")
        ctx = world.ctx()
        with self.assertNumQueries(8):
            data = load_control_input(ctx, world.project, cut=CUT, kind=MONTH)
        self.assertFalse(data.has_budget)
        self.assertEqual(data.items, {})
        self.assertEqual(data.plans, [])
        self.assertIsNone(data.contractual_direct_cost)


@skip_without_db
class CostLoaderTests(TestCase):
    """Costos, pieza 6a: cinco consultas más, solo con `costs=True`."""

    def setUp(self) -> None:
        self.world, self.budget = cost_world()
        self.ctx = self.world.ctx()
        self.concrete = item(self.budget, "2.1")
        self.steel = item(self.budget, "2.2")
        self.structure = item(self.budget, "2")
        plan(self.world, 5, self.concrete, "8")

    def load(self, *, costs: bool = True) -> ControlInput:
        return load_control_input(self.ctx, self.world.project, cut=CUT, kind=MONTH, costs=costs)

    def test_query_count_does_not_grow_with_the_data(self) -> None:
        with self.assertNumQueries(14):
            self.load()
        machine = equipment(self.world)
        for day in range(2, 12):
            entry(self.world, day=date(2026, 3, day), status=CostStatus.APPROVED)
            usage(self.world, machine, date(2026, 3, day), "100.00", budget_item=self.concrete)
            trip(self.world, date(2026, 3, day), "50.00")
        entry(
            self.world,
            status=CostStatus.APPROVED,
            scope=CostScope.SELECTION,
            chapter=self.structure,
            items=(self.concrete, self.steel),
        )
        with CaptureQueriesContext(connection) as queries:
            self.load()
        self.assertEqual(len(queries.captured_queries), 14)
        # El combustible y el mantenimiento ya están en la tarifa horaria: no se leen.
        tables = " ".join(query["sql"] for query in queries.captured_queries)
        self.assertNotIn('"fuel_', tables)
        self.assertNotIn('"work_order', tables)
        with self.assertNumQueries(9):
            self.assertIsNone(self.load(costs=False).costs)

    def test_ledger_lines_drafts_and_automatic_costs(self) -> None:
        task = self.world.task(5)
        entry(
            self.world,
            amount="1000.00",
            status=CostStatus.APPROVED,
            scope=CostScope.ITEM,
            items=(self.concrete,),
            chapter=self.structure,
            task=task,
        )
        entry(
            self.world,
            day=date(2026, 3, 11),
            amount="500.00",
            status=CostStatus.APPROVED,
            scope=CostScope.SELECTION,
            chapter=self.structure,
            items=(self.concrete, self.steel),
        )
        entry(self.world, day=date(2026, 3, 12), amount="700.00")  # borrador
        entry(self.world, day=date(2026, 3, 12), status=CostStatus.REJECTED)
        entry(self.world, day=date(2026, 4, 2), status=CostStatus.APPROVED)  # tras el corte
        machine = equipment(self.world)
        usage(self.world, machine, date(2026, 3, 10), "800.00", budget_item=self.concrete)
        usage(self.world, machine, date(2026, 3, 10), "200.00")
        usage(self.world, machine, date(2026, 3, 11), None)
        usage(
            self.world, machine, date(2026, 3, 11), "50.00", status=EquipmentUsageStatus.SCHEDULED
        )
        rented = equipment(self.world, "AL-01", ownership=EquipmentOwnership.RENTED)
        usage(self.world, rented, date(2026, 3, 10), "999.00")
        trip(self.world, date(2026, 3, 8), "1400.00")
        trip(self.world, date(2026, 3, 8), None)
        trip(self.world, date(2026, 3, 8), "999.00", own=machine)
        costs = self.load().costs
        assert costs is not None
        ledger = sorted(
            (line.day, line.amount, line.scope, line.items, line.chapter_id, line.task_id)
            for line in costs.lines
            if line.origin == "ledger"
        )
        self.assertEqual(
            ledger,
            [
                (
                    date(2026, 3, 10),
                    D("1000.00"),
                    "item",
                    (self.concrete.pk,),
                    self.structure.pk,
                    task.pk,
                ),
                (
                    date(2026, 3, 11),
                    D("500.00"),
                    "selection",
                    tuple(sorted((self.concrete.pk, self.steel.pk))),
                    self.structure.pk,
                    None,
                ),
            ],
        )
        automatic = sorted(
            (line.origin, line.day, line.amount, line.scope, line.items)
            for line in costs.lines
            if line.origin != "ledger"
        )
        self.assertEqual(
            automatic,
            [
                ("equipment", date(2026, 3, 10), D("200.00"), "unassigned", ()),
                ("equipment", date(2026, 3, 10), D("800.00"), "item", (self.concrete.pk,)),
                ("transport", date(2026, 3, 8), D("1400.00"), "unassigned", ()),
            ],
        )
        self.assertEqual((costs.pending_count, costs.pending_amount), (1, D("700.00")))
        self.assertEqual((costs.unpriced_usages, costs.unpriced_trips), (1, 1))

    def test_chapters_admin_and_budget_of_each_item(self) -> None:
        data = self.load()
        costs = data.costs
        assert costs is not None
        self.assertEqual(
            costs.chapters[self.structure.pk],
            ChapterRef(
                "2 Estructura",
                "2 Estructura",
                0,
                frozenset({self.concrete.pk, self.steel.pk, item(self.budget, "2.4").pk}),
            ),
        )
        # Administración 10 % del costo directo: 913.000 / 9.130.000.
        self.assertEqual(
            list(costs.admin), [AdminLine(self.budget.pk, "Contrato de obra", D("0.1"))]
        )
        self.assertEqual(data.items[self.concrete.pk].budget_id, self.budget.pk)
        self.assertIsNotNone(compute(data).cost)

    def test_project_without_budget(self) -> None:
        world = make_field_world("Sin presupuesto en costos")
        ctx = world.ctx()  # resolver el contexto cuesta dos consultas: fuera de la cuenta
        with self.assertNumQueries(12):
            data = load_control_input(ctx, world.project, cut=CUT, kind=MONTH, costs=True)
        assert data.costs is not None
        self.assertEqual((list(data.costs.admin), dict(data.costs.chapters)), ([], {}))
