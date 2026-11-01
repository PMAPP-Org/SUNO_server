"""Avance de campo: restricciones de `Visit` y el % validado de cada tarea."""

from __future__ import annotations

import unittest
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.test import TestCase

from visitacampo.core.models import Visit
from visitacampo.core.scoping import scoped_qs
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.field_progress import (
    is_regression,
    progress_visits,
    validated_pct_by_task,
)
from visitacampo.projects.importer import stage_import, summarize_changes
from visitacampo.projects.mspdi import parse_mspdi
from visitacampo.projects.schedule import Schedule, ScheduleRow, build_schedule

from .factories import make_field_world, make_visit
from .samples import v2

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class VisitTaskProgressConstraintTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()

    def test_a_visit_without_task_has_no_progress(self) -> None:
        make_visit(self.world.org, self.world.agent)  # sin tarea ni %: válida
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_visit(self.world.org, self.world.agent, pct="10")

    def test_a_linked_visit_needs_its_progress(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_visit(self.world.org, self.world.agent, task=self.world.task(3))

    def test_progress_is_between_0_and_100(self) -> None:
        self.world.visit(3, "0")
        self.world.visit(3, "100")
        for pct in ("-0.01", "100.01"):
            with self.subTest(pct=pct), self.assertRaises(IntegrityError), transaction.atomic():
                self.world.visit(3, pct)


@skip_without_db
class ValidatedPctTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        self.ctx = self.world.ctx()

    def pct(self, **kwargs: object) -> dict[UUID, Decimal]:
        return validated_pct_by_task(scoped_qs(self.ctx, Visit), **kwargs)  # type: ignore[arg-type]

    def test_latest_approved_by_capture_time_wins(self) -> None:
        w = self.world
        w.approve(w.visit(3, "30", hours=1))
        w.approve(w.visit(3, "60", hours=2))
        w.visit(3, "80", hours=3)  # sin revisar: no cuenta
        w.approve(w.visit(2, "100", hours=1))
        self.assertEqual(
            self.pct(), {w.task(3).pk: Decimal("60.00"), w.task(2).pk: Decimal("100.00")}
        )

    def test_the_latest_decision_wins(self) -> None:
        w = self.world
        w.approve(w.visit(3, "30", hours=1))
        later = w.visit(3, "60", hours=2)
        w.approve(later)
        w.reject(later)  # rechazada después de validarla: sale
        self.assertEqual(self.pct(), {w.task(3).pk: Decimal("30.00")})

    def test_deleted_visits_and_other_filters(self) -> None:
        w = self.world
        w.approve(w.visit(3, "30", hours=1))
        w.approve(w.visit(3, "90", hours=2, deleted=True))
        w.approve(w.visit(5, "10", hours=1))
        self.assertEqual(self.pct(task_ids=[w.task(3).pk]), {w.task(3).pk: Decimal("30.00")})
        self.assertEqual(self.pct(task_ids=[]), {})
        self.assertEqual(len(self.pct(project_id=w.project.pk)), 2)

    def test_previous_validated_and_regression(self) -> None:
        w = self.world
        first = w.visit(3, "60", hours=1)
        w.approve(first)
        lower = w.visit(3, "40", hours=2)
        higher = w.visit(3, "70", hours=3)
        rows = {row.visit.pk: row for row in progress_visits(self.ctx, w.task(3))}
        self.assertIsNone(rows[first.pk].previous_validated_pct)
        self.assertEqual(rows[lower.pk].previous_validated_pct, Decimal("60.00"))
        self.assertTrue(rows[lower.pk].regression)
        self.assertFalse(rows[higher.pk].regression)
        self.assertEqual(rows[first.pk].status, "approved")
        self.assertIsNone(rows[lower.pk].status)
        # Más reciente primero.
        self.assertEqual(
            [r.visit.pk for r in progress_visits(self.ctx, w.task(3))],
            [higher.pk, lower.pk, first.pk],
        )

    def test_is_regression_is_pure(self) -> None:
        self.assertTrue(is_regression(Decimal("40"), Decimal("60")))
        self.assertFalse(is_regression(Decimal("60"), Decimal("60")))
        self.assertFalse(is_regression(Decimal("40"), None))
        self.assertFalse(is_regression(None, Decimal("60")))


@skip_without_db
class ScheduleSunoPctTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()

    def row(self, schedule: Schedule, uid: int) -> ScheduleRow:
        return next(r for r in schedule.rows if r.task.uid == uid)

    def test_build_schedule_reads_validated_field_progress(self) -> None:
        w = self.world
        w.approve(w.visit(3, "50", hours=1))
        w.visit(3, "90", hours=2)  # sin validar
        row = self.row(build_schedule(w.ctx(), w.project), 3)
        assert row.indicators is not None
        self.assertEqual(row.indicators.suno.actual_pct, 50.0)

    def test_an_explicit_map_still_wins(self) -> None:
        w = self.world
        w.approve(w.visit(3, "50", hours=1))
        row = self.row(build_schedule(w.ctx(), w.project, suno_pct={}), 3)
        assert row.indicators is not None
        self.assertEqual(row.indicators.suno.actual_pct, 0.0)


@skip_without_db
class TaskPageFieldProgressTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        self.url = f"/plataforma/proyectos/{self.world.project.pk}/tareas/{self.world.task(3).pk}/"

    def test_shows_linked_visits_with_status_and_regression(self) -> None:
        w = self.world
        w.approve(w.visit(3, "60", hours=1))
        w.visit(3, "40", hours=2)
        self.client.force_login(w.supervisor)
        response = self.client.get(self.url)
        self.assertContains(response, "Avance de campo")
        self.assertContains(
            response, "% SUNO (última visita validada): <strong>60 %</strong>", html=False
        )
        self.assertContains(response, "Validada")
        self.assertContains(response, "Sin revisar")
        self.assertContains(response, "Menor que el último validado (60 %)")
        self.assertContains(response, w.agent.username)

    def test_without_visits(self) -> None:
        self.client.force_login(self.world.supervisor)
        response = self.client.get(self.url)
        self.assertContains(response, "Sin visitas vinculadas.")


@skip_without_db
class ImportPreviewFieldProgressTests(TestCase):
    def test_warns_about_retired_tasks_with_field_progress(self) -> None:
        w = make_field_world()
        w.visit(5, "10", hours=1)
        staged = stage_import(w.ctx(), w.project, file_name="v2.xml", data=v2())
        self.assertIn(
            "1 tarea(s) retirada(s) con avance de campo: 2.1 Columnas.", staged.summary["warnings"]
        )

    def test_summarize_changes_keeps_its_old_signature(self) -> None:
        summary = summarize_changes({}, parse_mspdi(v2()), set())
        self.assertNotIn("avance de campo", " ".join(summary["warnings"]))
