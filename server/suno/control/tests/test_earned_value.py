"""Cálculo del valor ganado (costos, pieza 5). Sin BD."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from suno.control.earned_value import (
    ControlInput,
    ControlResult,
    Execution,
    OutsidePlan,
    Overrun,
    PercentTask,
    PlanPair,
    PricedItem,
    TaskWindow,
    UnpricedPair,
    compute,
    spi_of,
    status_of,
)
from suno.control.periods import MONTH, WEEK
from suno.projects.workdays import CalendarException, WorkCalendar

D = Decimal
T1, T2, T3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
EXC, CONC, OTHER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
MON = date(2026, 3, 2)
FRI = date(2026, 3, 13)
CUT = date(2026, 3, 31)


def window(
    task_id: uuid.UUID = T1,
    start: date | None = MON,
    finish: date | None = FRI,
    *,
    label: str = "1.1 Excavación",
    location: str = "Tramo 1",
    from_baseline: bool = True,
    milestone: bool = False,
    order: int = 1,
) -> TaskWindow:
    return TaskWindow(
        task_id=task_id,
        label=label,
        location=location,
        start=start,
        finish=finish,
        from_baseline=from_baseline,
        milestone=milestone,
        order=order,
    )


def priced(
    item_id: uuid.UUID = EXC,
    price: str = "1000",
    *,
    label: str = "1.1 Excavación mecánica",
    chapter: str = "1 Preliminares",
) -> PricedItem:
    return PricedItem(item_id=item_id, label=label, unit="m3", unit_price=D(price), chapter=chapter)


CONCRETE = priced(CONC, "450000", label="2.1 Concreto zapatas", chapter="2 Estructura")


def plan(task_id: uuid.UUID = T1, item_id: uuid.UUID = EXC, planned: str = "100") -> PlanPair:
    return PlanPair(task_id=task_id, item_id=item_id, planned=D(planned))


def done(day: date, quantity: str, task_id: uuid.UUID = T1, item_id: uuid.UUID = EXC) -> Execution:
    return Execution(task_id=task_id, item_id=item_id, day=day, quantity=D(quantity))


def control(
    *,
    tasks: Sequence[TaskWindow] | None = None,
    items: Sequence[PricedItem] | None = None,
    plans: Sequence[PlanPair] | None = None,
    executions: Sequence[Execution] = (),
    cut: date = CUT,
    kind: str = WEEK,
    calendar: WorkCalendar | None = None,
    labels: Mapping[uuid.UUID, str] | None = None,
    percent_tasks: Sequence[PercentTask] = (),
    contractual: Decimal | None = None,
    contractual_of: tuple[int, int] = (0, 0),
) -> ControlResult:
    task_list = [window()] if tasks is None else list(tasks)
    item_list = [priced()] if items is None else list(items)
    return compute(
        ControlInput(
            cut=cut,
            kind=kind,
            calendar=calendar or WorkCalendar(),
            tasks={t.task_id: t for t in task_list},
            items={i.item_id: i for i in item_list},
            item_labels=labels or {i.item_id: i.label for i in item_list},
            plans=[plan()] if plans is None else list(plans),
            executions=list(executions),
            percent_tasks=list(percent_tasks),
            contractual_direct_cost=contractual,
            contractual_budgets=contractual_of[0],
            applied_budgets=contractual_of[1],
        )
    )


class PlannedValueTests(SimpleTestCase):
    def test_spread_evenly_over_workdays(self) -> None:
        result = control()
        self.assertEqual([r.pv for r in result.periods], [D("50000.00"), D("50000.00"), 0, 0, 0])
        self.assertEqual(result.periods[-1].pv_cum, D("100000.00"))
        self.assertEqual(result.indicators.bac, D("100000.00"))
        self.assertEqual(result.indicators.at_cut.pv, D("100000.00"))

    def test_cut_in_the_middle_of_a_week(self) -> None:
        result = control(cut=date(2026, 3, 4))
        rows = result.periods
        self.assertEqual((rows[0].period.end, rows[0].pv), (date(2026, 3, 4), D("30000.00")))
        self.assertEqual((rows[1].period.start, rows[1].pv), (date(2026, 3, 5), D("20000.00")))
        self.assertIsNone(rows[1].ev)
        self.assertEqual(rows[2].pv, D("50000.00"))
        self.assertEqual(result.indicators.at_cut.pv, D("30000.00"))
        self.assertEqual(result.indicators.pv_share, D("30.0"))

    def test_holidays_are_not_workdays(self) -> None:
        holiday = CalendarException(date(2026, 3, 9), date(2026, 3, 9), working=False)
        result = control(plans=[plan(planned="90")], calendar=WorkCalendar(exceptions=[holiday]))
        self.assertEqual([r.pv for r in result.periods[:2]], [D("50000.00"), D("40000.00")])

    def test_milestone_loads_everything_on_its_finish(self) -> None:
        milestone = window(start=date(2026, 3, 26), finish=date(2026, 3, 26), milestone=True)
        result = control(tasks=[milestone], plans=[plan(planned="5")])
        self.assertEqual([r.pv for r in result.periods], [D("5000.00"), D("0.00")])
        before = control(tasks=[milestone], plans=[plan(planned="5")], cut=date(2026, 3, 25))
        self.assertEqual(before.indicators.at_cut.pv, D("0.00"))

    def test_a_window_without_workdays_loads_on_its_finish(self) -> None:
        saturday = window(start=date(2026, 3, 28), finish=date(2026, 3, 28))
        on_it = control(tasks=[saturday], plans=[plan(planned="5")], cut=date(2026, 3, 28))
        self.assertEqual(on_it.indicators.at_cut.pv, D("5000.00"))
        before = control(tasks=[saturday], plans=[plan(planned="5")], cut=date(2026, 3, 27))
        self.assertEqual(before.indicators.at_cut.pv, D("0.00"))

    def test_monthly_periods_with_the_cut_in_the_middle(self) -> None:
        # 2 de marzo a 30 de abril: 22 + 22 días laborables; hasta el 18 de marzo, 13.
        result = control(
            tasks=[window(finish=date(2026, 4, 30))], kind=MONTH, cut=date(2026, 3, 18)
        )
        rows = result.periods
        self.assertEqual(rows[0].pv, D("29545.45"))
        self.assertEqual(rows[1].period.label, "Mar-2026 (tras el corte)")
        self.assertEqual((rows[1].pv, rows[1].pv_cum), (D("20454.55"), D("50000.00")))
        self.assertIsNone(rows[1].ev_cum)
        self.assertEqual((rows[2].period.label, rows[2].pv_cum), ("Abr-2026", D("100000.00")))
        self.assertEqual(result.indicators.at_cut.pv, D("29545.45"))


class EarnedValueTests(SimpleTestCase):
    def test_capped_at_the_plan_and_overrun_reported(self) -> None:
        result = control(executions=[done(date(2026, 3, 4), "80"), done(date(2026, 3, 10), "40")])
        self.assertEqual([r.ev for r in result.periods[:2]], [D("80000.00"), D("20000.00")])
        self.assertEqual(result.periods[-1].ev_cum, D("100000.00"))
        self.assertEqual(result.indicators.at_cut.ev, D("100000.00"))
        self.assertEqual(
            result.warnings.overrun,
            (
                Overrun(
                    task="1.1 Excavación",
                    item="1.1 Excavación mecánica",
                    unit="m3",
                    planned=D("100"),
                    executed=D("120"),
                    excess=D("20"),
                    value=D("20000.00"),
                ),
            ),
        )

    def test_negative_adjustments_subtract(self) -> None:
        result = control(executions=[done(date(2026, 3, 4), "50"), done(date(2026, 3, 10), "-20")])
        self.assertEqual([r.ev for r in result.periods[:2]], [D("50000.00"), D("-20000.00")])
        self.assertEqual(result.indicators.at_cut.ev, D("30000.00"))
        self.assertEqual(result.warnings.overrun, ())

    def test_earned_value_never_goes_below_zero(self) -> None:
        result = control(executions=[done(date(2026, 3, 4), "-5"), done(date(2026, 3, 10), "10")])
        self.assertEqual([r.ev for r in result.periods[:2]], [D("0.00"), D("5000.00")])

    def test_records_after_the_cut_do_not_count(self) -> None:
        result = control(executions=[done(date(2026, 4, 1), "10")])
        self.assertEqual(result.indicators.at_cut.ev, D("0.00"))

    def test_executed_outside_the_plan(self) -> None:
        result = control(
            items=[priced(), CONCRETE],
            executions=[done(date(2026, 3, 4), "2", item_id=CONC)],
        )
        self.assertEqual(result.indicators.at_cut.ev, D("0.00"))
        self.assertEqual(
            result.warnings.outside_plan,
            (
                OutsidePlan(
                    task="1.1 Excavación",
                    item="2.1 Concreto zapatas",
                    unit="m3",
                    quantity=D("2"),
                    value=D("900000.00"),
                ),
            ),
        )

    def test_outside_the_plan_without_price_or_netting_zero(self) -> None:
        labels = {EXC: "1.1 Excavación mecánica", OTHER: "9.9", CONC: "2.1 Concreto zapatas"}
        result = control(
            items=[priced(), CONCRETE],
            labels=labels,
            executions=[
                done(date(2026, 3, 4), "3", item_id=OTHER),
                done(date(2026, 3, 4), "2", item_id=CONC),
                done(date(2026, 3, 5), "-2", item_id=CONC),
            ],
        )
        self.assertEqual(
            result.warnings.outside_plan,
            (OutsidePlan(task="1.1 Excavación", item="9.9", unit="", quantity=D("3"), value=None),),
        )


class IndicatorTests(SimpleTestCase):
    def test_sv_spi_and_status_at_the_cut(self) -> None:
        result = control(cut=date(2026, 3, 6), executions=[done(date(2026, 3, 4), "40")])
        at_cut = result.indicators.at_cut
        self.assertEqual((at_cut.pv, at_cut.ev), (D("50000.00"), D("40000.00")))
        self.assertEqual(at_cut.sv, D("-10000.00"))
        self.assertEqual(at_cut.spi, D("0.80"))
        self.assertEqual((at_cut.status, at_cut.status_label), ("red", "Atrasado"))
        self.assertEqual(result.indicators.ev_share, D("40.0"))

    def test_spi_is_empty_without_planned_value(self) -> None:
        result = control(cut=date(2026, 2, 27), executions=[done(date(2026, 2, 26), "10")])
        at_cut = result.indicators.at_cut
        self.assertEqual((at_cut.pv, at_cut.ev), (D("0.00"), D("10000.00")))
        self.assertIsNone(at_cut.spi)
        self.assertEqual((at_cut.status, at_cut.status_label), ("none", "Sin valor planeado"))
        self.assertEqual(result.periods[0].ev, D("10000.00"))

    def test_status_thresholds(self) -> None:
        self.assertEqual(status_of(D("0.95")), "green")
        self.assertEqual(status_of(D("1.20")), "green")
        self.assertEqual(status_of(D("0.94")), "amber")
        self.assertEqual(status_of(D("0.85")), "amber")
        self.assertEqual(status_of(D("0.84")), "red")
        self.assertEqual(status_of(None), "none")

    def test_spi_rounds_to_two_decimals(self) -> None:
        self.assertEqual(spi_of(D("949"), D("1000")), D("0.95"))
        self.assertIsNone(spi_of(D("1"), D("0")))

    def test_contractual_reference_is_passed_through(self) -> None:
        result = control(contractual=D("9130000.00"))
        self.assertEqual(result.indicators.contractual_direct_cost, D("9130000.00"))

    def test_contractual_reference_can_be_partial(self) -> None:
        indicators = control(contractual=D("9130000.00"), contractual_of=(1, 2)).indicators
        self.assertTrue(indicators.contractual_partial)
        self.assertEqual((indicators.contractual_budgets, indicators.applied_budgets), (1, 2))
        self.assertFalse(
            control(contractual=D("1"), contractual_of=(2, 2)).indicators.contractual_partial
        )
        self.assertFalse(control(contractual_of=(0, 2)).indicators.contractual_partial)


class BreakdownTests(SimpleTestCase):
    def setUp(self) -> None:
        self.result = control(
            tasks=[
                window(),
                window(T2, label="2.1 Columnas", location="", order=2),
                window(T3, date(2026, 4, 6), date(2026, 4, 10), label="3.1 Remates", order=3),
            ],
            items=[priced(), CONCRETE],
            plans=[plan(), plan(T2, CONC, "4"), plan(T3, EXC, "10")],
            executions=[done(date(2026, 3, 4), "100"), done(date(2026, 3, 10), "1", T2, CONC)],
        )

    def test_by_task_worst_first_and_without_spi_last(self) -> None:
        rows = self.result.by_task
        self.assertEqual([r.label for r in rows], ["2.1 Columnas", "1.1 Excavación", "3.1 Remates"])
        self.assertEqual(rows[0].task_id, T2)
        self.assertEqual((rows[0].measure.spi, rows[0].measure.status), (D("0.25"), "red"))
        self.assertEqual((rows[1].measure.spi, rows[1].measure.status), (D("1.00"), "green"))
        self.assertIsNone(rows[2].measure.spi)

    def test_by_location_and_chapter(self) -> None:
        locations = self.result.by_location
        self.assertEqual([r.label for r in locations], ["Sin ubicación", "Tramo 1"])
        self.assertEqual(locations[1].bac, D("110000.00"))
        self.assertIsNone(locations[1].task_id)
        self.assertEqual(
            [r.label for r in self.result.by_chapter], ["2 Estructura", "1 Preliminares"]
        )

    def test_bac_adds_every_priced_pair(self) -> None:
        self.assertEqual(self.result.indicators.bac, D("1910000.00"))


class WarningTests(SimpleTestCase):
    def test_task_without_baseline_uses_its_current_dates(self) -> None:
        result = control(tasks=[window(from_baseline=False)])
        self.assertEqual(result.warnings.no_baseline, ("1.1 Excavación",))
        self.assertEqual(result.indicators.bac, D("100000.00"))

    def test_task_without_dates_does_not_count(self) -> None:
        result = control(tasks=[window(start=None)])
        self.assertEqual(result.warnings.undated, ("1.1 Excavación",))
        self.assertEqual(result.indicators.bac, D("0.00"))
        self.assertEqual(result.periods, ())
        self.assertTrue(result.has_plan)

    def test_item_without_current_price(self) -> None:
        result = control(
            plans=[plan(), plan(item_id=OTHER, planned="3")],
            labels={EXC: "1.1 Excavación mecánica", OTHER: "9.9"},
            executions=[done(date(2026, 3, 4), "1", item_id=OTHER)],
        )
        self.assertEqual(
            result.warnings.unpriced,
            (UnpricedPair(task="1.1 Excavación", item="9.9", planned=D("3"), executed=D("1")),),
        )
        self.assertEqual(result.indicators.bac, D("100000.00"))

    def test_plans_of_tasks_not_in_the_schedule_are_ignored(self) -> None:
        result = control(plans=[plan(task_id=uuid.uuid4())])
        self.assertEqual(result.indicators.bac, D("0.00"))
        self.assertEqual(result.warnings.count, 0)
        self.assertFalse(result.has_plan)

    def test_percent_tasks_are_listed(self) -> None:
        tasks = [PercentTask("1.3 Aseo", D("40.00")), PercentTask("1.4 Cierre", None)]
        result = control(percent_tasks=tasks)
        self.assertEqual(result.warnings.percent_tasks, tuple(tasks))
        self.assertEqual(result.warnings.count, 2)

    def test_without_plan(self) -> None:
        result = control(plans=[])
        self.assertFalse(result.has_plan)
        self.assertEqual(result.periods, ())
        self.assertEqual(result.by_task, ())
