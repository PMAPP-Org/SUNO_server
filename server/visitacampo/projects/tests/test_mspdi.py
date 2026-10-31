"""Lector MSPDI: qué se lee y qué se rechaza."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from visitacampo.projects.mspdi import MspdiError, parse_mspdi

from .samples import project_xml, task_xml, v1


class ParseV1Tests(SimpleTestCase):
    def setUp(self) -> None:
        self.schedule = parse_mspdi(v1())
        self.by_uid = {t.uid: t for t in self.schedule.tasks}

    def test_reads_project_level_data(self) -> None:
        self.assertEqual(self.schedule.status_date, date(2026, 3, 20))
        self.assertEqual(sorted(self.by_uid), [1, 2, 3, 4, 5, 6])  # sin el uid 0

    def test_reads_task_fields(self) -> None:
        t = self.by_uid[3]
        self.assertEqual(t.name, "Descapote")
        self.assertEqual(t.wbs, "1.2")
        self.assertEqual(t.outline_number, "1.2")
        self.assertEqual(t.outline_level, 2)
        self.assertEqual(t.sort_order, 3)
        self.assertEqual((t.start, t.finish), (date(2026, 3, 9), date(2026, 3, 17)))
        self.assertEqual(t.duration_days, Decimal("7"))
        self.assertEqual(t.baseline_start, date(2026, 3, 9))
        self.assertEqual(t.baseline_finish, date(2026, 3, 13))
        self.assertEqual(t.baseline_duration_days, Decimal("5"))
        self.assertEqual(t.project_pct, Decimal("60"))
        self.assertEqual(t.project_physical_pct, Decimal("50"))
        self.assertTrue(t.is_critical)
        self.assertFalse(t.is_summary)

    def test_slack_is_converted_to_days(self) -> None:
        self.assertEqual(self.by_uid[2].total_slack_days, Decimal("1"))
        self.assertEqual(self.by_uid[2].free_slack_days, Decimal("0"))

    def test_summary_milestone_and_actual_finish(self) -> None:
        self.assertTrue(self.by_uid[1].is_summary)
        self.assertTrue(self.by_uid[6].is_milestone)
        self.assertEqual(self.by_uid[2].actual_finish, date(2026, 3, 6))
        self.assertIsNone(self.by_uid[3].actual_finish)

    def test_dependencies(self) -> None:
        deps = {(d.predecessor_uid, d.successor_uid, d.type) for d in self.schedule.dependencies}
        self.assertEqual(deps, {(2, 3, "FS"), (3, 5, "FS"), (5, 6, "FS")})

    def test_resources_and_assignments_skip_unassigned(self) -> None:
        self.assertEqual(
            {(r.uid, r.name) for r in self.schedule.resources},
            {(1, "Cuadrilla A"), (2, "Topógrafo")},
        )
        self.assertEqual(set(self.schedule.assignments), {(2, 2), (3, 1), (5, 1)})

    def test_calendar(self) -> None:
        cal = self.schedule.calendar
        self.assertEqual(cal.working_weekdays, frozenset(range(5)))
        self.assertFalse(cal.is_working(date(2026, 3, 23)))
        self.assertTrue(cal.is_working(date(2026, 3, 24)))


class ParseVariantsTests(SimpleTestCase):
    def test_without_namespace(self) -> None:
        data = project_xml([task_xml(1, "A", "1")], namespace=False)
        self.assertEqual([t.uid for t in parse_mspdi(data).tasks], [1])

    def test_without_baseline_calendar_or_status_date(self) -> None:
        data = project_xml([task_xml(1, "A", "1")], with_calendar=False, status_date=None)
        schedule = parse_mspdi(data)
        self.assertIsNone(schedule.tasks[0].baseline_start)
        self.assertIsNone(schedule.status_date)
        self.assertEqual(schedule.calendar.working_weekdays, frozenset(range(5)))

    def test_null_rows_are_skipped(self) -> None:
        data = project_xml([task_xml(1, "A", "1"), "<Task><UID>9</UID><IsNull>1</IsNull></Task>"])
        self.assertEqual([t.uid for t in parse_mspdi(data).tasks], [1])

    def test_dependency_to_unknown_task_is_dropped(self) -> None:
        data = project_xml([task_xml(1, "A", "1", preds=((99, 1),))])
        self.assertEqual(parse_mspdi(data).dependencies, ())


class RejectionTests(SimpleTestCase):
    def assertRejected(self, data: bytes, fragment: str) -> None:
        with self.assertRaises(MspdiError) as caught:
            parse_mspdi(data)
        self.assertIn(fragment, str(caught.exception))

    def test_not_xml(self) -> None:
        self.assertRejected(b"esto no es xml", "no es un XML")

    def test_truncated_xml(self) -> None:
        self.assertRejected(v1()[:200], "no es un XML")

    def test_other_root(self) -> None:
        self.assertRejected(b"<html><body/></html>", "MSPDI")

    def test_task_without_name(self) -> None:
        bad = (
            "<Task><UID>5</UID><OutlineNumber>1</OutlineNumber>"
            "<OutlineLevel>1</OutlineLevel></Task>"
        )
        self.assertRejected(project_xml([bad]), "La tarea 5 no tiene nombre")

    def test_task_without_uid(self) -> None:
        bad = "<Task><Name>X</Name><OutlineLevel>1</OutlineLevel></Task>"
        self.assertRejected(project_xml([bad]), "sin UniqueID")

    def test_duplicated_uid(self) -> None:
        data = project_xml([task_xml(1, "A", "1"), task_xml(1, "B", "2")])
        self.assertRejected(data, "UniqueID repetido: 1")

    def test_missing_outline_number(self) -> None:
        bad = "<Task><UID>5</UID><Name>X</Name><OutlineLevel>1</OutlineLevel></Task>"
        self.assertRejected(project_xml([bad]), "OutlineNumber")

    def test_bad_date(self) -> None:
        bad = task_xml(1, "A", "1").replace("</Task>", "<Start>ayer</Start></Task>")
        self.assertRejected(project_xml([bad]), "Fecha inválida en la tarea 1")

    def test_too_big(self) -> None:
        with self.assertRaises(MspdiError) as caught:
            parse_mspdi(v1(), max_bytes=100)
        self.assertIn("supera", str(caught.exception))

    def test_entity_expansion_is_refused(self) -> None:
        bomb = (
            b'<?xml version="1.0"?><!DOCTYPE p [<!ENTITY a "aaaaaaaaaa">'
            b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><Project>&b;</Project>'
        )
        self.assertRejected(bomb, "no es un XML")
