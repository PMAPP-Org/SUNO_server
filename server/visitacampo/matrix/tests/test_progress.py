from __future__ import annotations

import uuid
from decimal import Decimal

from django.test import SimpleTestCase

from visitacampo.matrix.progress import PlanLine, task_progress

T1, T2 = uuid.uuid4(), uuid.uuid4()
A, B = uuid.uuid4(), uuid.uuid4()


def line(
    task: uuid.UUID, item: uuid.UUID, planned: str, executed: str, price: str | None
) -> PlanLine:
    return PlanLine(
        task_id=task,
        item_id=item,
        planned=Decimal(planned),
        executed=Decimal(executed),
        unit_price=None if price is None else Decimal(price),
    )


class TaskProgressTests(SimpleTestCase):
    def test_weighted_by_cost(self) -> None:
        # Concreto 10 m3 a 450.000 (4,5 M) y señales 100 m2 a 5.000 (0,5 M).
        result = task_progress([line(T1, A, "10", "5", "450000"), line(T1, B, "100", "0", "5000")])
        self.assertEqual(result[T1].pct, Decimal("45.00"))
        self.assertEqual(result[T1].value, Decimal("5000000.00"))

    def test_capped_at_100_and_negative_executed_counts_as_zero(self) -> None:
        result = task_progress([line(T1, A, "10", "15", "1"), line(T2, A, "10", "-3", "1")])
        self.assertEqual(result[T1].pct, Decimal("100.00"))
        self.assertEqual(result[T2].pct, Decimal("0.00"))

    def test_without_prices_uses_simple_average(self) -> None:
        result = task_progress([line(T1, A, "10", "5", None), line(T1, B, "4", "4", "0")])
        self.assertEqual(result[T1].pct, Decimal("75.00"))
        self.assertEqual(result[T1].value, Decimal("0.00"))

    def test_overrun_on_one_item_offsets_the_other_only_in_value(self) -> None:
        result = task_progress([line(T1, A, "10", "15", "1"), line(T1, B, "10", "0", "1")])
        self.assertEqual(result[T1].pct, Decimal("75.00"))

    def test_unpriced_item_contributes_nothing_when_another_has_price(self) -> None:
        result = task_progress([line(T1, A, "10", "10", "5"), line(T1, B, "10", "0", None)])
        self.assertEqual(result[T1].pct, Decimal("100.00"))
        self.assertEqual(result[T1].value, Decimal("50.00"))

    def test_zero_planned_lines_do_not_measure_the_task(self) -> None:
        result = task_progress([line(T1, A, "0", "5", "100")])
        self.assertNotIn(T1, result)

    def test_rounding(self) -> None:
        result = task_progress([line(T1, A, "3", "1", "1")])
        self.assertEqual(result[T1].pct, Decimal("33.33"))
