"""Atrasos con los datos de `samples.v1()` y fecha de corte el viernes 20 de marzo."""

from __future__ import annotations

from datetime import date

from django.test import SimpleTestCase

from visitacampo.projects.mspdi import parse_mspdi
from visitacampo.projects.progress import (
    TaskIndicators,
    TaskInput,
    compute_indicators,
    rollup,
)
from visitacampo.projects.workdays import WorkCalendar

from .samples import v1

CUT = date(2026, 3, 20)


def indicators(
    tasks: list[TaskInput], suno: dict[str, float], calendar: WorkCalendar
) -> dict[str, TaskIndicators]:
    """Indicadores de las tareas que tienen línea base (las demás quedan fuera)."""
    result = compute_indicators(tasks, suno, calendar, CUT)
    return {key: value for key, value in result.items() if value is not None}


def inputs_from_v1() -> tuple[list[TaskInput], WorkCalendar]:
    schedule = parse_mspdi(v1())
    by_outline = {t.outline_number: t for t in schedule.tasks}
    tasks = []
    for t in schedule.tasks:
        parent = t.outline_number.rsplit(".", 1)[0] if "." in t.outline_number else None
        tasks.append(
            TaskInput(
                key=str(t.uid),
                parent_key=str(by_outline[parent].uid) if parent else None,
                is_milestone=t.is_milestone,
                baseline_start=t.baseline_start,
                baseline_finish=t.baseline_finish,
                finish=t.finish,
                actual_finish=t.actual_finish,
                project_pct=float(t.project_pct) if t.project_pct is not None else None,
            )
        )
    return tasks, schedule.calendar


class LeafIndicatorTests(SimpleTestCase):
    def setUp(self) -> None:
        tasks, calendar = inputs_from_v1()
        self.ind = indicators(tasks, {}, calendar)

    def test_finished_on_time_by_project_but_zero_in_suno(self) -> None:
        t = self.ind["2"]
        self.assertEqual(t.planned_pct, 100.0)
        self.assertEqual(
            (t.project.actual_pct, t.project.delay_pct, t.project.delay_days), (100.0, 0.0, 0)
        )
        # SUNO sin reportes: 0 %, fin proyectado = corte → 10 días hábiles tras el 6.
        self.assertEqual((t.suno.actual_pct, t.suno.delay_pct, t.suno.delay_days), (0.0, 100.0, 10))

    def test_late_task_projects_to_cut_date(self) -> None:
        t = self.ind["3"]
        self.assertEqual(t.planned_pct, 100.0)
        self.assertEqual((t.project.delay_pct, t.project.delay_days), (40.0, 5))

    def test_running_task_uses_its_finish_and_skips_holiday(self) -> None:
        t = self.ind["5"]
        self.assertEqual(t.planned_pct, 62.5)  # 5 de 8 días hábiles (el 23 es festivo)
        self.assertEqual(t.weight, 8)
        self.assertEqual((t.project.delay_pct, t.project.delay_days), (62.5, 2))

    def test_milestone(self) -> None:
        t = self.ind["6"]
        self.assertEqual((t.planned_pct, t.weight), (0.0, 0))
        self.assertEqual(t.project.delay_days, 2)

    def test_suno_progress_is_used_when_given(self) -> None:
        tasks, calendar = inputs_from_v1()
        ind = indicators(tasks, {"3": 100.0}, calendar)
        self.assertEqual(ind["3"].suno.delay_pct, 0.0)
        # Al 100 % sin fin real, el fin proyectado es el `finish` del Project (17).
        self.assertEqual(ind["3"].suno.delay_days, 2)


class RollupTests(SimpleTestCase):
    def setUp(self) -> None:
        tasks, calendar = inputs_from_v1()
        self.ind = indicators(tasks, {}, calendar)

    def test_stage_one(self) -> None:
        stage = self.ind["1"]
        self.assertEqual(stage.planned_pct, 100.0)
        self.assertEqual(stage.weight, 10)  # su propia línea base: 2 al 13
        self.assertEqual((stage.project.actual_pct, stage.project.delay_pct), (80.0, 20.0))
        self.assertEqual(stage.project.delay_days, 5)

    def test_stage_two_ignores_zero_weight_milestone_in_average(self) -> None:
        stage = self.ind["4"]
        self.assertEqual(stage.planned_pct, 62.5)
        self.assertEqual(stage.project.delay_days, 2)

    def test_project_total(self) -> None:
        total = rollup([self.ind["1"], self.ind["4"]])
        assert total is not None
        self.assertEqual(total.planned_pct, 83.3)
        self.assertEqual(total.project.actual_pct, 44.4)
        self.assertEqual(total.project.delay_days, 5)

    def test_rollup_of_nothing_is_none(self) -> None:
        self.assertIsNone(rollup([]))


class NoBaselineTests(SimpleTestCase):
    def test_task_without_baseline_has_no_indicators(self) -> None:
        task = TaskInput(
            key="1",
            parent_key=None,
            is_milestone=False,
            baseline_start=None,
            baseline_finish=None,
            finish=date(2026, 3, 6),
            actual_finish=None,
            project_pct=50.0,
        )
        self.assertIsNone(compute_indicators([task], {}, WorkCalendar(), CUT)["1"])
