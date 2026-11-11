"""Coordenadas de la curva S (costos, pieza 5). Sin BD."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from suno.control.chart import MAX_X_LABELS, build_s_curve
from suno.control.earned_value import PeriodRow
from suno.control.periods import Period

D = Decimal


def row(index: int, label: str, pv_cum: str, ev_cum: str | None) -> PeriodRow:
    start = date(2026, 3, 1) + timedelta(days=31 * index)
    return PeriodRow(
        period=Period(start, start + timedelta(days=30), label, ev_cum is not None),
        pv=D(0),
        pv_cum=D(pv_cum),
        ev=None if ev_cum is None else D(0),
        ev_cum=None if ev_cum is None else D(ev_cum),
    )


class SCurveTests(SimpleTestCase):
    def test_points_axis_and_cut(self) -> None:
        curve = build_s_curve(
            [row(0, "Mar-2026", "50", "40"), row(1, "Abr-2026", "100", None)], D(100)
        )
        assert curve is not None
        self.assertEqual(curve.axis.maximum, D(100))
        self.assertEqual(curve.pv_points, "0,100 50.00,50.00 100.00,0.00")
        self.assertEqual(curve.ev_points, "0,100 50.00,60.00")
        self.assertEqual(curve.cut_x, D("50.00"))
        self.assertEqual(curve.x_labels, ((D("25.00"), "Mar-2026"), (D("75.00"), "Abr-2026")))
        self.assertEqual((curve.pv_last, curve.ev_last), ("$100", "$40"))

    def test_without_measured_periods(self) -> None:
        curve = build_s_curve([row(0, "Mar-2026", "50", None)], D(50))
        assert curve is not None
        self.assertEqual(curve.ev_points, "")
        self.assertIsNone(curve.cut_x)
        self.assertEqual(curve.ev_last, "")

    def test_labels_are_thinned(self) -> None:
        rows = [row(i, f"P{i}", str(i), None) for i in range(20)]
        curve = build_s_curve(rows, D(20))
        assert curve is not None
        self.assertLessEqual(len(curve.x_labels), MAX_X_LABELS)
        self.assertEqual(curve.x_labels[0][1], "P0")

    def test_no_rows_no_curve(self) -> None:
        self.assertIsNone(build_s_curve([], D(0)))
