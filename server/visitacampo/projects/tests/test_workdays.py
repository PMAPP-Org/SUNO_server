"""Días hábiles según el calendario del Project."""

from __future__ import annotations

from datetime import date

from django.test import SimpleTestCase

from visitacampo.projects.workdays import CalendarException, WorkCalendar

D = date
# Marzo de 2026: el lunes 2 es el primer día hábil; el lunes 23 es festivo.
HOLIDAY = CalendarException(start=D(2026, 3, 23), end=D(2026, 3, 23), working=False)


class WorkCalendarTests(SimpleTestCase):
    def test_monday_to_friday_by_default(self) -> None:
        cal = WorkCalendar()
        self.assertEqual(cal.count_inclusive(D(2026, 3, 2), D(2026, 3, 6)), 5)
        self.assertEqual(cal.count_inclusive(D(2026, 3, 2), D(2026, 3, 13)), 10)
        self.assertEqual(cal.count_inclusive(D(2026, 3, 7), D(2026, 3, 8)), 0)

    def test_end_before_start_counts_zero(self) -> None:
        self.assertEqual(WorkCalendar().count_inclusive(D(2026, 3, 6), D(2026, 3, 2)), 0)

    def test_holiday_is_not_counted(self) -> None:
        cal = WorkCalendar(exceptions=[HOLIDAY])
        self.assertFalse(cal.is_working(D(2026, 3, 23)))
        self.assertEqual(cal.count_inclusive(D(2026, 3, 16), D(2026, 3, 26)), 8)

    def test_working_saturday_exception_is_counted(self) -> None:
        saturday = CalendarException(start=D(2026, 3, 28), end=D(2026, 3, 28), working=True)
        cal = WorkCalendar(exceptions=[HOLIDAY, saturday])
        self.assertEqual(cal.count_inclusive(D(2026, 3, 23), D(2026, 3, 29)), 5)

    def test_later_exception_wins_on_overlap(self) -> None:
        week_off = CalendarException(start=D(2026, 3, 2), end=D(2026, 3, 6), working=False)
        back = CalendarException(start=D(2026, 3, 4), end=D(2026, 3, 4), working=True)
        cal = WorkCalendar(exceptions=[week_off, back])
        self.assertEqual(cal.count_inclusive(D(2026, 3, 2), D(2026, 3, 6)), 1)

    def test_custom_week(self) -> None:
        cal = WorkCalendar(working_weekdays=range(6))  # lunes a sábado
        self.assertEqual(cal.count_inclusive(D(2026, 3, 2), D(2026, 3, 8)), 6)

    def test_signed_gap(self) -> None:
        cal = WorkCalendar(exceptions=[HOLIDAY])
        self.assertEqual(cal.signed_gap(D(2026, 3, 13), D(2026, 3, 17)), 2)
        self.assertEqual(cal.signed_gap(D(2026, 3, 17), D(2026, 3, 13)), -2)
        self.assertEqual(cal.signed_gap(D(2026, 3, 13), D(2026, 3, 13)), 0)
        self.assertEqual(cal.signed_gap(D(2026, 3, 20), D(2026, 3, 24)), 1)

    def test_long_range_matches_day_by_day_count(self) -> None:
        cal = WorkCalendar(exceptions=[HOLIDAY])
        start, end = D(2025, 1, 1), D(2027, 12, 31)
        expected = sum(
            1
            for n in range((end - start).days + 1)
            if cal.is_working(date.fromordinal(start.toordinal() + n))
        )
        self.assertEqual(cal.count_inclusive(start, end), expected)

    def test_json_round_trip(self) -> None:
        cal = WorkCalendar(working_weekdays=[0, 1, 2, 3, 4, 5], exceptions=[HOLIDAY])
        again = WorkCalendar.from_json(cal.to_json())
        self.assertEqual(again.working_weekdays, cal.working_weekdays)
        self.assertEqual(again.exceptions, cal.exceptions)

    def test_empty_json_is_monday_to_friday(self) -> None:
        cal = WorkCalendar.from_json({})
        self.assertEqual(cal.working_weekdays, frozenset(range(5)))

    def test_rejects_too_many_total_exception_days(self) -> None:
        # Cada una cabe en el límite individual, pero juntas pasan de 20 000 días.
        many = [
            CalendarException(
                start=D(1900 + 10 * i, 1, 1), end=D(1909 + 10 * i, 12, 31), working=False
            )
            for i in range(7)
        ]
        with self.assertRaises(ValueError):
            WorkCalendar(exceptions=many)

    def test_rejects_absurdly_long_exception(self) -> None:
        huge = CalendarException(start=D(2000, 1, 1), end=D(2100, 1, 1), working=False)
        with self.assertRaises(ValueError):
            WorkCalendar(exceptions=[huge])
