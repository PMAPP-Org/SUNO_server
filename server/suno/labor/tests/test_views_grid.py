"""Cuadrícula semanal y vista por día en la web (spec 3.2 "Cuadrícula semanal"; decisiones 11 y
12 del plan): guardar la semana o el día, repetir el día anterior y partir una fila sin guardar."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.conf import settings
from django.test import TestCase

from suno.core.models import TimesheetLine
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import timesheets

from .factories import TODAY, WEEK, labor_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MON, TUE = WEEK[0], date(2026, 3, 3)


@skip_without_db
class GridPageTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.sheet = timesheets.create_timesheet(self.lw.ctx(self.lw.contributor), self.lw.project,
                                                 *WEEK)  # fmt: skip
        self.url = f"/plataforma/proyectos/{self.lw.project.pk}/tareo/{self.sheet.pk}/"
        self.client.force_login(self.lw.contributor)
        page = self.client.get(self.url)
        self.rows = {row["row"].worker.split(" · ")[0]: row["index"]
                     for row in page.context["inputs"]}  # fmt: skip

    def form(self, **cells: str) -> dict[str, Any]:
        """Los campos de la cuadrícula tal como los envía el navegador (todas las celdas)."""
        data: dict[str, Any] = {"semana": "2026-03-02", "filas": "2"}
        for name, index in self.rows.items():
            worker = self.lw.pedro if name == "Pedro Pérez" else self.lw.maria
            data[f"f{index}-trabajador"] = str(worker.pk)
            data[f"f{index}-destino"] = ""
            data[f"f{index}-tarea"] = ""
            for n in range(7):
                data[f"f{index}-d{(MON.toordinal() + n)}"] = ""
        data.update(cells)
        return data

    def cell(self, worker: str, day: date) -> str:
        return f"f{self.rows[worker]}-d{day.toordinal()}"

    def test_the_grid_lists_assigned_workers_with_masked_documents(self) -> None:
        page = self.client.get(self.url)
        self.assertContains(page, "Pedro Pérez · CC ***5678")
        self.assertNotContains(page, "1012345678")
        self.assertContains(page, 'class="ta-day', html=False)  # la vista por día está
        self.assertContains(page, "suno/js/tareo.js")
        self.assertEqual(settings.DATA_UPLOAD_MAX_NUMBER_FIELDS, 4000)

    def test_save_the_week(self) -> None:
        data = self.form(
            **{
                self.cell("Pedro Pérez", MON): "1",
                self.cell("María Gómez", TUE): "0,5",
                "accion": "guardar",
            }
        )
        self.assertRedirects(self.client.post(self.url + "guardar/", data),
                             self.url + "?semana=2026-03-02")  # fmt: skip
        saved = dict(TimesheetLine.objects.values_list("worker__full_name", "fraction"))
        self.assertEqual(saved, {"Pedro Pérez": D("1.0000"), "María Gómez": D("0.5000")})
        page = self.client.get(self.url)
        self.assertContains(page, "197.600,00")  # 121.600 + 76.000

    def test_repeat_and_split_redraw_without_saving(self) -> None:
        data = self.form(**{self.cell("Pedro Pérez", MON): "1",
                            "accion": f"repetir-{TUE.isoformat()}"})  # fmt: skip
        page = self.client.post(self.url + "guardar/", data)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'name="{self.cell("Pedro Pérez", TUE)}" value="1"', html=False)
        self.assertFalse(TimesheetLine.objects.exists())
        split = self.client.post(
            self.url + "guardar/", {**data, "accion": f"partir-{self.rows['Pedro Pérez']}"}
        )
        self.assertEqual(len(split.context["inputs"]), 3)
        self.assertFalse(TimesheetLine.objects.exists())

    def test_bad_cells_keep_what_was_typed(self) -> None:
        data = self.form(**{self.cell("Pedro Pérez", MON): "abc", "accion": "guardar"})
        page = self.client.post(self.url + "guardar/", data)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "no es un número")
        self.assertContains(page, 'value="abc"', html=False)
        self.assertFalse(TimesheetLine.objects.exists())

    def test_day_view_saves_only_that_day(self) -> None:
        self.client.post(self.url + "guardar/", self.form(**{
            self.cell("Pedro Pérez", MON): "1", self.cell("Pedro Pérez", TUE): "1",
            "accion": "guardar"}))  # fmt: skip
        day = {
            "semana": "2026-03-02",
            "dia": TUE.isoformat(),
            "filas": "2",
            "accion": "guardar-dia",
        }
        for name, index in self.rows.items():
            worker = self.lw.pedro if name == "Pedro Pérez" else self.lw.maria
            day[f"f{index}-trabajador"] = str(worker.pk)
            day[f"f{index}-destino"] = ""
            day[f"f{index}-tarea"] = ""
            day[f"f{index}-d{TUE.toordinal()}"] = "0,5" if name == "Pedro Pérez" else ""
        self.client.post(self.url + "guardar/", day)
        saved = dict(TimesheetLine.objects.values_list("worked_on", "fraction"))
        self.assertEqual(saved, {MON: D("1.0000"), TUE: D("0.5000")})

    def test_viewer_cannot_save(self) -> None:
        self.client.force_login(self.lw.viewer)
        self.assertEqual(
            self.client.post(self.url + "guardar/", self.form(accion="guardar")).status_code, 403
        )
