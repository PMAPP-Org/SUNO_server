"""Costo real en el control (costos, pieza 6a, spec sección 3). Sin BD."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from suno.control import actual_cost, earned_value
from suno.control.actual_cost import (
    CHAPTER,
    EQUIPMENT,
    INDIRECT,
    ITEM,
    LEDGER,
    SELECTION,
    TRANSPORT,
    UNASSIGNED,
    AdminLine,
    ChapterRef,
    CostInput,
    CostLine,
    RetiredCost,
    UnassignedCost,
)
from suno.control.earned_value import (
    ControlInput,
    ControlResult,
    Execution,
    PlanPair,
    PricedItem,
    TaskWindow,
    compute,
    worst_cost_first,
)
from suno.control.periods import WEEK
from suno.projects.workdays import WorkCalendar

D = Decimal
T1, T2 = uuid.uuid4(), uuid.uuid4()
EXC, CONC, STEEL, RETIRED = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
CH2, CH21, GONE = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
B1, B2 = uuid.uuid4(), uuid.uuid4()
MON, FRI = date(2026, 3, 2), date(2026, 3, 13)
CUT = date(2026, 3, 31)

ITEMS = {
    EXC: PricedItem(EXC, "1.1 Excavación", "m3", D("1000"), "1 Preliminares", budget_id=B1),
    CONC: PricedItem(CONC, "2.1.1 Concreto", "m3", D("450000"), "2 Estructura", budget_id=B2),
    STEEL: PricedItem(STEEL, "2.2 Acero", "kg", D("4200"), "2 Estructura", budget_id=B2),
}
CHAPTERS = {
    CH2: ChapterRef("2 Estructura", "2 Estructura", 0, frozenset({CONC, STEEL})),
    CH21: ChapterRef("2.1 Concretos", "2 Estructura", 1, frozenset({CONC})),
}
LABELS = {
    **{key: item.label for key, item in ITEMS.items()},
    RETIRED: "2.9 Retirado",
    GONE: "3 Capítulo retirado",
}


def line(
    day: date,
    amount: str,
    scope: str = ITEM,
    *,
    items: tuple[uuid.UUID, ...] = (),
    chapter: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
    origin: str = LEDGER,
) -> CostLine:
    return CostLine(day, D(amount), scope, items, chapter, task, origin)


def control(
    lines: Sequence[CostLine],
    *,
    cut: date = CUT,
    admin: Sequence[AdminLine] = (AdminLine(B1, "Contrato", D("0.1")),),
    pending: tuple[int, str] = (0, "0"),
) -> ControlResult:
    tasks = {
        T1: TaskWindow(T1, "1.1 Excavación", "Tramo 1", MON, FRI, True, False, 1),
        T2: TaskWindow(T2, "2.1 Columnas", "", MON, FRI, True, False, 2),
    }
    return compute(
        ControlInput(
            cut=cut,
            kind=WEEK,
            calendar=WorkCalendar(),
            tasks=tasks,
            items=ITEMS,
            item_labels=LABELS,
            plans=[
                PlanPair(T1, EXC, D("100")),
                PlanPair(T2, CONC, D("10")),
                PlanPair(T2, STEEL, D("800")),
            ],
            executions=[
                Execution(T1, EXC, date(2026, 3, 5), D("50")),
                Execution(T2, CONC, date(2026, 3, 5), D("2")),
            ],
            costs=CostInput(
                lines=list(lines),
                chapters=CHAPTERS,
                admin=list(admin),
                pending_count=pending[0],
                pending_amount=D(pending[1]),
            ),
        )
    )


#: BAC = 100.000 + 4.500.000 + 3.360.000 = 7.960.000. EV = 50.000 + 900.000 = 950.000.
BASIC = [
    line(date(2026, 3, 6), "40000", items=(EXC,), task=T1),
    line(date(2026, 3, 7), "10000", UNASSIGNED, origin=EQUIPMENT),
    line(date(2026, 3, 10), "5000", UNASSIGNED, origin=TRANSPORT),
    line(date(2026, 3, 11), "8000", INDIRECT),
]


class IndicatorTests(SimpleTestCase):
    def test_three_buckets_and_indices(self) -> None:
        cost = control(BASIC).cost
        assert cost is not None
        self.assertEqual(
            (cost.ac_assigned, cost.ac_unassigned, cost.ac_direct, cost.ac_indirect),
            (D("40000.00"), D("15000.00"), D("55000.00"), D("8000.00")),
        )
        self.assertEqual((cost.bac, cost.ev), (D("7960000.00"), D("950000.00")))
        self.assertEqual(cost.cv, D("895000.00"))
        self.assertEqual(cost.cpi, D("17.27"))
        self.assertEqual((cost.status, cost.status_label), ("green", "En presupuesto"))
        # EAC = 55.000 + (7.960.000 − 950.000) × 55.000 / 950.000
        self.assertEqual(cost.eac, D("460842.11"))
        self.assertEqual(cost.vac, D("7499157.89"))
        self.assertFalse(cost.over_budget)
        self.assertEqual(cost.tcpi, D("0.89"))
        self.assertEqual(cost.ac_share, D("0.7"))
        # Administración ganada: solo el presupuesto B1 (EV 50.000 × 10 %).
        self.assertEqual(cost.admin_earned, D("5000.00"))
        self.assertEqual(cost.admin_cpi, D("0.63"))  # 5.000 / 8.000 = 0,625
        self.assertEqual(cost.total_cpi, D("15.16"))  # 955.000 / 63.000

    def test_over_budget(self) -> None:
        cost = control([line(date(2026, 3, 6), "2000000", items=(CONC,), chapter=CH21)]).cost
        assert cost is not None
        self.assertEqual(cost.cpi, D("0.48"))  # 950.000 / 2.000.000
        self.assertEqual(cost.status_label, "Sobrecosto")
        self.assertTrue(cost.over_budget)
        self.assertEqual(cost.eac, D("16757894.74"))  # 2.000.000 + 7.010.000 × 2.000.000 / 950.000

    def test_without_cost(self) -> None:
        result = control([])
        cost = result.cost
        assert cost is not None
        self.assertTrue(cost.empty)
        self.assertIsNone(cost.cpi)
        self.assertIsNone(cost.eac)
        self.assertIsNone(cost.vac)
        self.assertEqual(cost.status_label, "Sin costo")

    def test_cost_without_earned_value(self) -> None:
        cost = control([line(date(2026, 3, 3), "1000", items=(EXC,))], cut=date(2026, 3, 4)).cost
        assert cost is not None
        self.assertEqual(cost.cpi, D("0.00"))
        self.assertIsNone(cost.eac)

    def test_exhausted_budget(self) -> None:
        result = control([line(date(2026, 3, 6), "8000000", items=(CONC,), chapter=CH21)])
        assert result.cost is not None and result.cost_warnings is not None
        self.assertIsNone(result.cost.tcpi)
        self.assertTrue(result.cost_warnings.exhausted)

    def test_admin_lines(self) -> None:
        none = control(BASIC, admin=[AdminLine(B1, "Contrato", None)]).cost
        assert none is not None
        self.assertIsNone(none.admin_earned)
        self.assertIsNone(none.admin_cpi)
        both = control(
            BASIC, admin=[AdminLine(B1, "Contrato", D("0.1")), AdminLine(B2, "Interventoría", None)]
        )
        assert both.cost is not None and both.cost_warnings is not None
        self.assertEqual(both.cost.admin_earned, D("5000.00"))
        self.assertEqual(both.cost_warnings.no_admin, ("Interventoría",))
        two = control(
            BASIC,
            admin=[AdminLine(B1, "Contrato", D("0.1")), AdminLine(B2, "Interventoría", D("0.05"))],
        ).cost
        assert two is not None
        self.assertEqual(two.admin_earned, D("50000.00"))  # 5.000 + 900.000 × 5 %


class PeriodTests(SimpleTestCase):
    def test_ac_by_period_until_the_cut(self) -> None:
        rows = control(BASIC).periods
        self.assertEqual(
            [(row.period.start, row.ac, row.ac_cum) for row in rows[:3]],
            [
                (date(2026, 3, 2), D("50000.00"), D("50000.00")),
                (date(2026, 3, 9), D("5000.00"), D("55000.00")),
                (date(2026, 3, 16), D("0.00"), D("55000.00")),
            ],
        )

    def test_a_reversal_does_not_change_past_periods(self) -> None:
        rows = control(
            [
                line(date(2026, 3, 6), "40000", items=(EXC,)),
                line(date(2026, 3, 20), "-40000", items=(EXC,)),
            ]
        ).periods
        self.assertEqual(rows[0].ac_cum, D("40000.00"))
        self.assertEqual(rows[2].ac, D("-40000.00"))
        self.assertEqual(rows[2].ac_cum, D("0.00"))

    def test_after_the_cut_does_not_count_and_before_the_start_extends(self) -> None:
        result = control(
            [line(date(2026, 4, 15), "999", items=(EXC,)), line(date(2026, 2, 20), "10", INDIRECT)]
        )
        assert result.cost is not None
        self.assertEqual(result.cost.ac_direct, D("0.00"))
        self.assertEqual(result.cost.ac_indirect, D("10.00"))
        self.assertEqual(result.periods[0].period.start, date(2026, 3, 2))  # indirecto: no extiende
        early = control([line(date(2026, 2, 20), "10", items=(EXC,))])
        self.assertEqual(early.periods[0].period.start, date(2026, 2, 16))


class BreakdownTests(SimpleTestCase):
    def test_chapter_task_and_location_rows(self) -> None:
        result = control(BASIC)
        chapter = {row.label: row for row in result.by_chapter}["1 Preliminares"]
        assert chapter.cost is not None
        self.assertEqual((chapter.cost.ac, chapter.cost.cpi), (D("40000.00"), D("1.25")))
        task = {row.label: row for row in result.by_task}["1.1 Excavación"]
        assert task.cost is not None
        self.assertEqual(task.cost.ac, D("40000.00"))
        self.assertTrue(all(row.cost is None for row in result.by_location))
        assert result.cost_warnings is not None
        self.assertEqual(result.cost_warnings.no_task, D("15000.00"))
        self.assertEqual(
            result.cost_warnings.unassigned,
            (
                UnassignedCost("Equipos propios sin ítem", D("10000.00")),
                UnassignedCost("Transporte de terceros", D("5000.00")),
            ),
        )

    def test_chapter_and_selection_groups(self) -> None:
        result = control(
            [
                line(date(2026, 3, 6), "1000000", CHAPTER, chapter=CH2),
                line(date(2026, 3, 6), "500000", items=(CONC,), chapter=CH21),
                line(date(2026, 3, 9), "100000", SELECTION, items=(CONC, STEEL), chapter=CH21),
            ]
        )
        items = {row.label: row for row in result.by_item}
        concrete, steel = items["2.1.1 Concreto"].cost, items["2.2 Acero"].cost
        assert concrete is not None and steel is not None
        self.assertEqual((concrete.ac, concrete.group), (D("500000.00"), "2 Estructura"))
        self.assertEqual((concrete.group_ev, concrete.group_ac), (D("900000.00"), D("1600000.00")))
        self.assertEqual(concrete.cpi, D("0.56"))
        self.assertEqual(concrete.cv, D("-700000.00"))
        self.assertEqual((steel.ac, steel.cpi), (D("0.00"), D("0.56")))
        chapter = {row.label: row for row in result.by_chapter}["2 Estructura"]
        assert chapter.cost is not None
        self.assertEqual(chapter.cost.ac, D("1600000.00"))
        excavation = items["1.1 Excavación"].cost
        assert excavation is not None
        self.assertEqual((excavation.group, excavation.cpi), ("", None))

    def test_a_selection_alone_is_named_after_its_chapter(self) -> None:
        result = control(
            [line(date(2026, 3, 9), "100000", SELECTION, items=(CONC, STEEL), chapter=CH21)]
        )
        concrete = {row.label: row for row in result.by_item}["2.1.1 Concreto"].cost
        assert concrete is not None
        self.assertEqual(concrete.group, "2.1 Concretos")

    def test_retired_items_count_in_their_chapter(self) -> None:
        result = control(
            [
                line(date(2026, 3, 6), "300", items=(RETIRED,), chapter=CH2),
                line(date(2026, 3, 6), "200", items=(RETIRED,), origin=EQUIPMENT),
                line(date(2026, 3, 6), "100", CHAPTER, chapter=GONE),
            ]
        )
        chapters = {row.label: row.cost.ac for row in result.by_chapter if row.cost}
        self.assertEqual(chapters["2 Estructura"], D("300.00"))
        self.assertEqual(chapters["Sin capítulo"], D("300.00"))
        assert result.cost_warnings is not None
        self.assertEqual(
            result.cost_warnings.retired,
            (
                RetiredCost("2.9 Retirado", "2 Estructura", D("300.00")),
                RetiredCost("2.9 Retirado", "Sin capítulo", D("200.00")),
                RetiredCost("3 Capítulo retirado", "Sin capítulo", D("100.00")),
            ),
        )

    def test_order_by_cpi(self) -> None:
        result = control(
            [
                line(date(2026, 3, 6), "100000", items=(EXC,)),
                line(date(2026, 3, 6), "100000", CHAPTER, chapter=CH2),
            ]
        )
        self.assertEqual(
            [row.label for row in worst_cost_first(result.by_chapter)],
            ["1 Preliminares", "2 Estructura"],  # 0,50 antes que 9,00
        )

    def test_pending_drafts_and_no_costs_permission(self) -> None:
        result = control(BASIC, pending=(2, "1500"))
        assert result.cost_warnings is not None
        self.assertEqual(
            (result.cost_warnings.pending_count, result.cost_warnings.pending_amount),
            (2, D("1500.00")),
        )
        self.assertEqual(actual_cost.NO_CHAPTER, earned_value.NO_CHAPTER)


class RetiredTests(SimpleTestCase):
    def test_cost_on_a_retired_task_is_not_lost(self) -> None:
        gone = uuid.uuid4()
        result = control(
            [
                line(date(2026, 3, 6), "40000", items=(EXC,), task=T1),
                line(date(2026, 3, 6), "7000", items=(EXC,), task=gone),
                line(date(2026, 3, 7), "3000", UNASSIGNED, origin=EQUIPMENT),
            ]
        )
        cost, warnings = result.cost, result.cost_warnings
        assert cost is not None and warnings is not None
        self.assertEqual((warnings.retired_tasks, warnings.no_task), (D("7000.00"), D("3000.00")))
        by_task = sum((row.cost.ac for row in result.by_task if row.cost), D(0))
        self.assertEqual(by_task + warnings.no_task + warnings.retired_tasks, cost.ac_direct)
        self.assertGreaterEqual(warnings.count, 2)

    def test_retired_members_of_a_selection_are_warned(self) -> None:
        unknown = uuid.uuid4()
        result = control(
            [
                line(date(2026, 3, 9), "100000", SELECTION, items=(CONC, RETIRED), chapter=CH2),
                line(date(2026, 3, 9), "300", SELECTION, items=(RETIRED, unknown), chapter=CH2),
            ]
        )
        assert result.cost_warnings is not None
        self.assertEqual(
            result.cost_warnings.retired,
            (
                RetiredCost("2.9 Retirado", "2 Estructura", D("50150.00")),
                RetiredCost("Ítem desconocido", "2 Estructura", D("150.00")),
            ),
        )
        concrete = {row.label: row for row in result.by_item}["2.1.1 Concreto"].cost
        assert concrete is not None
        self.assertEqual((concrete.group, concrete.group_ac), ("2 Estructura", D("50000.00")))
        chapter = {row.label: row for row in result.by_chapter}["2 Estructura"].cost
        assert chapter is not None
        self.assertEqual(chapter.ac, D("100300.00"))

    def test_cost_without_plan_pairs_is_still_reported(self) -> None:
        result = compute(
            ControlInput(
                cut=CUT,
                kind=WEEK,
                calendar=WorkCalendar(),
                tasks={},
                items=ITEMS,
                item_labels=LABELS,
                plans=[],
                executions=[],
                costs=CostInput(lines=BASIC, chapters=CHAPTERS),
            )
        )
        cost = result.cost
        assert cost is not None
        self.assertEqual((cost.ac_direct, cost.ac_indirect), (D("55000.00"), D("8000.00")))
        self.assertEqual((cost.bac, cost.ev), (D("0.00"), D("0.00")))
        self.assertIsNone(cost.cpi)
        self.assertIsNone(cost.eac)
        self.assertIsNone(cost.tcpi)
