"""Periodos del control (costos, pieza 5). Sin BD."""

from __future__ import annotations

from datetime import date

from django.test import SimpleTestCase

from suno.control.periods import (
    MONTH,
    WEEK,
    Period,
    PeriodRangeError,
    build_periods,
    next_start,
    period_label,
    period_start,
)


class PeriodHelpersTests(SimpleTestCase):
    def test_period_start(self) -> None:
        self.assertEqual(period_start(date(2026, 3, 4), WEEK), date(2026, 3, 2))  # lunes
        self.assertEqual(period_start(date(2026, 3, 8), WEEK), date(2026, 3, 2))  # domingo
        self.assertEqual(period_start(date(2026, 3, 4), MONTH), date(2026, 3, 1))

    def test_next_start(self) -> None:
        self.assertEqual(next_start(date(2026, 3, 2), WEEK), date(2026, 3, 9))
        self.assertEqual(next_start(date(2026, 11, 1), MONTH), date(2026, 12, 1))
        self.assertEqual(next_start(date(2026, 12, 1), MONTH), date(2027, 1, 1))

    def test_labels(self) -> None:
        self.assertEqual(period_label(date(2026, 3, 9), WEEK), "09-mar-2026")
        self.assertEqual(period_label(date(2026, 3, 1), MONTH), "Mar-2026")


class BuildPeriodsTests(SimpleTestCase):
    def test_weeks_until_the_cut(self) -> None:
        periods = build_periods(date(2026, 3, 2), date(2026, 3, 13), date(2026, 3, 31), WEEK)
        self.assertEqual(
            [(p.start, p.end, p.measured) for p in periods],
            [
                (date(2026, 3, 2), date(2026, 3, 8), True),
                (date(2026, 3, 9), date(2026, 3, 15), True),
                (date(2026, 3, 16), date(2026, 3, 22), True),
                (date(2026, 3, 23), date(2026, 3, 29), True),
                (date(2026, 3, 30), date(2026, 3, 31), True),  # termina en el corte
            ],
        )

    def test_the_cut_period_is_split_when_the_series_goes_on(self) -> None:
        periods = build_periods(date(2026, 3, 2), date(2026, 4, 30), date(2026, 3, 18), MONTH)
        self.assertEqual(
            periods,
            [
                Period(date(2026, 3, 1), date(2026, 3, 18), "Mar-2026", True),
                Period(date(2026, 3, 19), date(2026, 3, 31), "Mar-2026 (tras el corte)", False),
                Period(date(2026, 4, 1), date(2026, 4, 30), "Abr-2026", False),
            ],
        )

    def test_a_cut_on_the_period_end_does_not_split(self) -> None:
        periods = build_periods(date(2026, 3, 2), date(2026, 4, 30), date(2026, 3, 31), MONTH)
        self.assertEqual(
            [(p.start, p.end, p.measured) for p in periods],
            [
                (date(2026, 3, 1), date(2026, 3, 31), True),
                (date(2026, 4, 1), date(2026, 4, 30), False),
            ],
        )

    def test_a_cut_before_the_first_start(self) -> None:
        periods = build_periods(date(2026, 3, 2), date(2026, 3, 13), date(2026, 2, 20), WEEK)
        self.assertEqual(
            periods[0], Period(date(2026, 2, 16), date(2026, 2, 20), "16-feb-2026", True)
        )
        self.assertEqual((periods[1].start, periods[1].end), (date(2026, 2, 21), date(2026, 2, 22)))
        self.assertFalse(periods[1].measured)
        self.assertEqual(len(periods), 5)
        self.assertEqual(periods[-1].end, date(2026, 3, 15))

    def test_too_many_periods(self) -> None:
        with self.assertRaises(PeriodRangeError):
            build_periods(date(1900, 1, 1), date(2100, 1, 1), date(2026, 1, 1), WEEK)

    def test_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            build_periods(date(2026, 3, 2), date(2026, 3, 13), date(2026, 3, 31), "dia")
