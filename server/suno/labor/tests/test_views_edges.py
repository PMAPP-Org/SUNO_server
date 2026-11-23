"""Pestaña Tareo en la web: los caminos de error que faltaban (revisión de la 6d, "Cobertura"):
crear sin permiso o sin fechas, editar y borrar asignaciones, recalcular, partir una fila
inexistente, guardar el día con errores, borrar o ver lo que no se puede, e importar tareo con
archivos inválidos."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from typing import Any
from unittest import mock
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from suno.core.models import ProjectWorker, Timesheet
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import timesheets

from .factories import TODAY, WEEK, bare_timesheet, labor_world, line_data, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

MON, TUE = WEEK[0], date(2026, 3, 3)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def texts(response: Any) -> list[str]:
    return [str(m) for m in response.context["messages"]]


@skip_without_db
class TabEdgeTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        storage = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        storage.enable()
        self.addCleanup(storage.disable)
        self.lw = labor_world()
        self.ctx = self.lw.ctx(self.lw.contributor)
        self.base = f"/plataforma/proyectos/{self.lw.project.pk}/tareo/"
        self.sheet = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        timesheets.save_lines(self.ctx, self.lw.project, self.sheet.pk,
                              [line_data(self.lw.pedro, MON)], [MON])  # fmt: skip
        self.url = f"{self.base}{self.sheet.pk}/"
        self.client.force_login(self.lw.contributor)

    def version(self) -> str:
        return str(Timesheet.objects.get(pk=self.sheet.pk).version)

    def test_create_needs_permission_and_dates(self) -> None:
        self.client.force_login(self.lw.viewer)
        self.assertEqual(self.client.post(self.base + "nueva/", {}).status_code, 403)
        self.client.force_login(self.lw.contributor)
        response = self.client.post(self.base + "nueva/", {"starts_on": "x"}, follow=True)
        self.assertIn("Escribe las fechas de la planilla.", texts(response))

    def test_edit_and_delete_an_assignment(self) -> None:
        self.client.force_login(self.lw.world.admin)  # el Colaborador edita solo lo suyo
        row = ProjectWorker.objects.get(project=self.lw.project, worker=self.lw.maria)
        url = f"{self.base}asignaciones/{row.pk}/"
        missing = self.client.post(url, {"starts_on": ""}, follow=True)
        self.assertIn("Escribe desde cuándo.", texts(missing))
        self.client.post(url, {"starts_on": "2026-01-10", "ends_on": "2026-12-31"})
        row.refresh_from_db()
        self.assertEqual((row.starts_on, row.ends_on), (date(2026, 1, 10), date(2026, 12, 31)))
        self.client.post(url, {"accion": "borrar"})
        self.assertFalse(ProjectWorker.objects.filter(pk=row.pk).exists())

    def test_recalculate_split_and_day_errors(self) -> None:
        recalculated = self.client.post(self.url + "guardar/", {
            "accion": "recalcular", "version": self.version()}, follow=True)  # fmt: skip
        self.assertIn("Resumen recalculado", texts(recalculated)[0])
        split = self.client.post(self.url + "guardar/", {
            "accion": "partir-x", "filas": "0", "version": self.version()})  # fmt: skip
        self.assertEqual(split.status_code, 200)
        day = self.client.post(
            self.url + "guardar/",
            {
                "accion": "guardar-dia",
                "dia": TUE.isoformat(),
                "semana": MON.isoformat(),
                "filas": "1",
                "version": "abc",
                "f0-trabajador": str(self.lw.pedro.pk),
                "f0-destino": "",
                "f0-tarea": "9.9",
                f"f0-d{TUE.toordinal()}": "1",
            },
            follow=True,
        )
        self.assertIn("No hay una tarea vigente con número 9.9.", " ".join(texts(day)))
        self.assertRedirects(day, f"{self.url}?semana={MON}&dia={TUE}")

    def test_delete_and_files_that_are_not_there(self) -> None:
        approved = bare_timesheet(self.lw, number=2, status="approved", start=date(2026, 3, 9),
                                  end=date(2026, 3, 15))  # fmt: skip
        refused = self.client.post(f"{self.base}{approved.pk}/borrar/", follow=True)
        self.assertRedirects(refused, f"{self.base}{approved.pk}/")
        self.assertEqual(self.client.get(self.url + "firmada/ver/").status_code, 404)
        self.assertEqual(self.client.get(f"{self.base}{uuid4()}/").status_code, 404)
        self.assertRedirects(self.client.post(self.url + "firmada/", {}), self.url)

    def test_import_upload_errors(self) -> None:
        url = self.url + "importar/"
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.post(url, {}), "Elige un archivo .xlsx.")
        with mock.patch("suno.labor.views_timesheets.MAX_BYTES", 10):
            big = self.client.post(url, {"file": SimpleUploadedFile("t.xlsx", b"x" * 11)})
        self.assertContains(big, "El archivo supera 20 MB.")
        named = self.client.post(url, {"file": SimpleUploadedFile("t.csv", b"x")})
        self.assertTrue(named.context["errors"])
        approved = bare_timesheet(self.lw, number=2, status="approved", start=date(2026, 3, 9),
                                  end=date(2026, 3, 15))  # fmt: skip
        self.client.force_login(self.lw.world.admin)
        book = xlsx("Tareo", ["Tipo de documento", "Número de documento", "Fecha", "Fracción"], [])
        locked = self.client.post(
            f"{self.base}{approved.pk}/importar/",
            {"file": SimpleUploadedFile("t.xlsx", book, content_type=XLSX)},
        )
        self.assertContains(locked, "Solo se importa a una planilla en borrador o rechazada.")
        failed = self.client.post(url + "aplicar/", {"token": "x"})
        self.assertRedirects(failed, url)
        self.client.force_login(self.lw.viewer)
        self.assertEqual(self.client.get(url).status_code, 403)
