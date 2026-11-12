"""Excel del control (costos, pieza 5): dos hojas y celdas seguras. Sin BD."""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from suno.control.earned_value import (
    ControlInput,
    Execution,
    PlanPair,
    PricedItem,
    TaskWindow,
    compute,
)
from suno.control.export import control_workbook
from suno.control.periods import WEEK
from suno.projects.workdays import WorkCalendar

D = Decimal


class ControlWorkbookTests(SimpleTestCase):
    def test_two_sheets_with_safe_cells(self) -> None:
        task = TaskWindow(
            task_id=uuid.uuid4(),
            label="=SUM(A1)",
            location="",
            start=date(2026, 3, 2),
            finish=date(2026, 3, 13),
            from_baseline=True,
            milestone=False,
        )
        item = PricedItem(
            item_id=uuid.uuid4(),
            label="1.1 Excavación",
            unit="m3",
            unit_price=D("1000"),
            chapter="1 Preliminares",
        )
        result = compute(
            ControlInput(
                cut=date(2026, 3, 31),
                kind=WEEK,
                calendar=WorkCalendar(),
                tasks={task.task_id: task},
                items={item.item_id: item},
                item_labels={item.item_id: item.label},
                plans=[PlanPair(task.task_id, item.item_id, D("100"))],
                executions=[Execution(task.task_id, item.item_id, date(2026, 3, 4), D("40"))],
            )
        )
        with zipfile.ZipFile(io.BytesIO(control_workbook(result))) as book:
            self.assertIsNone(book.testzip())
            workbook = book.read("xl/workbook.xml").decode()
            periods = book.read("xl/worksheets/sheet1.xml").decode()
            breakdown = book.read("xl/worksheets/sheet2.xml").decode()
        self.assertIn('<sheet name="Periodos"', workbook)
        self.assertIn('<sheet name="Desglose"', workbook)
        self.assertIn(">pv_acumulado<", periods)
        self.assertIn(">02-mar-2026<", periods)
        self.assertIn(">2026-03-08<", periods)
        self.assertIn("<v>50000.0</v>", periods)
        self.assertIn(">proyecto<", breakdown)
        self.assertIn(">tarea<", breakdown)
        self.assertIn(">'=SUM(A1)<", breakdown, "un nombre con fórmula sale como texto")
        self.assertIn(">Atrasado<", breakdown)
